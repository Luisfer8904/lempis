"""
Dashboard del tenant: resumen, KPIs, accesos rápidos e inventario.
"""
from datetime import datetime, timedelta
from sqlalchemy import desc, func

from flask import Blueprint, render_template
from flask_login import login_required, current_user

from models import db
from models.invoice import Invoice, InvoiceItem
from models.catalog import Customer, Product
from services.tenant_context import current_tenant
from services.permissions import tenant_required
from services.inventory import batches_by_status
from services.receivables import receivables_summary

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/app")


@dashboard_bp.route("/")
@login_required
@tenant_required
def home():
    tenant = current_tenant()
    now = datetime.utcnow()
    since = now - timedelta(days=30)
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_month_end = current_month_start - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)

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
    revenue_current_month = (
        db.session.query(func.coalesce(func.sum(Invoice.total), 0))
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(["issued", "paid", "partially_paid"]),
            Invoice.issue_date >= current_month_start,
        )
        .scalar()
        or 0
    )
    revenue_previous_month = (
        db.session.query(func.coalesce(func.sum(Invoice.total), 0))
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(["issued", "paid", "partially_paid"]),
            Invoice.issue_date >= previous_month_start,
            Invoice.issue_date < current_month_start,
        )
        .scalar()
        or 0
    )
    invoices_30d_count = (
        Invoice.query.filter(
            Invoice.tenant_id == tenant.id,
            Invoice.issue_date >= since,
        ).count()
    )
    customers_30d_count = (
        Customer.query.filter(
            Customer.tenant_id == tenant.id,
            Customer.created_at >= since,
        ).count()
    )
    low_stock_count = (
        Product.query.filter(
            Product.tenant_id == tenant.id,
            Product.is_active.is_(True),
            Product.track_stock.is_(True),
            Product.stock <= 5,
        ).count()
    )

    rotation_since = now - timedelta(days=90)
    sold_qty = func.coalesce(func.sum(InvoiceItem.quantity), 0).label("sold_qty")
    sold_value = func.coalesce(func.sum(InvoiceItem.subtotal), 0).label("sold_value")
    rotation_sales = (
        db.session.query(
            InvoiceItem.product_id.label("product_id"),
            sold_qty,
            sold_value,
        )
        .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(["issued", "paid", "partially_paid"]),
            Invoice.issue_date >= rotation_since,
            InvoiceItem.product_id.isnot(None),
        )
        .group_by(InvoiceItem.product_id)
        .subquery()
    )
    sold_qty_col = func.coalesce(rotation_sales.c.sold_qty, 0)
    sold_value_col = func.coalesce(rotation_sales.c.sold_value, 0)

    low_stock_products = (
        Product.query.filter(
            Product.tenant_id == tenant.id,
            Product.is_active.is_(True),
            Product.track_stock.is_(True),
            Product.stock <= 5,
        )
        .order_by(Product.stock.asc(), Product.name.asc())
        .limit(5)
        .all()
    )
    high_rotation_products = (
        db.session.query(
            Product,
            sold_qty_col.label("sold_qty"),
            sold_value_col.label("sold_value"),
        )
        .outerjoin(rotation_sales, rotation_sales.c.product_id == Product.id)
        .filter(
            Product.tenant_id == tenant.id,
            Product.is_active.is_(True),
            Product.track_stock.is_(True),
            sold_qty_col > 0,
        )
        .order_by(desc(sold_qty_col), desc(sold_value_col), Product.name.asc())
        .limit(5)
        .all()
    )
    low_rotation_products = (
        db.session.query(
            Product,
            sold_qty_col.label("sold_qty"),
            sold_value_col.label("sold_value"),
        )
        .outerjoin(rotation_sales, rotation_sales.c.product_id == Product.id)
        .filter(
            Product.tenant_id == tenant.id,
            Product.is_active.is_(True),
            Product.track_stock.is_(True),
            Product.stock > 0,
        )
        .order_by(sold_qty_col.asc(), Product.stock.desc(), Product.name.asc())
        .limit(5)
        .all()
    )

    # Últimas 5 facturas
    last_invoices = (
        Invoice.query.filter_by(tenant_id=tenant.id)
        .order_by(Invoice.issue_date.desc())
        .limit(5)
        .all()
    )

    # Tendencia de ventas de los últimos 6 meses, calculada en Python para
    # mantener compatibilidad entre motores de base de datos.
    month_names = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    month_starts = []
    cursor = current_month_start
    for _ in range(6):
        month_starts.append(cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    month_starts.reverse()
    chart_start = month_starts[0]
    chart_invoices = (
        Invoice.query.filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(["issued", "paid", "partially_paid"]),
            Invoice.issue_date >= chart_start,
        ).all()
    )
    revenue_by_month = {(d.year, d.month): 0.0 for d in month_starts}
    for invoice in chart_invoices:
        key = (invoice.issue_date.year, invoice.issue_date.month)
        if key in revenue_by_month:
            revenue_by_month[key] += float(invoice.total or 0)
    sales_trend = [
        {
            "label": month_names[d.month - 1],
            "value": revenue_by_month[(d.year, d.month)],
        }
        for d in month_starts
    ]
    max_trend_value = max([item["value"] for item in sales_trend] + [1])
    chart_points = []
    for idx, item in enumerate(sales_trend):
        x = 8 + (idx * (84 / max(len(sales_trend) - 1, 1)))
        y = 88 - ((item["value"] / max_trend_value) * 68)
        chart_points.append(f"{x:.2f},{y:.2f}")
    chart_line_points = " ".join(chart_points)
    chart_area_points = f"8,92 {chart_line_points} 92,92"
    month_delta = 0.0
    if revenue_previous_month:
        month_delta = (
            (float(revenue_current_month) - float(revenue_previous_month))
            / float(revenue_previous_month)
        ) * 100

    # Vencimientos de lotes
    lotes_status = batches_by_status(tenant.id, days_ahead=30)

    # Cuentas por cobrar
    receivables = receivables_summary(tenant.id)

    return render_template(
        "dashboard/home.html",
        tenant=tenant,
        user=current_user,
        invoices_count=invoices_count,
        customers_count=customers_count,
        products_count=products_count,
        revenue_30d=revenue_30d,
        revenue_current_month=revenue_current_month,
        revenue_previous_month=revenue_previous_month,
        month_delta=month_delta,
        invoices_30d_count=invoices_30d_count,
        customers_30d_count=customers_30d_count,
        low_stock_count=low_stock_count,
        sales_trend=sales_trend,
        chart_line_points=chart_line_points,
        chart_area_points=chart_area_points,
        max_trend_value=max_trend_value,
        low_stock_products=low_stock_products,
        high_rotation_products=high_rotation_products,
        low_rotation_products=low_rotation_products,
        last_invoices=last_invoices,
        lotes_status=lotes_status,
        receivables=receivables,
    )
