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

    stats = {
        "users": len(users),
        "invoices": Invoice.query.filter_by(tenant_id=tenant.id).count(),
        "customers": Customer.query.filter_by(tenant_id=tenant.id).count(),
        "products": Product.query.filter_by(tenant_id=tenant.id).count(),
        "revenue": float(revenue_total or 0),
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

    # Adjuntar tenant a cada user para el template
    return render_template("admin/usuarios.html", users=users, q=q)


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
