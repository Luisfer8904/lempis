"""
Panel SuperAdmin de Lempis — vista cross-tenant para administrar la plataforma.

Solo accesible por usuarios con is_superadmin=True.

Páginas:
- /admin/                      → Dashboard global (KPIs de la plataforma)
- /admin/empresas              → Lista todas las empresas (tenants)
- /admin/empresas/<id>         → Detalle empresa: usuarios, facturas, suscripción
- /admin/empresas/<id>/toggle  → Suspender/reactivar empresa
- /admin/usuarios              → Lista usuarios cross-tenant
- /admin/suscripciones         → Suscripciones y estado de pagos
"""
from __future__ import annotations

from datetime import datetime, timedelta
from sqlalchemy import func, or_

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required

from models import db
from models.tenant import Tenant, Plan, Subscription
from models.user import User, Role, UserRole
from models.invoice import Invoice
from models.catalog import Customer, Product
from services.permissions import superadmin_required

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------------- DASHBOARD GLOBAL ----------------

@admin_bp.route("/")
@login_required
@superadmin_required
def index():
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # KPIs de la plataforma
    total_tenants = Tenant.query.count()
    active_tenants = Tenant.query.filter_by(is_active=True).count()
    total_users = User.query.count()
    total_invoices = Invoice.query.count()
    invoices_this_month = Invoice.query.filter(
        Invoice.issue_date >= month_start,
        Invoice.status != "draft",
    ).count()

    # Tenants nuevos en los últimos 30 días
    thirty_days_ago = now - timedelta(days=30)
    new_tenants = Tenant.query.filter(Tenant.created_at >= thirty_days_ago).count()

    # Distribución por plan
    plan_distribution = (
        db.session.query(
            Plan.name.label("plan_name"),
            Plan.code.label("plan_code"),
            func.count(Subscription.id).label("count"),
        )
        .outerjoin(Subscription, Subscription.plan_id == Plan.id)
        .group_by(Plan.id, Plan.name, Plan.code)
        .order_by(Plan.price_monthly)
        .all()
    )

    # MRR estimado (suma de price_monthly de suscripciones activas)
    mrr = (
        db.session.query(func.coalesce(func.sum(Plan.price_monthly), 0))
        .select_from(Subscription)
        .join(Plan, Plan.id == Subscription.plan_id)
        .filter(Subscription.status.in_(["trialing", "active"]))
        .scalar()
    )

    # Últimos tenants
    recent_tenants = (
        Tenant.query.order_by(Tenant.created_at.desc()).limit(10).all()
    )

    return render_template(
        "admin/index.html",
        total_tenants=total_tenants,
        active_tenants=active_tenants,
        total_users=total_users,
        total_invoices=total_invoices,
        invoices_this_month=invoices_this_month,
        new_tenants=new_tenants,
        plan_distribution=plan_distribution,
        mrr=float(mrr or 0),
        recent_tenants=recent_tenants,
    )


# ---------------- EMPRESAS (TENANTS) ----------------

@admin_bp.route("/empresas")
@login_required
@superadmin_required
def empresas():
    q = (request.args.get("q") or "").strip()
    query = Tenant.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Tenant.name.ilike(like),
            Tenant.slug.ilike(like),
            Tenant.tax_id.ilike(like),
            Tenant.email.ilike(like),
        ))
    tenants = query.order_by(Tenant.created_at.desc()).all()

    # Adjuntar conteos por tenant (eficiente: un solo query)
    counts = {}
    for t in tenants:
        counts[t.id] = {
            "users": User.query.filter_by(tenant_id=t.id).count(),
            "invoices": Invoice.query.filter_by(tenant_id=t.id).count(),
            "customers": Customer.query.filter_by(tenant_id=t.id).count(),
        }

    return render_template("admin/empresas.html", tenants=tenants, counts=counts, q=q)


@admin_bp.route("/empresas/<int:tenant_id>")
@login_required
@superadmin_required
def empresa_detail(tenant_id):
    tenant = db.session.get(Tenant, tenant_id)
    if tenant is None:
        abort(404)

    users = User.query.filter_by(tenant_id=tenant.id).order_by(User.created_at.asc()).all()
    last_invoices = (
        Invoice.query.filter_by(tenant_id=tenant.id)
        .order_by(Invoice.issue_date.desc())
        .limit(20)
        .all()
    )

    # Revenue total del tenant
    revenue_total = (
        db.session.query(func.coalesce(func.sum(Invoice.total), 0))
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(["issued", "paid", "partially_paid"]),
        )
        .scalar()
    )

    # Indicadores de actividad
    last_login_overall = None
    for u in users:
        if u.last_login_at and (last_login_overall is None or u.last_login_at > last_login_overall):
            last_login_overall = u.last_login_at

    last_invoice = last_invoices[0] if last_invoices else None

    stats = {
        "users": len(users),
        "invoices": Invoice.query.filter_by(tenant_id=tenant.id).count(),
        "customers": Customer.query.filter_by(tenant_id=tenant.id).count(),
        "products": Product.query.filter_by(tenant_id=tenant.id).count(),
        "revenue": float(revenue_total or 0),
        "last_login_overall": last_login_overall,
        "last_invoice_date": last_invoice.issue_date if last_invoice else None,
    }

    return render_template(
        "admin/empresa_detail.html",
        tenant=tenant, users=users,
        last_invoices=last_invoices, stats=stats,
    )


@admin_bp.route("/empresas/<int:tenant_id>/toggle", methods=["POST"])
@login_required
@superadmin_required
def empresa_toggle(tenant_id):
    tenant = db.session.get(Tenant, tenant_id)
    if tenant is None:
        abort(404)
    tenant.is_active = not tenant.is_active
    db.session.commit()
    estado = "reactivada" if tenant.is_active else "suspendida"
    flash(f"Empresa '{tenant.name}' {estado}.", "success")
    return redirect(url_for("admin.empresa_detail", tenant_id=tenant.id))


@admin_bp.route("/empresas/<int:tenant_id>/delete", methods=["POST"])
@login_required
@superadmin_required
def empresa_delete(tenant_id):
    """
    Borra una empresa Y todos sus datos en cascada
    (usuarios, clientes, productos, facturas, suscripción).
    Acción IRREVERSIBLE — requiere escribir el slug exacto para confirmar.
    """
    tenant = db.session.get(Tenant, tenant_id)
    if tenant is None:
        abort(404)

    # Protección: no permitir borrar el tenant del equipo Lempis
    if tenant.slug == "lempis-admin":
        flash("No puedes borrar el tenant del equipo Lempis.", "danger")
        return redirect(url_for("admin.empresa_detail", tenant_id=tenant.id))

    confirmation = (request.form.get("confirm_slug") or "").strip()
    if confirmation != tenant.slug:
        flash(
            f"Confirmación incorrecta. Para borrar debes escribir exactamente '{tenant.slug}'.",
            "danger",
        )
        return redirect(url_for("admin.empresa_detail", tenant_id=tenant.id))

    name = tenant.name
    db.session.delete(tenant)
    db.session.commit()
    flash(f"Empresa '{name}' eliminada permanentemente junto con todos sus datos.", "warning")
    return redirect(url_for("admin.empresas"))


# ---------------- USUARIOS CROSS-TENANT ----------------

@admin_bp.route("/usuarios")
@login_required
@superadmin_required
def usuarios():
    q = (request.args.get("q") or "").strip()
    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            User.email.ilike(like),
            User.full_name.ilike(like),
        ))
    users = query.order_by(User.created_at.desc()).limit(200).all()

    # Lista de tenants activos para el modal de mover
    all_tenants = Tenant.query.filter_by(is_active=True).order_by(Tenant.name).all()

    # Calcular días desde último login para cada user
    now = datetime.utcnow()
    user_inactivity = {}
    for u in users:
        if u.last_login_at:
            user_inactivity[u.id] = (now - u.last_login_at).days
        else:
            user_inactivity[u.id] = None  # nunca

    return render_template(
        "admin/usuarios.html",
        users=users, q=q, all_tenants=all_tenants, user_inactivity=user_inactivity,
    )


@admin_bp.route("/usuarios/<int:user_id>/toggle-superadmin", methods=["POST"])
@login_required
@superadmin_required
def toggle_superadmin(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    user.is_superadmin = not user.is_superadmin
    db.session.commit()
    estado = "promovido a" if user.is_superadmin else "removido de"
    flash(f"Usuario {user.email} {estado} SuperAdmin.", "success")
    return redirect(url_for("admin.usuarios"))


@admin_bp.route("/usuarios/<int:user_id>/delete", methods=["POST"])
@login_required
@superadmin_required
def usuario_delete(user_id):
    """Borra un usuario. Protecciones: no borrar a uno mismo ni a owners."""
    from flask_login import current_user
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    if user.id == current_user.id:
        flash("No puedes borrarte a ti mismo.", "danger")
        return redirect(url_for("admin.usuarios"))
    if user.is_owner:
        flash(
            f"No puedes borrar a {user.email} porque es el propietario de su empresa. "
            "Primero traspasa la propiedad o borra la empresa completa.",
            "danger",
        )
        return redirect(url_for("admin.usuarios"))

    email = user.email
    db.session.delete(user)
    db.session.commit()
    flash(f"Usuario {email} eliminado.", "warning")
    return redirect(url_for("admin.usuarios"))


@admin_bp.route("/usuarios/<int:user_id>/reset-password", methods=["GET", "POST"])
@login_required
@superadmin_required
def usuario_reset_password(user_id):
    """Restablece la clave de un usuario cross-tenant desde el panel SuperAdmin."""
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if request.method == "GET":
        return render_template("admin/reset_password.html", user=user)

    password = request.form.get("password") or ""
    password_confirm = request.form.get("password_confirm") or ""
    if len(password) < 8:
        flash("La nueva contraseña debe tener al menos 8 caracteres.", "danger")
        return render_template("admin/reset_password.html", user=user)
    if password != password_confirm:
        flash("La confirmación de contraseña no coincide.", "danger")
        return render_template("admin/reset_password.html", user=user)

    user.set_password(password)
    user.reset_token = None
    user.reset_token_expires = None
    db.session.commit()
    flash(f"Contraseña restablecida para {user.email}.", "success")
    return redirect(url_for("admin.usuarios"))


@admin_bp.route("/usuarios/<int:user_id>/move", methods=["POST"])
@login_required
@superadmin_required
def usuario_move(user_id):
    """Reasigna un usuario a otra empresa (tenant). Limpia sus UserRole."""
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user.is_owner:
        flash(
            f"No puedes mover a {user.email}: es propietario de su empresa.",
            "danger",
        )
        return redirect(url_for("admin.usuarios"))

    target_tenant_id = request.form.get("target_tenant_id", type=int)
    if not target_tenant_id:
        flash("Selecciona una empresa destino.", "warning")
        return redirect(url_for("admin.usuarios"))

    target = db.session.get(Tenant, target_tenant_id)
    if target is None:
        abort(404)

    old_tenant_name = user.tenant.name if user.tenant else "(sin empresa)"
    # Limpiar roles del tenant anterior
    UserRole.query.filter_by(user_id=user.id).delete()

    user.tenant_id = target.id

    # Asignar rol por defecto en el nuevo tenant
    default_role = Role.query.filter_by(code="vendedor").first()
    if default_role:
        db.session.add(UserRole(
            user_id=user.id, role_id=default_role.id, tenant_id=target.id
        ))

    db.session.commit()
    flash(
        f"Usuario {user.email} movido de '{old_tenant_name}' a '{target.name}' (rol: vendedor).",
        "success",
    )
    return redirect(url_for("admin.usuarios"))


# ---------------- SUSCRIPCIONES ----------------

@admin_bp.route("/suscripciones")
@login_required
@superadmin_required
def suscripciones():
    status_filter = request.args.get("status")
    query = Subscription.query
    if status_filter:
        query = query.filter_by(status=status_filter)
    subs = query.order_by(Subscription.created_at.desc()).all()
    return render_template("admin/suscripciones.html", subs=subs, status=status_filter)


@admin_bp.route("/suscripciones/<int:sub_id>/change-plan", methods=["POST"])
@login_required
@superadmin_required
def change_plan(sub_id):
    sub = db.session.get(Subscription, sub_id)
    if sub is None:
        abort(404)
    new_plan_code = request.form.get("plan_code")
    new_status = request.form.get("status")

    if new_plan_code:
        plan = Plan.query.filter_by(code=new_plan_code).first()
        if plan:
            sub.plan_id = plan.id
    if new_status:
        sub.status = new_status

    db.session.commit()
    flash(f"Suscripción de '{sub.tenant.name}' actualizada.", "success")
    return redirect(url_for("admin.suscripciones"))


# ---------------- INACTIVIDAD ----------------

@admin_bp.route("/inactividad")
@login_required
@superadmin_required
def inactividad():
    """
    Detecta empresas y usuarios inactivos.
    Filtros: 30/60/90 días sin login, o sin datos cargados.
    """
    filtro = request.args.get("filtro", "60")  # default: 60 días
    now = datetime.utcnow()

    if filtro == "sin_datos":
        # Empresas que NO tienen clientes, productos ni facturas
        tenants = Tenant.query.all()
        empresas_inactivas = []
        for t in tenants:
            if t.slug == "lempis-admin":
                continue
            stats = _tenant_stats(t.id, now)
            if stats["customers"] == 0 and stats["products"] == 0 and stats["invoices"] == 0:
                empresas_inactivas.append({"tenant": t, "stats": stats})
        empresas_inactivas.sort(key=lambda x: x["tenant"].created_at, reverse=True)
        criterio = "sin datos cargados (cero clientes, productos y facturas)"

    elif filtro == "nunca":
        # Empresas donde NINGÚN usuario ha iniciado sesión nunca
        empresas_inactivas = []
        for t in Tenant.query.all():
            if t.slug == "lempis-admin":
                continue
            never_logged = all(u.last_login_at is None for u in t.users)
            if never_logged and t.users:
                empresas_inactivas.append({"tenant": t, "stats": _tenant_stats(t.id, now)})
        empresas_inactivas.sort(key=lambda x: x["tenant"].created_at, reverse=True)
        criterio = "ningún usuario ha iniciado sesión nunca"

    else:
        # 30/60/90 días sin login
        days = int(filtro)
        threshold = now - timedelta(days=days)

        empresas_inactivas = []
        for t in Tenant.query.all():
            if t.slug == "lempis-admin":
                continue
            # Último login de cualquier usuario del tenant
            last_login = None
            for u in t.users:
                if u.last_login_at and (last_login is None or u.last_login_at > last_login):
                    last_login = u.last_login_at

            if last_login is None or last_login < threshold:
                empresas_inactivas.append({
                    "tenant": t,
                    "stats": _tenant_stats(t.id, now),
                    "last_login": last_login,
                })
        # Ordenar: más antiguas primero
        empresas_inactivas.sort(
            key=lambda x: (x.get("last_login") or datetime(1970, 1, 1))
        )
        criterio = f"sin login en los últimos {days} días"

    return render_template(
        "admin/inactividad.html",
        empresas_inactivas=empresas_inactivas,
        filtro=filtro, criterio=criterio,
    )


def _tenant_stats(tenant_id: int, now: datetime) -> dict:
    """Estadísticas rápidas de uso de un tenant."""
    last_invoice = (
        Invoice.query.filter_by(tenant_id=tenant_id)
        .order_by(Invoice.issue_date.desc()).first()
    )
    return {
        "users": User.query.filter_by(tenant_id=tenant_id).count(),
        "customers": Customer.query.filter_by(tenant_id=tenant_id).count(),
        "products": Product.query.filter_by(tenant_id=tenant_id).count(),
        "invoices": Invoice.query.filter_by(tenant_id=tenant_id).count(),
        "last_invoice_date": last_invoice.issue_date if last_invoice else None,
    }
