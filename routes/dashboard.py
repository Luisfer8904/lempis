"""
Dashboard del tenant: resumen, KPIs, accesos rápidos, uso del plan.
"""
from datetime import datetime, timedelta
from sqlalchemy import func

from flask import Blueprint, render_template
from flask_login import login_required, current_user

from models import db
from models.invoice import Invoice
from models.catalog import Customer, Product
from services.tenant_context import current_tenant
from services.permissions import tenant_required
from services.plan_limits import limits_with_usage

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/app")


@dashboard_bp.route("/")
@login_required
@tenant_required
def home():
    tenant = current_tenant()
    since = datetime.utcnow() - timedelta(days=30)

    # KPIs básicos
    invoices_count = Invoice.query.filter_by(tenant_id=tenant.id).count()
    customers_count = Customer.query.filter_by(tenant_id=tenant.id).count()
    products_count = Product.query.filter_by(tenant_id=tenant.id).count()

    revenue_30d = (
        db.session.query(func.coalesce(func.sum(Invoice.total), 0))
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(["issued", "paid", "partially_paid"]),
            Invoice.issue_date >= since,
        )
        .scalar()
    )

    # Uso del plan
    plan_usage = limits_with_usage(tenant)

    # Últimas 5 facturas
    last_invoices = (
        Invoice.query.filter_by(tenant_id=tenant.id)
        .order_by(Invoice.issue_date.desc())
        .limit(5)
        .all()
    )

    return render_template(
        "dashboard/home.html",
        tenant=tenant,
        user=current_user,
        invoices_count=invoices_count,
        customers_count=customers_count,
        products_count=products_count,
        revenue_30d=revenue_30d,
        plan_usage=plan_usage,
        last_invoices=last_invoices,
    )
