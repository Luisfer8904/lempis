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
from io import BytesIO
from sqlalchemy import func, extract

from flask import Blueprint, render_template, request, send_file
from flask_login import login_required
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from models import db
from models.invoice import Invoice, InvoiceItem
from models.catalog import Product, Customer, Category, ProductBatch
from services.tenant_context import current_tenant
from services.permissions import permission_required, tenant_required

reportes_bp = Blueprint("reportes", __name__, url_prefix="/app/reportes")

REPORT_OPTIONS = [
    ("ventas_contado", "Reporte de ventas de contado", "Ventas pagadas en efectivo, transferencia o tarjeta."),
    ("ventas_credito", "Reporte de ventas de crédito", "Facturas emitidas a crédito y sus saldos."),
    ("inventario", "Inventarios", "Existencias, costos y valor de inventario."),
    ("productos_vencer", "Listado de productos por vencer", "Lotes vigentes próximos a vencer."),
]


@reportes_bp.route("/")
@login_required
@tenant_required
@permission_required("reports.view")
def index():
    tenant = current_tenant()
    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)
    month_end = datetime.combine(now.date() + timedelta(days=1), time.min)
    valid_statuses = ["issued", "paid", "partially_paid", "overdue"]

    rows = _profit_by_category(tenant.id, month_start, month_end, valid_statuses)
    total_profit = sum(float(r.profit or 0) for r in rows)
    total_revenue = sum(float(r.revenue or 0) for r in rows)
    total_cost = sum(float(r.cost or 0) for r in rows)
    margin = (total_profit / total_revenue * 100) if total_revenue else 0
    year_start = datetime(now.year, 1, 1)
    year_context = _summary_blocks_context(tenant, year_start, month_end, valid_statuses)

    return render_template(
        "reportes/index.html",
        tenant=tenant,
        period_label=f"01/{now.month:02d}/{now.year} - {now.strftime('%d/%m/%Y')}",
        category_profit=rows,
        category_labels=[r.category for r in rows],
        category_profit_values=[float(r.profit or 0) for r in rows],
        total_profit=total_profit,
        total_revenue=total_revenue,
        total_cost=total_cost,
        margin=margin,
        **year_context,
    )


@reportes_bp.route("/detallado")
@login_required
@tenant_required
@permission_required("reports.view")
def detallado():
    context = _custom_report_context()
    return render_template("reportes/detallado.html", **context)


@reportes_bp.route("/detallado/excel")
@login_required
@tenant_required
@permission_required("reports.view")
def detallado_excel():
    context = _custom_report_context()
    stream = _build_custom_excel_report(context)
    return send_file(
        stream,
        as_attachment=True,
        download_name=f"{context['report_slug']}-{context['desde']}-{context['hasta']}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@reportes_bp.route("/detallado/pdf")
@login_required
@tenant_required
@permission_required("reports.view")
def detallado_pdf():
    context = _custom_report_context()
    stream = _build_custom_pdf_report(context)
    return send_file(
        stream,
        as_attachment=True,
        download_name=f"{context['report_slug']}-{context['desde']}-{context['hasta']}.pdf",
        mimetype="application/pdf",
    )


def _custom_report_context():
    tenant = current_tenant()
    now = datetime.utcnow()
    report_map = {key: (title, desc) for key, title, desc in REPORT_OPTIONS}
    report_type = request.args.get("tipo") or ""
    if report_type not in report_map:
        report_type = ""

    default_start = datetime(now.year, now.month, 1).date()
    default_end = now.date()
    start_date = _parse_date(request.args.get("desde")) or default_start
    end_date = _parse_date(request.args.get("hasta")) or default_end
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    title, description = report_map.get(report_type, ("Selecciona un reporte", "Elige el tipo de reporte que quieres consultar."))
    columns, rows, totals = _custom_report_data(tenant, report_type, start_date, end_date)
    return dict(
        tenant=tenant,
        report_options=REPORT_OPTIONS,
        report_type=report_type,
        report_slug=report_type or "reporte-detallado",
        report_title=title,
        report_description=description,
        columns=columns,
        rows=rows,
        totals=totals,
        desde=start_date.isoformat(),
        hasta=end_date.isoformat(),
        period_label=f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}",
        uses_dates=report_type in {"ventas_contado", "ventas_credito"},
    )


def _custom_report_data(tenant, report_type, start_date, end_date):
    if not report_type:
        return [], [], {}
    period_start = datetime.combine(start_date, time.min)
    period_end = datetime.combine(end_date + timedelta(days=1), time.min)
    valid_statuses = ["issued", "paid", "partially_paid", "overdue"]

    if report_type == "ventas_contado":
        invoices = (
            Invoice.query
            .filter(
                Invoice.tenant_id == tenant.id,
                Invoice.status.in_(valid_statuses),
                Invoice.payment_method.in_(["efectivo", "transferencia", "tarjeta"]),
                Invoice.issue_date >= period_start,
                Invoice.issue_date < period_end,
            )
            .order_by(Invoice.issue_date.desc(), Invoice.number.desc())
            .all()
        )
        rows = []
        for inv in invoices:
            profit = _invoice_profit(inv)
            rows.append({
                "fecha": inv.issue_date.strftime("%d/%m/%Y"),
                "factura": inv.number,
                "cliente": inv.customer.name if inv.customer else "Consumidor final",
                "metodo": _payment_label(inv.payment_method),
                "subtotal": float(inv.subtotal or 0),
                "total": float(inv.total or 0),
                "utilidad": profit,
            })
        return (
            [
                ("fecha", "Fecha", "text"), ("factura", "Factura", "text"),
                ("cliente", "Cliente", "text"), ("metodo", "Método", "text"),
                ("subtotal", "Subtotal", "money"), ("total", "Total", "money"),
                ("utilidad", "Utilidad", "money"),
            ],
            rows,
            _totals(rows, ["subtotal", "total", "utilidad"]),
        )

    if report_type == "ventas_credito":
        invoices = (
            Invoice.query
            .filter(
                Invoice.tenant_id == tenant.id,
                Invoice.status.in_(valid_statuses),
                Invoice.payment_method == "credito",
                Invoice.issue_date >= period_start,
                Invoice.issue_date < period_end,
            )
            .order_by(Invoice.issue_date.desc(), Invoice.number.desc())
            .all()
        )
        rows = [{
            "fecha": inv.issue_date.strftime("%d/%m/%Y"),
            "factura": inv.number,
            "cliente": inv.customer.name if inv.customer else "Sin cliente",
            "vence": inv.due_date.strftime("%d/%m/%Y") if inv.due_date else "",
            "total": float(inv.total or 0),
            "abonado": float(inv.amount_paid or 0),
            "saldo": float(inv.amount_due or 0),
            "estado": inv.status,
        } for inv in invoices]
        return (
            [
                ("fecha", "Fecha", "text"), ("factura", "Factura", "text"),
                ("cliente", "Cliente", "text"), ("vence", "Vence", "text"),
                ("total", "Total", "money"), ("abonado", "Abonado", "money"),
                ("saldo", "Saldo", "money"), ("estado", "Estado", "text"),
            ],
            rows,
            _totals(rows, ["total", "abonado", "saldo"]),
        )

    if report_type == "inventario":
        products = (
            Product.query
            .filter(Product.tenant_id == tenant.id)
            .order_by(Product.name.asc())
            .all()
        )
        rows = [{
            "sku": p.sku,
            "producto": p.name,
            "categoria": p.category.name if p.category else "Sin categoría",
            "stock": float(p.stock or 0),
            "costo": float(p.cost or 0),
            "precio": float(p.price or 0),
            "valor_costo": float((p.stock or 0) * (p.cost or 0)),
            "activo": "Activo" if p.is_active else "Inactivo",
        } for p in products]
        return (
            [
                ("sku", "SKU", "text"), ("producto", "Producto", "text"),
                ("categoria", "Categoría", "text"), ("stock", "Stock", "number"),
                ("costo", "Costo", "money"), ("precio", "Precio", "money"),
                ("valor_costo", "Valor costo", "money"), ("activo", "Estado", "text"),
            ],
            rows,
            _totals(rows, ["stock", "valor_costo"]),
        )

    if report_type == "productos_vencer":
        today = datetime.utcnow().date()
        limit = today + timedelta(days=60)
        batches = (
            ProductBatch.query
            .join(Product, Product.id == ProductBatch.product_id)
            .filter(
                ProductBatch.tenant_id == tenant.id,
                ProductBatch.expiration_date.isnot(None),
                ProductBatch.expiration_date >= today,
                ProductBatch.expiration_date <= limit,
                ProductBatch.remaining_quantity > 0,
            )
            .order_by(ProductBatch.expiration_date.asc())
            .all()
        )
        rows = [{
            "producto": b.product.name if b.product else "",
            "sku": b.product.sku if b.product else "",
            "lote": b.batch_number,
            "vence": b.expiration_date.strftime("%d/%m/%Y") if b.expiration_date else "",
            "dias": b.days_until_expiry,
            "cantidad": float(b.remaining_quantity or 0),
            "costo": float(b.cost or 0),
        } for b in batches]
        return (
            [
                ("producto", "Producto", "text"), ("sku", "SKU", "text"),
                ("lote", "Lote", "text"), ("vence", "Vence", "text"),
                ("dias", "Días", "number"), ("cantidad", "Cantidad", "number"),
                ("costo", "Costo", "money"),
            ],
            rows,
            _totals(rows, ["cantidad"]),
        )

    return [], [], {}


def _detailed_context():
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

    return dict(
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


def _profit_by_category(tenant_id, period_start, period_end, valid_statuses):
    return (
        db.session.query(
            func.coalesce(Category.name, "Sin categoría").label("category"),
            func.coalesce(func.sum(InvoiceItem.subtotal), 0).label("revenue"),
            func.coalesce(func.sum(InvoiceItem.quantity * func.coalesce(Product.cost, 0)), 0).label("cost"),
            func.coalesce(
                func.sum(InvoiceItem.subtotal - (InvoiceItem.quantity * func.coalesce(Product.cost, 0))),
                0,
            ).label("profit"),
            func.coalesce(func.sum(InvoiceItem.quantity), 0).label("units"),
        )
        .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .outerjoin(Product, Product.id == InvoiceItem.product_id)
        .outerjoin(Category, Category.id == Product.category_id)
        .filter(
            InvoiceItem.tenant_id == tenant_id,
            Invoice.status.in_(valid_statuses),
            Invoice.issue_date >= period_start,
            Invoice.issue_date < period_end,
        )
        .group_by(func.coalesce(Category.name, "Sin categoría"))
        .order_by(func.sum(InvoiceItem.subtotal - (InvoiceItem.quantity * func.coalesce(Product.cost, 0))).desc())
        .all()
    )


def _summary_blocks_context(tenant, period_start, period_end, valid_statuses):
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
    return dict(
        summary_period_label=f"{period_start.strftime('%d/%m/%Y')} - {(period_end - timedelta(days=1)).strftime('%d/%m/%Y')}",
        top_products=top_products,
        top_customers=top_customers,
        payment_methods=payment_methods,
        sales_by_category=sales_by_category,
    )


def _build_excel_report(context):
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    headers = ["Indicador", "Valor"]
    ws.append(headers)
    ws.append(["Periodo", context["period_label"]])
    ws.append(["Ingresos", context["revenue_period"]])
    ws.append(["Utilidad bruta estimada", context["profit_total"]])
    ws.append(["Margen bruto", f"{context['margin']:.1f}%"])
    ws.append(["Facturas", context["invoices_period"]])
    _style_sheet(ws)

    products = wb.create_sheet("Productos")
    products.append(["Producto", "SKU", "Unidades", "Ingresos", "Utilidad"])
    for p in context["top_products"]:
        products.append([p.name, p.sku, float(p.units or 0), float(p.revenue or 0), float(p.profit or 0)])
    _style_sheet(products)

    customers = wb.create_sheet("Clientes")
    customers.append(["Cliente", "RTN", "Facturas", "Total"])
    for c in context["top_customers"]:
        customers.append([c.name, c.tax_id or "", c.count, float(c.revenue or 0)])
    _style_sheet(customers)

    payments = wb.create_sheet("Metodos de pago")
    payments.append(["Metodo", "Facturas", "Total"])
    for pm in context["payment_methods"]:
        payments.append([pm.method, pm.count, float(pm.total or 0)])
    _style_sheet(payments)

    categories = wb.create_sheet("Categorias")
    categories.append(["Categoria", "Unidades", "Total"])
    for cat in context["sales_by_category"]:
        categories.append([cat.category, float(cat.units or 0), float(cat.total or 0)])
    _style_sheet(categories)

    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream


def _build_custom_excel_report(context):
    wb = Workbook()
    ws = wb.active
    ws.title = "Reporte"
    ws.append([context["report_title"]])
    ws.append(["Periodo", context["period_label"] if context["uses_dates"] else "Actual"])
    ws.append([])
    ws.append([label for _, label, _ in context["columns"]])
    for row in context["rows"]:
        ws.append([row.get(key, "") for key, _, _ in context["columns"]])
    if context["totals"]:
        ws.append([])
        total_row = []
        for key, label, _ in context["columns"]:
            total_row.append(context["totals"].get(key, "Totales" if not total_row else ""))
        ws.append(total_row)
    _style_sheet(ws)
    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream


def _style_sheet(ws):
    fill = PatternFill("solid", fgColor="EEF2FF")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="1E293B")
        cell.fill = fill
    ws.freeze_panes = "A2"
    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        width = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[letter].width = min(max(width + 2, 12), 42)


def _build_pdf_report(context):
    stream = BytesIO()
    doc = SimpleDocTemplate(
        stream,
        pagesize=letter,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Reporte detallado", styles["Title"]),
        Paragraph(f"Periodo: {context['period_label']}", styles["Normal"]),
        Spacer(1, 8),
    ]
    summary_data = [
        ["Ingresos", f"{context['tenant'].currency} {context['revenue_period']:.2f}"],
        ["Utilidad bruta estimada", f"{context['tenant'].currency} {context['profit_total']:.2f}"],
        ["Margen bruto", f"{context['margin']:.1f}%"],
        ["Facturas", str(context["invoices_period"])],
    ]
    story.append(_pdf_table(summary_data, [70 * mm, 55 * mm]))
    story += [Spacer(1, 12), Paragraph("Top productos", styles["Heading2"])]
    product_rows = [["Producto", "Unid.", "Ingresos", "Utilidad"]]
    for p in context["top_products"]:
        product_rows.append([
            p.name[:36],
            f"{float(p.units or 0):.0f}",
            f"{float(p.revenue or 0):.2f}",
            f"{float(p.profit or 0):.2f}",
        ])
    story.append(_pdf_table(product_rows, [70 * mm, 25 * mm, 35 * mm, 35 * mm], header=True))
    story += [Spacer(1, 12), Paragraph("Top clientes", styles["Heading2"])]
    customer_rows = [["Cliente", "Facturas", "Total"]]
    for c in context["top_customers"]:
        customer_rows.append([c.name[:42], str(c.count), f"{float(c.revenue or 0):.2f}"])
    story.append(_pdf_table(customer_rows, [90 * mm, 30 * mm, 45 * mm], header=True))
    doc.build(story)
    stream.seek(0)
    return stream


def _build_custom_pdf_report(context):
    stream = BytesIO()
    doc = SimpleDocTemplate(
        stream,
        pagesize=letter,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph(context["report_title"], styles["Title"]),
        Paragraph(f"Periodo: {context['period_label'] if context['uses_dates'] else 'Actual'}", styles["Normal"]),
        Spacer(1, 8),
    ]
    visible_cols = context["columns"][:7]
    data = [[label for _, label, _ in visible_cols]]
    for row in context["rows"][:60]:
        data.append([_format_report_value(row.get(key), kind, context["tenant"].currency) for key, _, kind in visible_cols])
    if len(data) == 1:
        data.append(["Sin datos"] + [""] * (len(visible_cols) - 1))
    widths = _pdf_widths(len(visible_cols))
    story.append(_pdf_table(data, widths, header=True))
    doc.build(story)
    stream.seek(0)
    return stream


def _pdf_table(data, col_widths, header=False):
    table = Table(data, colWidths=col_widths)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2FF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    table.setStyle(TableStyle(style))
    return table


def _pdf_widths(count):
    usable = 185 * mm
    if count <= 4:
        return [usable / count] * count
    return [usable * 0.22] + [usable * 0.78 / (count - 1)] * (count - 1)


def _invoice_profit(invoice):
    profit = 0
    for item in invoice.items:
        cost = item.product.cost if item.product else 0
        profit += float(item.subtotal or 0) - (float(item.quantity or 0) * float(cost or 0))
    return profit


def _payment_label(value):
    return {
        "efectivo": "Efectivo",
        "transferencia": "Transferencia",
        "tarjeta": "Tarjeta",
        "credito": "Crédito",
    }.get(value, value or "")


def _totals(rows, keys):
    return {key: sum(float(row.get(key) or 0) for row in rows) for key in keys}


def _format_report_value(value, kind, currency):
    if kind == "money":
        return f"{currency} {float(value or 0):.2f}"
    if kind == "number":
        return f"{float(value or 0):.2f}".rstrip("0").rstrip(".")
    return "" if value is None else str(value)


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
