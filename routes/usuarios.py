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
from models.locations import Branch
from services.tenant_context import current_tenant
from services.permissions import (
    ALL_PERMISSIONS,
    DEFAULT_ROLE_PERMISSIONS,
    PERMISSION_GROUPS,
    permission_required,
    role_permissions_for,
    tenant_required,
)
from services.plan_limits import (
    PlanLimitError,
    allowed_role_codes,
    check_can_add_user,
    current_plan,
)

usuarios_bp = Blueprint("usuarios", __name__, url_prefix="/app/usuarios")
TEAM_ROLE_CODES = ["admin", "cajero", "vendedor", "contador", "viewer"]
BRANCH_REQUIRED_ROLE_CODES = {"cajero", "vendedor"}


def _tenant_plan_name(tenant) -> str:
    plan = current_plan(tenant)
    return plan.name if plan else "Free"


def _get_or_404(user_id: int) -> User:
    tenant = current_tenant()
    user = User.query.filter_by(id=user_id, tenant_id=tenant.id).first()
    if user is None:
        abort(404)
    return user


def _branch_id_from_form(tenant):
    branch_id = request.form.get("branch_id", type=int)
    if not branch_id:
        return None
    branch = Branch.query.filter_by(id=branch_id, tenant_id=tenant.id, is_active=True).first()
    return branch.id if branch else None


def _active_branches(tenant):
    return Branch.query.filter_by(tenant_id=tenant.id, is_active=True).order_by(Branch.name.asc()).all()


@usuarios_bp.route("/")
@login_required
@tenant_required
@permission_required("users.manage")
def list():
    tenant = current_tenant()
    users = User.query.filter_by(tenant_id=tenant.id).order_by(User.created_at.asc()).all()
    return render_template("usuarios/list.html", users=users, plan_name=_tenant_plan_name(tenant))


@usuarios_bp.route("/roles", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("users.manage")
def roles():
    tenant = current_tenant()
    allowed_codes = allowed_role_codes(tenant)
    roles = Role.query.filter(Role.code.in_(allowed_codes)).order_by(Role.name.asc()).all()

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
        plan_name=_tenant_plan_name(tenant),
        locked_roles=[code for code in TEAM_ROLE_CODES if code not in allowed_codes],
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
        allowed_codes = allowed_role_codes(tenant)
        role_code = request.form.get("role_code") or allowed_codes[0]
        if role_code not in allowed_codes:
            flash("Ese rol no está incluido en el plan actual.", "warning")
            return redirect(url_for("usuarios.new"))
        branch_id = _branch_id_from_form(tenant)
        if role_code in BRANCH_REQUIRED_ROLE_CODES and not branch_id:
            flash("Asigna una sede para este rol. Así se controla desde dónde puede facturar.", "warning")
            return redirect(url_for("usuarios.new"))

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
            branch_id=branch_id,
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

    roles = Role.query.filter(Role.code.in_(allowed_role_codes(tenant))).order_by(Role.name.asc()).all()
    return render_template(
        "usuarios/form.html",
        user=None,
        roles=roles,
        branches=_active_branches(tenant),
        plan_name=_tenant_plan_name(tenant),
    )


@usuarios_bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("users.manage")
def edit(user_id):
    user = _get_or_404(user_id)
    tenant = current_tenant()

    if request.method == "POST":
        user.full_name = (request.form.get("full_name") or "").strip() or None
        user.branch_id = _branch_id_from_form(tenant)
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
            allowed_codes = allowed_role_codes(tenant)
            role_code = request.form.get("role_code") or allowed_codes[0]
            if role_code not in allowed_codes:
                flash("Ese rol no está incluido en el plan actual.", "warning")
                return redirect(url_for("usuarios.edit", user_id=user_id))
            if role_code in BRANCH_REQUIRED_ROLE_CODES and not user.branch_id:
                flash("Asigna una sede para este rol. Así se controla desde dónde puede facturar.", "warning")
                return redirect(url_for("usuarios.edit", user_id=user_id))
            # Eliminar roles previos
            UserRole.query.filter_by(user_id=user.id).delete()
            role = Role.query.filter_by(code=role_code).first()
            if role:
                db.session.add(UserRole(user_id=user.id, role_id=role.id, tenant_id=tenant.id))

        db.session.commit()
        flash(f"Usuario {user.email} actualizado.", "success")
        return redirect(url_for("usuarios.list"))

    roles = Role.query.filter(Role.code.in_(allowed_role_codes(tenant))).order_by(Role.name.asc()).all()
    current_role = user.roles[0].code if user.roles else "vendedor"
    return render_template(
        "usuarios/form.html",
        user=user,
        roles=roles,
        current_role=current_role,
        branches=_active_branches(tenant),
        plan_name=_tenant_plan_name(tenant),
    )


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
