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
from numbers import Number
import os
from urllib.parse import urlparse
from xml.sax.saxutils import escape
from sqlalchemy import case, func, extract

from flask import Blueprint, current_app, render_template, request, send_file
from flask_login import login_required
from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from models import db
from models.cash import CashClosure, CashExpense
from models.invoice import Invoice, InvoiceItem
from models.catalog import Product, Customer, Category, ProductBatch
from models.locations import Branch, Warehouse, WarehouseStock
from services.datetime_utils import format_local_datetime, local_date_range_to_utc, local_now, tenant_today
from services.currency import currency_symbol, format_money
from services.tenant_context import current_tenant
from services.permissions import permission_required, tenant_required
from services.locations import sync_default_warehouse_stock

reportes_bp = Blueprint("reportes", __name__, url_prefix="/app/reportes")

REPORT_OPTIONS = [
    ("ventas_contado", "Reporte de ventas de contado", "Ventas pagadas en efectivo, transferencia o tarjeta."),
    ("ventas_credito", "Reporte de ventas de crédito", "Facturas emitidas a crédito y sus saldos."),
    ("ventas_producto", "Rastreo de ventas por producto", "Historial de facturas, clientes, cantidades y precios de un producto."),
    ("ventas_categoria", "Ventas por categoría", "Productos vendidos agrupados por categoría, con cantidades, costos y precios promedio."),
    ("cierres_caja", "Reporte de cierres de caja", "Aperturas, formas de pago, gastos, dinero contado y entregado."),
    ("inventario", "Inventarios", "Existencias, costos y valor de inventario."),
    ("productos_vencer", "Listado de productos por vencer", "Lotes vigentes próximos a vencer."),
]


@reportes_bp.route("/")
@login_required
@tenant_required
@permission_required("reports.view")
def index():
    tenant = current_tenant()
    now = local_now(tenant)
    today = now.date()
    month_start_date = today.replace(day=1)
    month_start, month_end = local_date_range_to_utc(month_start_date, today, tenant)
    valid_statuses = ["issued", "paid", "partially_paid", "overdue"]

    rows = _profit_by_category(tenant.id, month_start, month_end, valid_statuses)
    total_profit = sum(float(r.profit or 0) for r in rows)
    total_revenue = sum(float(r.revenue or 0) for r in rows)
    total_cost = sum(float(r.cost or 0) for r in rows)
    margin = (total_profit / total_revenue * 100) if total_revenue else 0
    year_start, _ = local_date_range_to_utc(datetime(now.year, 1, 1).date(), today, tenant)
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
    now = local_now(tenant)
    report_map = {key: (title, desc) for key, title, desc in REPORT_OPTIONS}
    report_type = request.args.get("tipo") or ""
    if report_type not in report_map:
        report_type = ""

    default_start = datetime(now.year, now.month, 1).date()
    default_end = tenant_today(tenant)
    start_date = _parse_date(request.args.get("desde")) or default_start
    end_date = _parse_date(request.args.get("hasta")) or default_end
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    title, description = report_map.get(report_type, ("Selecciona un reporte", "Elige el tipo de reporte que quieres consultar."))
    sync_default_warehouse_stock(tenant)
    branches = (
        Branch.query.filter_by(tenant_id=tenant.id, is_active=True)
        .order_by(Branch.name.asc())
        .all()
    )
    available_branch_ids = {branch.id for branch in branches}
    requested_branch_id = request.args.get("branch_id", type=int) or None
    branch_id = requested_branch_id if requested_branch_id in available_branch_ids else None
    product_id = request.args.get("product_id", type=int) or None
    categories = (
        Category.query.filter_by(tenant_id=tenant.id)
        .order_by(Category.name.asc())
        .all()
    )
    available_category_ids = {category.id for category in categories}
    category_ids = []
    for category_id in request.args.getlist("category_id", type=int):
        if category_id in available_category_ids and category_id not in category_ids:
            category_ids.append(category_id)
    columns, rows, totals = _custom_report_data(
        tenant,
        report_type,
        start_date,
        end_date,
        branch_id,
        product_id,
        category_ids,
    )
    selected_branch = next((branch for branch in branches if branch.id == branch_id), None)
    products = (
        Product.query.filter_by(tenant_id=tenant.id, is_active=True)
        .order_by(Product.name.asc())
        .all()
    )
    selected_product = next((p for p in products if p.id == product_id), None)
    selected_categories = [category for category in categories if category.id in category_ids]
    category_selection_label = (
        ", ".join(category.name for category in selected_categories)
        if selected_categories
        else "Todas las categorías"
    )
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
        uses_dates=report_type in {
            "ventas_contado",
            "ventas_credito",
            "ventas_producto",
            "ventas_categoria",
            "cierres_caja",
        },
        branches=branches,
        branch_id=branch_id,
        selected_branch=selected_branch,
        products=products,
        product_id=product_id,
        selected_product=selected_product,
        categories=categories,
        category_ids=category_ids,
        selected_categories=selected_categories,
        category_selection_label=category_selection_label,
    )


def _custom_report_data(
    tenant,
    report_type,
    start_date,
    end_date,
    branch_id=None,
    product_id=None,
    category_ids=None,
):
    if not report_type:
        return [], [], {}
    period_start, period_end = local_date_range_to_utc(start_date, end_date, tenant)
    valid_statuses = ["issued", "paid", "partially_paid", "overdue"]

    if report_type == "ventas_contado":
        query = (
            Invoice.query
            .filter(
                Invoice.tenant_id == tenant.id,
                Invoice.status.in_(valid_statuses),
                Invoice.payment_method.in_(["efectivo", "transferencia", "tarjeta"]),
                Invoice.issue_date >= period_start,
                Invoice.issue_date < period_end,
            )
        )
        if branch_id:
            query = query.join(Warehouse, Warehouse.id == Invoice.warehouse_id).filter(
                Warehouse.branch_id == branch_id
            )
        invoices = query.order_by(Invoice.issue_date.desc(), Invoice.number.desc()).all()
        rows = []
        for inv in invoices:
            profit = _invoice_profit(inv)
            rows.append({
                "fecha": format_local_datetime(inv.issue_date, "%d/%m/%Y", tenant),
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
        query = (
            Invoice.query
            .filter(
                Invoice.tenant_id == tenant.id,
                Invoice.status.in_(valid_statuses),
                Invoice.payment_method == "credito",
                Invoice.issue_date >= period_start,
                Invoice.issue_date < period_end,
            )
        )
        if branch_id:
            query = query.join(Warehouse, Warehouse.id == Invoice.warehouse_id).filter(
                Warehouse.branch_id == branch_id
            )
        invoices = query.order_by(Invoice.issue_date.desc(), Invoice.number.desc()).all()
        rows = [{
            "fecha": format_local_datetime(inv.issue_date, "%d/%m/%Y", tenant),
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

    if report_type == "ventas_producto":
        query = (
            db.session.query(
                InvoiceItem,
                Invoice,
                Product,
            )
            .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
            .outerjoin(Product, Product.id == InvoiceItem.product_id)
            .filter(
                InvoiceItem.tenant_id == tenant.id,
                Invoice.status.in_(valid_statuses),
                Invoice.issue_date >= period_start,
                Invoice.issue_date < period_end,
            )
        )
        if branch_id:
            query = query.join(Warehouse, Warehouse.id == Invoice.warehouse_id).filter(
                Warehouse.branch_id == branch_id
            )
        if product_id:
            query = query.filter(Product.id == product_id)
        rows_raw = query.order_by(
            Invoice.issue_date.desc(), Invoice.number.desc(), InvoiceItem.id.asc()
        ).all()
        rows = []
        for item, invoice, product in rows_raw:
            product_name = product.name if product else item.description
            product_label = f"{product.sku} · {product_name}" if product and product.sku else product_name
            line_total = (
                float(item.subtotal or 0)
                + float(item.tax_amount or 0)
                - float(item.discount_amount or 0)
            )
            rows.append({
                "fecha": format_local_datetime(invoice.issue_date, "%d/%m/%Y %H:%M", tenant),
                "factura": invoice.number,
                "cliente": invoice.customer.name if invoice.customer else (invoice.receptor_name or "Consumidor final"),
                "producto": product_label or "Producto sin catálogo",
                "cantidad": float(item.quantity or 0),
                "precio": float(item.unit_price or 0),
                "total_linea": line_total,
            })
        return (
            [
                ("fecha", "Fecha", "text"), ("factura", "Factura", "text"),
                ("cliente", "Cliente", "text"), ("producto", "Producto / SKU", "text"),
                ("cantidad", "Cantidad", "number"), ("precio", "Precio unitario", "money"),
                ("total_linea", "Total", "money"),
            ],
            rows,
            _totals(rows, ["cantidad", "total_linea"]),
        )

    if report_type == "ventas_categoria":
        category_name = func.coalesce(Category.name, "Sin categoría")
        product_name = func.coalesce(Product.name, InvoiceItem.description, "Producto sin catálogo")
        unit_label = case(
            (Product.kind == "service", "Servicio"),
            else_="Unidad",
        )
        historical_unit_cost = func.coalesce(ProductBatch.cost, Product.cost, 0)
        query = (
            db.session.query(
                category_name.label("categoria"),
                func.coalesce(Product.sku, "").label("codigo"),
                product_name.label("descripcion"),
                unit_label.label("unidad"),
                func.coalesce(func.sum(InvoiceItem.quantity), 0).label("cantidad"),
                func.coalesce(func.sum(InvoiceItem.subtotal), 0).label("ventas"),
                func.coalesce(
                    func.sum(InvoiceItem.quantity * historical_unit_cost),
                    0,
                ).label("costo_estimado"),
            )
            .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
            .outerjoin(Product, Product.id == InvoiceItem.product_id)
            .outerjoin(Category, Category.id == Product.category_id)
            .outerjoin(ProductBatch, ProductBatch.id == InvoiceItem.batch_id)
            .filter(
                InvoiceItem.tenant_id == tenant.id,
                Invoice.status.in_(valid_statuses),
                Invoice.issue_date >= period_start,
                Invoice.issue_date < period_end,
            )
        )
        if branch_id:
            query = query.join(Warehouse, Warehouse.id == Invoice.warehouse_id).filter(
                Warehouse.branch_id == branch_id
            )
        if category_ids:
            query = query.filter(Category.id.in_(category_ids))
        rows_raw = (
            query.group_by(category_name, Product.sku, product_name, Product.kind)
            .order_by(category_name.asc(), product_name.asc())
            .all()
        )
        rows = []
        for item in rows_raw:
            quantity = float(item.cantidad or 0)
            sales_value = float(item.ventas or 0)
            estimated_cost = float(item.costo_estimado or 0)
            rows.append({
                "categoria": item.categoria,
                "codigo": item.codigo or "",
                "descripcion": item.descripcion,
                "unidad": item.unidad,
                "cantidad": quantity,
                "costo_promedio": (estimated_cost / quantity) if quantity else 0,
                "precio_promedio": (sales_value / quantity) if quantity else 0,
                "total_vendido": sales_value,
            })
        return (
            [
                ("categoria", "Categoría", "text"), ("codigo", "Código", "text"),
                ("descripcion", "Descripción", "text"), ("unidad", "Unidad", "text"),
                ("cantidad", "Cantidad", "number"),
                ("costo_promedio", "Costo promedio", "money"),
                ("precio_promedio", "Precio promedio", "money"),
                ("total_vendido", "Total vendido", "money"),
            ],
            rows,
            _totals(rows, ["cantidad", "total_vendido"]),
        )

    if report_type == "cierres_caja":
        closures_query = (
            CashClosure.query
            .filter(
                CashClosure.tenant_id == tenant.id,
                CashClosure.closure_date >= start_date,
                CashClosure.closure_date <= end_date,
                CashClosure.status == "closed",
            )
        )
        if branch_id:
            closures_query = closures_query.filter(CashClosure.branch_id == branch_id)
        closures = closures_query.order_by(
            CashClosure.closure_date.desc(), CashClosure.id.desc()
        ).all()
        expenses_query = (
            db.session.query(func.coalesce(func.sum(CashExpense.amount), 0))
            .filter(
                CashExpense.tenant_id == tenant.id,
                CashExpense.expense_date >= period_start,
                CashExpense.expense_date < period_end,
            )
        )
        if branch_id:
            expenses_query = expenses_query.filter(CashExpense.branch_id == branch_id)
        expenses_total = expenses_query.scalar()
        rows = [{
            "fecha": c.closure_date.strftime("%d/%m/%Y"),
            "sede": c.branch.name if c.branch else "Toda la empresa",
            "usuario": c.user.full_name if c.user else "",
            "apertura": float(c.opening_amount or 0),
            "efectivo": float((c.cash_sales_amount or 0) + (c.receivable_cash_amount or 0)),
            "transferencia": float((c.transfer_sales_amount or 0) + (c.receivable_transfer_amount or 0)),
            "tarjeta": float((c.card_sales_amount or 0) + (c.receivable_card_amount or 0)),
            "credito": float(c.credit_sales_amount or 0),
            "gastos": float(c.expenses_amount or 0),
            "esperado": float(c.expected_cash_amount or 0),
            "contado": float(c.actual_cash_amount or 0),
            "entregado": float(c.delivered_cash_amount or 0),
            "dejado_caja": float(c.retained_cash_amount or 0),
            "diferencia": float(c.variance_amount or 0),
            "notas": c.notes or "",
        } for c in closures]
        totals = _totals(rows, [
            "apertura", "efectivo", "transferencia", "tarjeta", "credito",
            "gastos", "esperado", "contado", "entregado", "dejado_caja", "diferencia",
        ])
        totals["gastos_periodo"] = float(expenses_total or 0)
        return (
            [
                ("fecha", "Fecha", "text"), ("sede", "Sede", "text"),
                ("usuario", "Usuario", "text"), ("apertura", "Apertura", "money"),
                ("efectivo", "Efectivo", "money"), ("transferencia", "Transferencia", "money"),
                ("tarjeta", "Tarjeta", "money"), ("credito", "Crédito", "money"),
                ("gastos", "Gastos efectivo", "money"), ("esperado", "Esperado", "money"),
                ("contado", "Contado", "money"), ("entregado", "Entregado", "money"),
                ("dejado_caja", "Dejado en caja", "money"),
                ("diferencia", "Diferencia", "money"), ("notas", "Notas", "text"),
            ],
            rows,
            totals,
        )

    if report_type == "inventario":
        query = (
            db.session.query(
                Product.sku.label("sku"),
                Product.name.label("producto"),
                func.coalesce(Category.name, "Sin categoría").label("categoria"),
                func.coalesce(func.sum(WarehouseStock.quantity), 0).label("stock"),
                Product.cost.label("costo"),
            )
            .join(Product, Product.id == WarehouseStock.product_id)
            .join(Warehouse, Warehouse.id == WarehouseStock.warehouse_id)
            .join(Branch, Branch.id == Warehouse.branch_id)
            .outerjoin(Category, Category.id == Product.category_id)
            .filter(WarehouseStock.tenant_id == tenant.id)
        )
        if branch_id:
            query = query.filter(Branch.id == branch_id)
        stock_rows = (
            query.group_by(
                Product.id, Product.sku, Product.name, Product.cost, Category.name,
            )
            .order_by(Category.name.asc(), Product.name.asc())
            .all()
        )
        rows = []
        for item in stock_rows:
            quantity = float(item.stock or 0)
            cost = float(item.costo or 0)
            rows.append({
                "sku": item.sku,
                "producto": item.producto,
                "categoria": item.categoria,
                "stock": quantity,
                "costo": cost,
                "valor_inventario": quantity * cost,
            })
        return (
            [
                ("sku", "SKU", "text"), ("producto", "Producto", "text"),
                ("categoria", "Categoría", "text"), ("stock", "Stock", "number"),
                ("costo", "Costo unitario", "money"),
                ("valor_inventario", "Valor inventario", "money"),
            ],
            rows,
            _totals(rows, ["stock", "valor_inventario"]),
        )

    if report_type == "productos_vencer":
        today = datetime.utcnow().date()
        limit = today + timedelta(days=60)
        query = (
            db.session.query(ProductBatch, Product, Warehouse, Branch, WarehouseStock)
            .join(Product, Product.id == ProductBatch.product_id)
            .join(WarehouseStock, WarehouseStock.batch_id == ProductBatch.id)
            .join(Warehouse, Warehouse.id == WarehouseStock.warehouse_id)
            .join(Branch, Branch.id == Warehouse.branch_id)
            .filter(
                ProductBatch.tenant_id == tenant.id,
                ProductBatch.expiration_date.isnot(None),
                ProductBatch.expiration_date >= today,
                ProductBatch.expiration_date <= limit,
                ProductBatch.remaining_quantity > 0,
                WarehouseStock.quantity > 0,
            )
        )
        if branch_id:
            query = query.filter(Branch.id == branch_id)
        batches = query.order_by(ProductBatch.expiration_date.asc()).all()
        rows = [{
            "sede": branch.name,
            "producto": product.name,
            "sku": product.sku,
            "lote": batch.batch_number,
            "vence": batch.expiration_date.strftime("%d/%m/%Y") if batch.expiration_date else "",
            "dias": batch.days_until_expiry,
            "cantidad": float(stock.quantity or 0),
            "costo": float(batch.cost or 0),
        } for batch, product, warehouse, branch, stock in batches]
        return (
            [
                ("sede", "Sede", "text"), ("producto", "Producto", "text"),
                ("sku", "SKU", "text"),
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
    if context["report_type"] == "ventas_categoria":
        return _build_category_excel_report(context)
    if context["report_type"] == "inventario":
        return _build_inventory_excel_report(context)
    wb = Workbook()
    ws = wb.active
    ws.title = _safe_sheet_title(context["report_title"])
    columns = context["columns"]
    last_col = max(len(columns), 4)
    _prepare_excel_report_header(ws, context, last_col)
    table_header_row = 7
    for column_index, (_, label, _) in enumerate(columns, start=1):
        ws.cell(table_header_row, column_index, label)
    data_start_row = table_header_row + 1
    for row in context["rows"]:
        ws.append([row.get(key, "") for key, _, _ in columns])
        current_row = ws.max_row
        for column_index, (_, _, kind) in enumerate(columns, start=1):
            _format_excel_value(ws.cell(current_row, column_index), kind, context["tenant"].currency)
    data_end_row = ws.max_row
    if context["totals"]:
        ws.append([])
        total_row = []
        for key, _, _ in columns:
            total_row.append(context["totals"].get(key, "Totales" if not total_row else ""))
        ws.append(total_row)
        totals_row_number = ws.max_row
        for column_index, (_, _, kind) in enumerate(columns, start=1):
            cell = ws.cell(totals_row_number, column_index)
            cell.font = Font(bold=True, color="172554")
            cell.fill = PatternFill("solid", fgColor="DBEAFE")
            _format_excel_value(cell, kind, context["tenant"].currency)
    _style_excel_report_table(
        ws,
        table_header_row,
        data_start_row,
        data_end_row,
        len(columns),
    )
    _finish_excel_report_sheet(ws, table_header_row, len(columns))
    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream


def _safe_sheet_title(value):
    invalid = set('[]:*?/\\')
    clean = "".join("-" if char in invalid else char for char in (value or "Reporte"))
    return clean[:31]


def _report_filter_label(context):
    parts = []
    if context.get("uses_dates"):
        parts.append(f"Periodo: {context['period_label']}")
    else:
        parts.append("Consulta actual")
    if context.get("selected_branch"):
        parts.append(f"Sede: {context['selected_branch'].name}")
    else:
        parts.append("Todas las sedes")
    if context.get("selected_product"):
        parts.append(f"Producto: {context['selected_product'].name}")
    if context.get("report_type") == "ventas_categoria":
        parts.append(context["category_selection_label"])
    return "  •  ".join(parts)


def _resolve_report_logo_path(tenant=None, *, lempis=False):
    static_folder = current_app.static_folder
    if lempis:
        path = os.path.join(static_folder, "img", "favicon.png")
        return path if os.path.exists(path) else None

    logo_url = (getattr(tenant, "logo_url", None) or "").strip()
    if not logo_url:
        return None
    parsed = urlparse(logo_url)
    if parsed.scheme not in ("", "file"):
        return None
    path = parsed.path
    if os.path.isabs(path) and not path.startswith("/static/"):
        return path if os.path.exists(path) else None
    if path.startswith("/static/"):
        path = path[len("/static/"):]
    elif path.startswith("static/"):
        path = path[len("static/"):]
    resolved = os.path.join(static_folder, path.lstrip("/"))
    return resolved if os.path.exists(resolved) else None


def _add_excel_logo(ws, path, anchor, max_width=74, max_height=58):
    if not path:
        return False
    try:
        image = ExcelImage(path)
        scale = min(max_width / image.width, max_height / image.height, 1)
        image.width = int(image.width * scale)
        image.height = int(image.height * scale)
        ws.add_image(image, anchor)
        return True
    except Exception:
        return False


def _prepare_excel_report_header(ws, context, last_col):
    tenant = context["tenant"]
    company_name = tenant.legal_name or tenant.name
    company_logo = _resolve_report_logo_path(tenant)
    lempis_logo = _resolve_report_logo_path(lempis=True)
    last_letter = get_column_letter(last_col)
    center_start = 2 if last_col >= 4 else 1
    center_end = last_col - 1 if last_col >= 4 else last_col

    _add_excel_logo(ws, company_logo, "A1")
    _add_excel_logo(ws, lempis_logo, f"{last_letter}1")
    if not company_logo:
        ws["A1"] = "EMPRESA"
        ws["A1"].font = Font(bold=True, color="1D4ED8")
        ws["A1"].alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells(start_row=1, start_column=center_start, end_row=1, end_column=center_end)
    ws.cell(1, center_start, company_name.upper())
    ws.cell(1, center_start).font = Font(bold=True, size=16, color="0F172A")
    ws.cell(1, center_start).alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(start_row=2, start_column=center_start, end_row=2, end_column=center_end)
    identity = "  •  ".join(part for part in [
        f"RTN: {tenant.tax_id}" if tenant.tax_id else "",
        f"Tel: {tenant.phone}" if tenant.phone else "",
    ] if part) or "Reporte empresarial"
    ws.cell(2, center_start, identity)
    ws.cell(2, center_start).font = Font(size=10, color="64748B")
    ws.cell(2, center_start).alignment = Alignment(horizontal="center")

    ws.merge_cells(start_row=4, start_column=1, end_row=4, end_column=last_col)
    ws.cell(4, 1, context["report_title"].upper())
    ws.cell(4, 1).font = Font(bold=True, size=14, color="FFFFFF")
    ws.cell(4, 1).fill = PatternFill("solid", fgColor="1E3A8A")
    ws.cell(4, 1).alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(start_row=5, start_column=1, end_row=5, end_column=last_col)
    ws.cell(5, 1, _report_filter_label(context))
    ws.cell(5, 1).font = Font(italic=True, size=10, color="475569")
    ws.cell(5, 1).fill = PatternFill("solid", fgColor="EFF6FF")
    ws.cell(5, 1).alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 34
    ws.row_dimensions[2].height = 20
    ws.row_dimensions[4].height = 26
    ws.row_dimensions[5].height = 23


def _format_excel_value(cell, kind, currency):
    if kind == "money" and isinstance(cell.value, Number):
        symbol = currency_symbol(currency).replace('"', '""')
        cell.number_format = f'"{symbol}" #,##0.00'
    elif kind == "number" and isinstance(cell.value, Number):
        cell.number_format = "#,##0.00"


def _style_excel_report_table(ws, header_row, data_start, data_end, column_count):
    thin_line = Side(style="thin", color="CBD5E1")
    for cell in ws[header_row][:column_count]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0F766E")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=thin_line)
    ws.row_dimensions[header_row].height = 30
    for row_number in range(data_start, data_end + 1):
        fill = "FFFFFF" if (row_number - data_start) % 2 == 0 else "F8FAFC"
        for cell in ws[row_number][:column_count]:
            cell.fill = PatternFill("solid", fgColor=fill)
            cell.border = Border(bottom=thin_line)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[row_number].height = 22


def _finish_excel_report_sheet(ws, header_row, column_count):
    for column_index in range(1, column_count + 1):
        letter = get_column_letter(column_index)
        values = [
            len(str(ws.cell(row_number, column_index).value or ""))
            for row_number in range(header_row, ws.max_row + 1)
        ]
        ws.column_dimensions[letter].width = min(max(max(values, default=10) + 3, 13), 42)
    ws.freeze_panes = f"A{header_row + 1}"
    ws.auto_filter.ref = f"A{header_row}:{get_column_letter(column_count)}{max(ws.max_row, header_row)}"
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape" if column_count >= 6 else "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"1:{header_row}"
    ws.oddFooter.center.text = "Página &P de &N"
    ws.oddFooter.right.text = "Generado con Lempis"


def _build_category_excel_report(context):
    wb = Workbook()
    ws = wb.active
    ws.title = "Ventas por categoría"
    _prepare_excel_report_header(ws, context, 7)
    ws.append([])

    header_fill = PatternFill("solid", fgColor="0F766E")
    category_fill = PatternFill("solid", fgColor="DBEAFE")
    thin_line = Side(style="thin", color="CBD5E1")
    symbol = currency_symbol(context["tenant"].currency).replace('"', '""')
    money_format = f'"{symbol}" #,##0.00'
    headers = [
        "Código", "Descripción", "Unidad", "Cantidad",
        "Costo promedio", "Precio promedio", "Total vendido",
    ]
    for category_name, category_rows in _category_report_groups(context["rows"]):
        ws.append([f"CATEGORÍA: {category_name}"])
        category_row_number = ws.max_row
        ws.merge_cells(start_row=category_row_number, start_column=1, end_row=category_row_number, end_column=7)
        ws.cell(category_row_number, 1).font = Font(bold=True, color="1E3A8A", size=11)
        ws.cell(category_row_number, 1).fill = category_fill
        ws.cell(category_row_number, 1).alignment = Alignment(vertical="center")
        ws.row_dimensions[category_row_number].height = 24
        ws.append(headers)
        header_row_number = ws.max_row
        for cell in ws[header_row_number]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = Border(bottom=thin_line)
        ws.row_dimensions[header_row_number].height = 28
        for row in category_rows:
            ws.append([
                row["codigo"],
                row["descripcion"],
                row["unidad"],
                float(row["cantidad"] or 0),
                float(row["costo_promedio"] or 0),
                float(row["precio_promedio"] or 0),
                float(row["total_vendido"] or 0),
            ])
            ws.cell(ws.max_row, 4).number_format = "#,##0.00"
            ws.cell(ws.max_row, 5).number_format = money_format
            ws.cell(ws.max_row, 6).number_format = money_format
            ws.cell(ws.max_row, 7).number_format = money_format
            for cell in ws[ws.max_row]:
                cell.border = Border(bottom=thin_line)
                cell.alignment = Alignment(vertical="center", wrap_text=True)
            ws.row_dimensions[ws.max_row].height = 22
        category_quantity = sum(float(row.get("cantidad") or 0) for row in category_rows)
        category_total = sum(float(row.get("total_vendido") or 0) for row in category_rows)
        ws.append([
            f"TOTAL {category_name.upper()}", "", "", category_quantity, "", "", category_total,
        ])
        subtotal_row_number = ws.max_row
        ws.cell(subtotal_row_number, 1).font = Font(bold=True, color="172554")
        ws.cell(subtotal_row_number, 4).font = Font(bold=True, color="172554")
        ws.cell(subtotal_row_number, 7).font = Font(bold=True, color="1D4ED8")
        ws.cell(subtotal_row_number, 4).number_format = "#,##0.00"
        ws.cell(subtotal_row_number, 7).number_format = money_format
        for cell in ws[subtotal_row_number]:
            cell.fill = PatternFill("solid", fgColor="E0E7FF")
            cell.border = Border(top=Side(style="medium", color="A5B4FC"))
        ws.append([])

    ws.append([
        "TOTALES", "", "", float(context["totals"].get("cantidad", 0)), "", "",
        float(context["totals"].get("total_vendido", 0)),
    ])
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    ws.cell(ws.max_row, 4).font = Font(bold=True)
    ws.cell(ws.max_row, 7).font = Font(bold=True)
    ws.cell(ws.max_row, 4).number_format = "#,##0.00"
    ws.cell(ws.max_row, 7).number_format = money_format
    for cell in ws[ws.max_row]:
        cell.fill = PatternFill("solid", fgColor="DBEAFE")
        cell.border = Border(top=Side(style="medium", color="1E3A8A"))
    widths = [18, 38, 14, 16, 19, 19, 20]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.freeze_panes = "A7"
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:5"
    ws.oddFooter.center.text = "Página &P de &N"
    ws.oddFooter.right.text = "Generado con Lempis"
    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream


def _build_inventory_excel_report(context):
    wb = Workbook()
    ws = wb.active
    ws.title = "Inventario"
    _prepare_excel_report_header(ws, context, 5)
    ws.append([])

    header_fill = PatternFill("solid", fgColor="0F766E")
    category_fill = PatternFill("solid", fgColor="DBEAFE")
    thin_line = Side(style="thin", color="CBD5E1")
    symbol = currency_symbol(context["tenant"].currency).replace('"', '""')
    money_format = f'"{symbol}" #,##0.00'
    headers = ["SKU", "Nombre del producto", "Stock", "Costo unitario", "Valor inventario"]
    for category_name, category_rows in _category_report_groups(context["rows"]):
        ws.append([f"CATEGORÍA: {category_name.upper()}"])
        category_row_number = ws.max_row
        ws.merge_cells(start_row=category_row_number, start_column=1, end_row=category_row_number, end_column=5)
        ws.cell(category_row_number, 1).font = Font(bold=True, color="1E3A8A", size=11)
        ws.cell(category_row_number, 1).fill = category_fill
        ws.row_dimensions[category_row_number].height = 24
        ws.append(headers)
        header_row_number = ws.max_row
        for cell in ws[header_row_number]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = Border(bottom=thin_line)
        for row in category_rows:
            ws.append([
                row["sku"], row["producto"], float(row["stock"] or 0),
                float(row["costo"] or 0), float(row["valor_inventario"] or 0),
            ])
            ws.cell(ws.max_row, 3).number_format = "#,##0.00"
            ws.cell(ws.max_row, 4).number_format = money_format
            ws.cell(ws.max_row, 5).number_format = money_format
            for cell in ws[ws.max_row]:
                cell.border = Border(bottom=thin_line)
                cell.alignment = Alignment(vertical="center", wrap_text=True)
        category_stock = sum(float(row.get("stock") or 0) for row in category_rows)
        category_value = sum(float(row.get("valor_inventario") or 0) for row in category_rows)
        ws.append([f"TOTAL {category_name.upper()}", "", category_stock, "", category_value])
        subtotal_row = ws.max_row
        ws.cell(subtotal_row, 1).font = Font(bold=True, color="172554")
        ws.cell(subtotal_row, 3).font = Font(bold=True, color="172554")
        ws.cell(subtotal_row, 5).font = Font(bold=True, color="1D4ED8")
        ws.cell(subtotal_row, 3).number_format = "#,##0.00"
        ws.cell(subtotal_row, 5).number_format = money_format
        for cell in ws[subtotal_row]:
            cell.fill = PatternFill("solid", fgColor="E0E7FF")
            cell.border = Border(top=Side(style="medium", color="A5B4FC"))
        ws.append([])

    ws.append([
        "TOTAL GENERAL", "", float(context["totals"].get("stock", 0)), "",
        float(context["totals"].get("valor_inventario", 0)),
    ])
    total_row = ws.max_row
    ws.cell(total_row, 1).font = Font(bold=True, color="172554")
    ws.cell(total_row, 3).font = Font(bold=True, color="172554")
    ws.cell(total_row, 5).font = Font(bold=True, color="1D4ED8")
    ws.cell(total_row, 3).number_format = "#,##0.00"
    ws.cell(total_row, 5).number_format = money_format
    for cell in ws[total_row]:
        cell.fill = PatternFill("solid", fgColor="BFDBFE")
        cell.border = Border(top=Side(style="medium", color="1E3A8A"))

    for index, width in enumerate([16, 52, 18, 21, 24], start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.freeze_panes = "A7"
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:5"
    ws.oddFooter.center.text = "Página &P de &N"
    ws.oddFooter.right.text = "Generado con Lempis"
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
        ["Ingresos", format_money(context["revenue_period"], context["tenant"].currency)],
        ["Utilidad bruta estimada", format_money(context["profit_total"], context["tenant"].currency)],
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
            format_money(p.revenue, context["tenant"].currency),
            format_money(p.profit, context["tenant"].currency),
        ])
    story.append(_pdf_table(product_rows, [70 * mm, 25 * mm, 35 * mm, 35 * mm], header=True))
    story += [Spacer(1, 12), Paragraph("Top clientes", styles["Heading2"])]
    customer_rows = [["Cliente", "Facturas", "Total"]]
    for c in context["top_customers"]:
        customer_rows.append([c.name[:42], str(c.count), format_money(c.revenue, context["tenant"].currency)])
    story.append(_pdf_table(customer_rows, [90 * mm, 30 * mm, 45 * mm], header=True))
    doc.build(story)
    stream.seek(0)
    return stream


def _build_custom_pdf_report(context):
    if context["report_type"] == "ventas_categoria":
        return _build_category_pdf_report(context)
    if context["report_type"] == "inventario":
        return _build_inventory_pdf_report(context)
    if context["report_type"] == "ventas_producto":
        return _build_product_sales_pdf_report(context)
    stream = BytesIO()
    doc = SimpleDocTemplate(
        stream,
        pagesize=letter,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )
    styles = _report_pdf_styles()
    story = _report_pdf_header(context, styles)
    visible_cols = context["columns"][:7]
    data = [[label for _, label, _ in visible_cols]]
    for row in context["rows"]:
        data.append([_format_report_value(row.get(key), kind, context["tenant"].currency) for key, _, kind in visible_cols])
    if len(data) == 1:
        data.append(["Sin datos"] + [""] * (len(visible_cols) - 1))
    widths = _pdf_widths(len(visible_cols))
    story.append(_pdf_table(data, widths, header=True))
    if context["totals"]:
        total_values = []
        for index, (key, _, kind) in enumerate(visible_cols):
            if key in context["totals"]:
                total_values.append(_format_report_value(context["totals"][key], kind, context["tenant"].currency))
            else:
                total_values.append("TOTALES" if index == 0 else "")
        story += [Spacer(1, 8), _pdf_total_band(total_values, widths)]
    decorator = _pdf_page_decorator(context["tenant"])
    doc.build(story, onFirstPage=decorator, onLaterPages=decorator)
    stream.seek(0)
    return stream


def _build_category_pdf_report(context):
    stream = BytesIO()
    doc = SimpleDocTemplate(
        stream,
        pagesize=letter,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )
    styles = _report_pdf_styles()
    tenant = context["tenant"]
    story = _report_pdf_header(context, styles)
    headers = ["Código", "Descripción", "Unidad", "Cantidad", "Costo prom.", "Precio prom.", "Total vendido"]
    widths = [20 * mm, 48 * mm, 17 * mm, 21 * mm, 26 * mm, 26 * mm, 28 * mm]
    for category_name, category_rows in _category_report_groups(context["rows"]):
        story += [
            Spacer(1, 10),
            Paragraph(f"CATEGORÍA: {escape(category_name.upper())}", styles["ReportSection"]),
        ]
        data = [headers]
        for row in category_rows:
            data.append([
                str(row["codigo"] or "—"),
                str(row["descripcion"] or ""),
                row["unidad"],
                _format_report_value(row["cantidad"], "number", tenant.currency),
                format_money(row["costo_promedio"], tenant.currency),
                format_money(row["precio_promedio"], tenant.currency),
                format_money(row["total_vendido"], tenant.currency),
            ])
        story.append(_pdf_table(data, widths, header=True))
        category_quantity = sum(float(row.get("cantidad") or 0) for row in category_rows)
        category_total = sum(float(row.get("total_vendido") or 0) for row in category_rows)
        story.append(_pdf_total_band(
            [
                f"TOTAL {category_name.upper()}", "", "",
                _format_report_value(category_quantity, "number", tenant.currency),
                "", "", format_money(category_total, tenant.currency),
            ],
            widths,
        ))
    if not context["rows"]:
        story += [Spacer(1, 16), Paragraph("Sin datos para los filtros seleccionados.", styles["Normal"])]
    if context["rows"]:
        story += [
            Spacer(1, 10),
            Paragraph(
                f"Cantidad total: {_format_report_value(context['totals'].get('cantidad'), 'number', tenant.currency)}"
                f" &nbsp;&nbsp; Total vendido: {escape(format_money(context['totals'].get('total_vendido'), tenant.currency))}",
                styles["Heading3"],
            ),
        ]
    decorator = _pdf_page_decorator(tenant)
    doc.build(story, onFirstPage=decorator, onLaterPages=decorator)
    stream.seek(0)
    return stream


def _build_inventory_pdf_report(context):
    stream = BytesIO()
    doc = SimpleDocTemplate(
        stream,
        pagesize=letter,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )
    styles = _report_pdf_styles()
    tenant = context["tenant"]
    story = _report_pdf_header(context, styles)
    headers = ["SKU", "Nombre del producto", "Stock", "Costo unitario", "Valor inventario"]
    widths = [20 * mm, 78 * mm, 24 * mm, 30 * mm, 34 * mm]
    for category_name, category_rows in _category_report_groups(context["rows"]):
        story += [
            Spacer(1, 10),
            Paragraph(f"CATEGORÍA: {escape(category_name.upper())}", styles["ReportSection"]),
        ]
        data = [headers]
        for row in category_rows:
            data.append([
                str(row["sku"] or "—"),
                str(row["producto"] or ""),
                _format_report_value(row["stock"], "number", tenant.currency),
                format_money(row["costo"], tenant.currency),
                format_money(row["valor_inventario"], tenant.currency),
            ])
        story.append(_pdf_table(data, widths, header=True))
        category_stock = sum(float(row.get("stock") or 0) for row in category_rows)
        category_value = sum(float(row.get("valor_inventario") or 0) for row in category_rows)
        story.append(_pdf_total_band(
            [
                f"TOTAL {category_name.upper()}", "",
                _format_report_value(category_stock, "number", tenant.currency),
                "", format_money(category_value, tenant.currency),
            ],
            widths,
        ))
    if not context["rows"]:
        story += [Spacer(1, 16), Paragraph("Sin datos para los filtros seleccionados.", styles["Normal"])]
    else:
        story += [
            Spacer(1, 10),
            _pdf_total_band(
                [
                    "TOTAL GENERAL", "",
                    _format_report_value(context["totals"].get("stock"), "number", tenant.currency),
                    "", format_money(context["totals"].get("valor_inventario"), tenant.currency),
                ],
                widths,
            ),
        ]
    decorator = _pdf_page_decorator(tenant)
    doc.build(story, onFirstPage=decorator, onLaterPages=decorator)
    stream.seek(0)
    return stream


def _build_product_sales_pdf_report(context):
    stream = BytesIO()
    doc = SimpleDocTemplate(
        stream,
        pagesize=letter,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )
    styles = _report_pdf_styles()
    tenant = context["tenant"]
    story = _report_pdf_header(context, styles)
    columns = context["columns"]
    widths = [25 * mm, 20 * mm, 40 * mm, 49 * mm, 16 * mm, 18 * mm, 18 * mm]
    data = [[label for _, label, _ in columns]]
    for row in context["rows"]:
        data.append([
            _format_report_value(row.get(key), kind, tenant.currency)
            for key, _, kind in columns
        ])
    if len(data) == 1:
        data.append(["Sin ventas para los filtros seleccionados."] + [""] * (len(columns) - 1))
    story.append(_pdf_table(data, widths, header=True))
    if context["rows"]:
        story += [
            Spacer(1, 8),
            _pdf_total_band(
                [
                    "TOTALES", "", "", "",
                    _format_report_value(context["totals"].get("cantidad"), "number", tenant.currency),
                    "", format_money(context["totals"].get("total_linea"), tenant.currency),
                ],
                widths,
            ),
        ]
    decorator = _pdf_page_decorator(tenant)
    doc.build(story, onFirstPage=decorator, onLaterPages=decorator)
    stream.seek(0)
    return stream


def _report_pdf_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportCompany",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=17,
        textColor=colors.HexColor("#0F172A"),
        alignment=1,
        spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        name="ReportMeta",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#64748B"),
        alignment=1,
    ))
    styles.add(ParagraphStyle(
        name="ReportTitle",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=colors.white,
        alignment=1,
    ))
    styles.add(ParagraphStyle(
        name="ReportFilter",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#334155"),
        alignment=1,
    ))
    styles.add(ParagraphStyle(
        name="ReportSection",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=12,
        textColor=colors.HexColor("#1E3A8A"),
        backColor=colors.HexColor("#DBEAFE"),
        borderPadding=6,
        spaceAfter=4,
    ))
    return styles


def _pdf_logo(path, max_width=28 * mm, max_height=20 * mm):
    if not path:
        return ""
    try:
        width, height = ImageReader(path).getSize()
        scale = min(max_width / width, max_height / height)
        return Image(path, width=width * scale, height=height * scale)
    except Exception:
        return ""


def _report_pdf_header(context, styles):
    tenant = context["tenant"]
    company_name = tenant.legal_name or tenant.name
    identity = " · ".join(part for part in [
        f"RTN: {tenant.tax_id}" if tenant.tax_id else "",
        f"Tel: {tenant.phone}" if tenant.phone else "",
        tenant.email or "",
    ] if part) or "Reporte empresarial"
    company_logo = _pdf_logo(_resolve_report_logo_path(tenant))
    if not company_logo:
        company_logo = Paragraph("EMPRESA", styles["ReportMeta"])
    lempis_logo = _pdf_logo(_resolve_report_logo_path(lempis=True))
    lempis_brand = Table(
        [[
            lempis_logo,
            [
                Paragraph("<b>Lempis</b>", styles["ReportMeta"]),
                Paragraph("SISTEMA DE<br/>FACTURACIÓN", styles["ReportMeta"]),
            ],
        ]],
        colWidths=[15 * mm, 29 * mm],
    )
    lempis_brand.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    brand = Table(
        [[
            company_logo,
            [
                Paragraph(escape(company_name.upper()), styles["ReportCompany"]),
                Paragraph(escape(identity), styles["ReportMeta"]),
            ],
            lempis_brand,
        ]],
        colWidths=[28 * mm, 112 * mm, 46 * mm],
    )
    brand.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (-1, 0), (-1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.8, colors.HexColor("#CBD5E1")),
    ]))
    title_band = Table(
        [[Paragraph(escape(context["report_title"].upper()), styles["ReportTitle"])]],
        colWidths=[186 * mm],
    )
    title_band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1E3A8A")),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    filter_band = Table(
        [[Paragraph(escape(_report_filter_label(context)), styles["ReportFilter"])]],
        colWidths=[186 * mm],
    )
    filter_band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EFF6FF")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#BFDBFE")),
    ]))
    return [brand, Spacer(1, 7), title_band, filter_band, Spacer(1, 10)]


def _pdf_page_decorator(tenant):
    generated_at = local_now(tenant).strftime("%d/%m/%Y %H:%M")

    def draw_footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
        canvas.line(12 * mm, 9 * mm, doc.pagesize[0] - 12 * mm, 9 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(12 * mm, 5.5 * mm, f"Generado el {generated_at}")
        canvas.drawRightString(
            doc.pagesize[0] - 12 * mm,
            5.5 * mm,
            f"Página {doc.page} · Generado con Lempis",
        )
        canvas.restoreState()

    return draw_footer


def _pdf_total_band(values, widths):
    table = Table([values], colWidths=widths)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#DBEAFE")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#172554")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#93C5FD")),
    ]))
    return table


def _category_report_groups(rows):
    groups = []
    by_category = {}
    for row in rows:
        category_name = row.get("categoria") or "Sin categoría"
        if category_name not in by_category:
            category_rows = []
            by_category[category_name] = category_rows
            groups.append((category_name, category_rows))
        by_category[category_name].append(row)
    return groups


def _pdf_table(data, col_widths, header=False):
    cell_style = ParagraphStyle(
        "ReportTableCell",
        fontName="Helvetica",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#1E293B"),
    )
    header_style = ParagraphStyle(
        "ReportTableHeader",
        parent=cell_style,
        fontName="Helvetica-Bold",
        textColor=colors.white,
        alignment=1,
    )
    prepared_data = []
    for row_index, row in enumerate(data):
        prepared_data.append([
            value if hasattr(value, "wrap") else Paragraph(
                escape("" if value is None else str(value)),
                header_style if header and row_index == 0 else cell_style,
            )
            for value in row
        ])
    table = Table(prepared_data, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("LINEBELOW", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F766E")),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#115E59")),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
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
        "cheque": "Cheque",
        "credito": "Crédito",
    }.get(value, value or "")


def _status_label(value):
    return {
        "issued": "Emitida",
        "paid": "Pagada",
        "partially_paid": "Pago parcial",
        "overdue": "Vencida",
    }.get(value, value or "")


def _totals(rows, keys):
    return {key: sum(float(row.get(key) or 0) for row in rows) for key in keys}


def _format_report_value(value, kind, currency):
    if kind == "money":
        return format_money(value, currency)
    if kind == "number":
        return f"{float(value or 0):,.2f}".rstrip("0").rstrip(".")
    return "" if value is None else str(value)


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
