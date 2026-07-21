from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import func

from flask import Blueprint, render_template
from flask_login import login_required, current_user

from models import db
from models.invoice import Invoice, InvoiceItem
from models.catalog import Customer, Product
from models.locations import StockMovement
from services.tenant_context import current_tenant
from services.permissions import tenant_required
from services.plan_limits import limits_with_usage
from services.inventory import batches_by_status
from services.receivables import receivables_summary

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/app")


SALE_STATUSES = ["issued", "paid", "partially_paid", "overdue"]


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


@dashboard_bp.route("/")
@login_required
@tenant_required
def home():
    tenant = current_tenant()
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    since_30d = now - timedelta(days=30)

    invoices_count = Invoice.query.filter_by(tenant_id=tenant.id).count()
    customers_count = Customer.query.filter_by(tenant_id=tenant.id).count()
    products_count = Product.query.filter_by(tenant_id=tenant.id).count()

    sales_today = (
        db.session.query(func.coalesce(func.sum(Invoice.total), 0))
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(SALE_STATUSES),
            Invoice.issue_date >= today_start,
        )
        .scalar()
    )
    sales_month = (
        db.session.query(func.coalesce(func.sum(Invoice.total), 0))
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(SALE_STATUSES),
            Invoice.issue_date >= month_start,
        )
        .scalar()
    )
    revenue_30d = (
        db.session.query(func.coalesce(func.sum(Invoice.total), 0))
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(SALE_STATUSES),
            Invoice.issue_date >= since_30d,
        )
        .scalar()
    )
    invoice_today_count = (
        db.session.query(func.count(Invoice.id))
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(SALE_STATUSES),
            Invoice.issue_date >= today_start,
        )
        .scalar()
    )
    invoice_month_count = (
        db.session.query(func.count(Invoice.id))
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(SALE_STATUSES),
            Invoice.issue_date >= month_start,
        )
        .scalar()
    )
    avg_ticket_month = (
        _decimal(sales_month) / Decimal(invoice_month_count)
        if invoice_month_count
        else Decimal("0")
    )

    draft_count = Invoice.query.filter_by(tenant_id=tenant.id, status="draft").count()

    plan_usage = limits_with_usage(tenant)

    last_invoices = (
        Invoice.query.filter_by(tenant_id=tenant.id)
        .order_by(Invoice.issue_date.desc())
        .limit(6)
        .all()
    )

    top_products = (
        db.session.query(
            Product.name,
            Product.sku,
            func.coalesce(func.sum(InvoiceItem.quantity), 0).label("units"),
            func.coalesce(func.sum(InvoiceItem.subtotal), 0).label("revenue"),
        )
        .join(InvoiceItem, InvoiceItem.product_id == Product.id)
        .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .filter(
            Product.tenant_id == tenant.id,
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(SALE_STATUSES),
            Invoice.issue_date >= month_start,
        )
        .group_by(Product.id, Product.name, Product.sku)
        .order_by(func.coalesce(func.sum(InvoiceItem.subtotal), 0).desc())
        .limit(5)
        .all()
    )

    low_stock_products = (
        Product.query.filter(
            Product.tenant_id == tenant.id,
            Product.kind == "product",
            Product.track_stock.is_(True),
            Product.is_active.is_(True),
            Product.stock <= 5,
        )
        .order_by(Product.stock.asc(), Product.name.asc())
        .limit(6)
        .all()
    )

    recent_movements = (
        StockMovement.query.filter_by(tenant_id=tenant.id)
        .order_by(StockMovement.moved_at.desc(), StockMovement.created_at.desc())
        .limit(5)
        .all()
    )

    lotes_status = batches_by_status(tenant.id, days_ahead=30)
    receivables = receivables_summary(tenant.id)

    return render_template(
        "dashboard/home.html",
        tenant=tenant,
        user=current_user,
        invoices_count=invoices_count,
        customers_count=customers_count,
        products_count=products_count,
        sales_today=sales_today,
        sales_month=sales_month,
        revenue_30d=revenue_30d,
        invoice_today_count=invoice_today_count,
        invoice_month_count=invoice_month_count,
        avg_ticket_month=avg_ticket_month,
        draft_count=draft_count,
        plan_usage=plan_usage,
        last_invoices=last_invoices,
        top_products=top_products,
        low_stock_products=low_stock_products,
        recent_movements=recent_movements,
        lotes_status=lotes_status,
        receivables=receivables,
    )
