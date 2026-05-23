"""
Gestión de usuarios del tenant (equipo):
- Listar usuarios de la empresa
- Crear usuario (admin asigna password temporal)
- Editar rol
- Activar/desactivar
"""
from __future__ import annotations

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from models import db
import json

from models.user import User, Role, UserRole, RolePermission
from services.tenant_context import current_tenant
from services.permissions import (
    ALL_PERMISSIONS,
    DEFAULT_ROLE_PERMISSIONS,
    PERMISSION_GROUPS,
    permission_required,
    role_permissions_for,
    tenant_required,
)
from services.plan_limits import check_can_add_user, PlanLimitError

usuarios_bp = Blueprint("usuarios", __name__, url_prefix="/app/usuarios")
TEAM_ROLE_CODES = ["admin", "cajero", "vendedor", "contador", "viewer"]


def _get_or_404(user_id: int) -> User:
    tenant = current_tenant()
    user = User.query.filter_by(id=user_id, tenant_id=tenant.id).first()
    if user is None:
        abort(404)
    return user


@usuarios_bp.route("/")
@login_required
@tenant_required
@permission_required("users.manage")
def list():
    tenant = current_tenant()
    users = User.query.filter_by(tenant_id=tenant.id).order_by(User.created_at.asc()).all()
    return render_template("usuarios/list.html", users=users)


@usuarios_bp.route("/roles", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("users.manage")
def roles():
    tenant = current_tenant()
    roles = Role.query.filter(Role.code.in_(TEAM_ROLE_CODES)).order_by(Role.name.asc()).all()

    if request.method == "POST":
        valid = set(ALL_PERMISSIONS)
        for role in roles:
            selected = [
                p for p in request.form.getlist(f"permissions_{role.id}")
                if p in valid
            ]
            custom = RolePermission.query.filter_by(tenant_id=tenant.id, role_id=role.id).first()
            if custom is None:
                custom = RolePermission(tenant_id=tenant.id, role_id=role.id)
                db.session.add(custom)
            custom.permissions = json.dumps(selected)
        db.session.commit()
        flash("Permisos de roles actualizados.", "success")
        return redirect(url_for("usuarios.roles"))

    role_permissions = {
        role.id: role_permissions_for(tenant.id, role)
        for role in roles
    }
    defaults = {
        role.id: set(DEFAULT_ROLE_PERMISSIONS.get(role.code, []))
        for role in roles
    }
    return render_template(
        "usuarios/roles.html",
        roles=roles,
        permission_groups=PERMISSION_GROUPS,
        role_permissions=role_permissions,
        defaults=defaults,
    )


@usuarios_bp.route("/new", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("users.manage")
def new():
    tenant = current_tenant()

    try:
        check_can_add_user(tenant)
    except PlanLimitError as e:
        flash(str(e), "warning")
        return redirect(url_for("billing.index"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        full_name = (request.form.get("full_name") or "").strip()
        password = request.form.get("password") or ""
        role_code = request.form.get("role_code") or "vendedor"

        # Validaciones
        if not email or not password:
            flash("Email y contraseña son obligatorios.", "danger")
            return redirect(url_for("usuarios.new"))

        if User.query.filter_by(tenant_id=tenant.id, email=email).first():
            flash(f"Ya existe un usuario con el correo {email}.", "danger")
            return redirect(url_for("usuarios.new"))

        if len(password) < 8:
            flash("La contraseña debe tener al menos 8 caracteres.", "danger")
            return redirect(url_for("usuarios.new"))

        # Crear usuario
        user = User(
            tenant_id=tenant.id,
            email=email,
            full_name=full_name or None,
            is_active=True,
            email_verified=True,  # Como lo creó un admin, asumimos verificado
        )
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        # Asignar rol
        role = Role.query.filter_by(code=role_code).first()
        if role:
            db.session.add(UserRole(user_id=user.id, role_id=role.id, tenant_id=tenant.id))

        db.session.commit()
        flash(f"Usuario {email} creado. Comparte la contraseña con la persona.", "success")
        return redirect(url_for("usuarios.list"))

    roles = Role.query.filter(Role.code.in_(TEAM_ROLE_CODES)).order_by(Role.name.asc()).all()
    return render_template("usuarios/form.html", user=None, roles=roles)


@usuarios_bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("users.manage")
def edit(user_id):
    user = _get_or_404(user_id)
    tenant = current_tenant()

    if request.method == "POST":
        user.full_name = (request.form.get("full_name") or "").strip() or None
        user.is_active = bool(request.form.get("is_active"))

        # Cambiar password si se proporcionó
        new_password = request.form.get("password")
        if new_password:
            if len(new_password) < 8:
                flash("La contraseña debe tener al menos 8 caracteres.", "danger")
                return redirect(url_for("usuarios.edit", user_id=user_id))
            user.set_password(new_password)

        # Actualizar rol — no permitido para el owner
        if not user.is_owner:
            role_code = request.form.get("role_code") or "vendedor"
            # Eliminar roles previos
            UserRole.query.filter_by(user_id=user.id).delete()
            role = Role.query.filter_by(code=role_code).first()
            if role:
                db.session.add(UserRole(user_id=user.id, role_id=role.id, tenant_id=tenant.id))

        db.session.commit()
        flash(f"Usuario {user.email} actualizado.", "success")
        return redirect(url_for("usuarios.list"))

    roles = Role.query.filter(Role.code.in_(TEAM_ROLE_CODES)).order_by(Role.name.asc()).all()
    current_role = user.roles[0].code if user.roles else "vendedor"
    return render_template("usuarios/form.html", user=user, roles=roles, current_role=current_role)


@usuarios_bp.route("/<int:user_id>/toggle", methods=["POST"])
@login_required
@tenant_required
@permission_required("users.manage")
def toggle(user_id):
    user = _get_or_404(user_id)

    if user.is_owner:
        flash("No puedes desactivar al propietario del tenant.", "danger")
        return redirect(url_for("usuarios.list"))

    if user.id == current_user.id:
        flash("No puedes desactivarte a ti mismo.", "danger")
        return redirect(url_for("usuarios.list"))

    user.is_active = not user.is_active
    db.session.commit()
    estado = "activado" if user.is_active else "desactivado"
    flash(f"Usuario {user.email} {estado}.", "success")
    return redirect(url_for("usuarios.list"))
