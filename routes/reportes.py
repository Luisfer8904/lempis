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
    month_start = datetime(now.year, now.month, 1)
    month_end = datetime.combine(now.date() + timedelta(days=1), time.min)
    valid_statuses = ["issued", "paid", "partially_paid", "overdue"]

    rows = _profit_by_category(tenant.id, month_start, month_end, valid_statuses)
    total_profit = sum(float(r.profit or 0) for r in rows)
    total_revenue = sum(float(r.revenue or 0) for r in rows)
    total_cost = sum(float(r.cost or 0) for r in rows)
    margin = (total_profit / total_revenue * 100) if total_revenue else 0

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
    )


@reportes_bp.route("/detallado")
@login_required
@tenant_required
@permission_required("reports.view")
def detallado():
    context = _detailed_context()
    return render_template("reportes/detallado.html", **context)


@reportes_bp.route("/detallado/excel")
@login_required
@tenant_required
@permission_required("reports.view")
def detallado_excel():
    context = _detailed_context()
    stream = _build_excel_report(context)
    return send_file(
        stream,
        as_attachment=True,
        download_name=f"reporte-detallado-{context['desde']}-{context['hasta']}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@reportes_bp.route("/detallado/pdf")
@login_required
@tenant_required
@permission_required("reports.view")
def detallado_pdf():
    context = _detailed_context()
    stream = _build_pdf_report(context)
    return send_file(
        stream,
        as_attachment=True,
        download_name=f"reporte-detallado-{context['desde']}-{context['hasta']}.pdf",
        mimetype="application/pdf",
    )


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


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
