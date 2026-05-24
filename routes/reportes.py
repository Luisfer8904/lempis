"""
Reportes y analítica del tenant:
- Ventas por mes (últimos 12 meses)
- Top productos por ingresos
- Top clientes por ingresos
- Distribución por método de pago
- Estado de cobros (pendiente vs pagado vs vencido)
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from sqlalchemy import func, extract

from flask import Blueprint, render_template, request
from flask_login import login_required

from models import db
from models.invoice import Invoice, InvoiceItem
from models.catalog import Product, Customer, Category
from services.tenant_context import current_tenant
from services.permissions import permission_required, tenant_required

reportes_bp = Blueprint("reportes", __name__, url_prefix="/app/reportes")


@reportes_bp.route("/")
@login_required
@tenant_required
@permission_required("reports.view")
def index():
    tenant = current_tenant()
    now = datetime.utcnow()
    year_start = datetime(now.year, 1, 1)
    default_start = year_start.date()
    default_end = now.date()
    start_date = _parse_date(request.args.get("desde")) or default_start
    end_date = _parse_date(request.args.get("hasta")) or default_end
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    period_start = datetime.combine(start_date, time.min)
    period_end = datetime.combine(end_date + timedelta(days=1), time.min)
    period_days = (end_date - start_date).days + 1

    valid_statuses = ["issued", "paid", "partially_paid", "overdue"]

    # -------- Ingresos y utilidad por periodo --------
    if period_days <= 62:
        bucket = func.date(Invoice.issue_date).label("bucket")
        labels = [(start_date + timedelta(days=i)).strftime("%d/%m") for i in range(period_days)]
        bucket_keys = [(start_date + timedelta(days=i)).isoformat() for i in range(period_days)]
        order_cols = [bucket]
    else:
        bucket_year = extract("year", Invoice.issue_date).label("bucket_year")
        bucket_month = extract("month", Invoice.issue_date).label("bucket_month")
        bucket = None
        labels = []
        bucket_keys = []
        month_names = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
        cur = start_date.replace(day=1)
        while cur <= end_date:
            labels.append(f"{month_names[cur.month - 1]} {str(cur.year)[2:]}")
            bucket_keys.append(f"{cur.year}-{cur.month:02d}")
            if cur.month == 12:
                cur = cur.replace(year=cur.year + 1, month=1)
            else:
                cur = cur.replace(month=cur.month + 1)
        order_cols = [bucket_year, bucket_month]

    chart_query = db.session.query(
        func.coalesce(func.sum(InvoiceItem.subtotal), 0).label("revenue"),
        func.coalesce(
            func.sum(InvoiceItem.subtotal - (InvoiceItem.quantity * func.coalesce(Product.cost, 0))),
            0,
        ).label("profit"),
    )
    if period_days <= 62:
        chart_query = chart_query.add_columns(bucket)
    else:
        chart_query = chart_query.add_columns(bucket_year, bucket_month)

    chart_rows = (
        chart_query
        .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .outerjoin(Product, Product.id == InvoiceItem.product_id)
        .filter(
            InvoiceItem.tenant_id == tenant.id,
            Invoice.status.in_(valid_statuses),
            Invoice.issue_date >= period_start,
            Invoice.issue_date < period_end,
        )
        .group_by(*(order_cols))
        .order_by(*(order_cols))
        .all()
    )

    revenue_by_key = {}
    profit_by_key = {}
    for row in chart_rows:
        if period_days <= 62:
            key = row.bucket.isoformat() if hasattr(row.bucket, "isoformat") else str(row.bucket)
        else:
            key = f"{int(row.bucket_year)}-{int(row.bucket_month):02d}"
        revenue_by_key[key] = float(row.revenue or 0)
        profit_by_key[key] = float(row.profit or 0)

    chart_revenue_values = [revenue_by_key.get(key, 0) for key in bucket_keys]
    chart_profit_values = [profit_by_key.get(key, 0) for key in bucket_keys]

    # -------- Ventas por mes (últimos 12 meses, referencia) --------
    twelve_months_ago = (now.replace(day=1) - timedelta(days=365)).replace(day=1)
    monthly_rows = (
        db.session.query(
            extract("year", Invoice.issue_date).label("year"),
            extract("month", Invoice.issue_date).label("month"),
            func.coalesce(func.sum(Invoice.total), 0).label("total"),
            func.count(Invoice.id).label("count"),
        )
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(valid_statuses),
            Invoice.issue_date >= twelve_months_ago,
        )
        .group_by("year", "month")
        .order_by("year", "month")
        .all()
    )

    months_labels = []
    months_values = []
    month_names = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
                   "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    # Generar últimos 12 meses
    cur = twelve_months_ago
    while cur <= now:
        label = f"{month_names[cur.month - 1]} {str(cur.year)[2:]}"
        months_labels.append(label)
        # Buscar valor
        match = next((r for r in monthly_rows if int(r.year) == cur.year and int(r.month) == cur.month), None)
        months_values.append(float(match.total) if match else 0)
        # Avanzar 1 mes
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)

    # -------- Top productos (periodo) --------
    top_products = (
        db.session.query(
            Product.name.label("name"),
            Product.sku.label("sku"),
            func.coalesce(func.sum(InvoiceItem.subtotal), 0).label("revenue"),
            func.coalesce(func.sum(InvoiceItem.quantity), 0).label("units"),
            func.coalesce(
                func.sum(InvoiceItem.subtotal - (InvoiceItem.quantity * func.coalesce(Product.cost, 0))),
                0,
            ).label("profit"),
        )
        .join(InvoiceItem, InvoiceItem.product_id == Product.id)
        .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .filter(
            Product.tenant_id == tenant.id,
            Invoice.status.in_(valid_statuses),
            Invoice.issue_date >= period_start,
            Invoice.issue_date < period_end,
        )
        .group_by(Product.id, Product.name, Product.sku)
        .order_by(func.sum(InvoiceItem.subtotal).desc())
        .limit(10)
        .all()
    )

    # -------- Top clientes (periodo) --------
    top_customers = (
        db.session.query(
            Customer.name.label("name"),
            Customer.tax_id.label("tax_id"),
            func.coalesce(func.sum(Invoice.total), 0).label("revenue"),
            func.count(Invoice.id).label("count"),
        )
        .join(Invoice, Invoice.customer_id == Customer.id)
        .filter(
            Customer.tenant_id == tenant.id,
            Invoice.status.in_(valid_statuses),
            Invoice.issue_date >= period_start,
            Invoice.issue_date < period_end,
        )
        .group_by(Customer.id, Customer.name, Customer.tax_id)
        .order_by(func.sum(Invoice.total).desc())
        .limit(10)
        .all()
    )

    # -------- Métodos de pago (periodo) --------
    payment_methods = (
        db.session.query(
            Invoice.payment_method.label("method"),
            func.count(Invoice.id).label("count"),
            func.coalesce(func.sum(Invoice.total), 0).label("total"),
        )
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(valid_statuses),
            Invoice.issue_date >= period_start,
            Invoice.issue_date < period_end,
        )
        .group_by(Invoice.payment_method)
        .all()
    )

    # -------- Ventas por categoría (periodo) --------
    sales_by_category = (
        db.session.query(
            func.coalesce(Category.name, "Sin categoría").label("category"),
            func.coalesce(func.sum(InvoiceItem.subtotal), 0).label("total"),
            func.coalesce(func.sum(InvoiceItem.quantity), 0).label("units"),
        )
        .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .join(Product, Product.id == InvoiceItem.product_id)
        .outerjoin(Category, Category.id == Product.category_id)
        .filter(
            Product.tenant_id == tenant.id,
            Invoice.status.in_(valid_statuses),
            Invoice.issue_date >= period_start,
            Invoice.issue_date < period_end,
        )
        .group_by(func.coalesce(Category.name, "Sin categoría"))
        .order_by(func.sum(InvoiceItem.subtotal).desc())
        .all()
    )

    # -------- Resumen --------
    summary_q = (
        db.session.query(
            func.coalesce(func.sum(Invoice.total), 0).label("revenue_period"),
            func.count(Invoice.id).label("invoices_period"),
        )
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(valid_statuses),
            Invoice.issue_date >= period_start,
            Invoice.issue_date < period_end,
        )
        .one()
    )
    profit_total = sum(chart_profit_values)
    revenue_subtotal = sum(chart_revenue_values)
    margin = (profit_total / revenue_subtotal * 100) if revenue_subtotal else 0

    pending_q = (
        db.session.query(func.coalesce(func.sum(Invoice.total), 0))
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(["issued", "partially_paid", "overdue"]),
        )
        .scalar()
    )

    return render_template(
        "reportes/index.html",
        tenant=tenant,
        months_labels=months_labels,
        months_values=months_values,
        chart_labels=labels,
        chart_revenue_values=chart_revenue_values,
        chart_profit_values=chart_profit_values,
        top_products=top_products,
        top_customers=top_customers,
        payment_methods=payment_methods,
        sales_by_category=sales_by_category,
        revenue_period=float(summary_q.revenue_period or 0),
        invoices_period=summary_q.invoices_period or 0,
        profit_total=float(profit_total or 0),
        margin=float(margin or 0),
        pending_total=float(pending_q or 0),
        year=now.year,
        desde=start_date.isoformat(),
        hasta=end_date.isoformat(),
        period_label=f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}",
    )


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
