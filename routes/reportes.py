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
from models.cash import CashClosure, CashExpense
from models.invoice import Invoice, InvoiceItem
from models.catalog import Product, Customer, Category, ProductBatch
from models.locations import Branch, Warehouse, WarehouseStock
from services.datetime_utils import format_local_datetime, local_date_range_to_utc, local_now, tenant_today
from services.currency import format_money
from services.tenant_context import current_tenant
from services.permissions import permission_required, tenant_required
from services.locations import active_warehouses, sync_default_warehouse_stock

reportes_bp = Blueprint("reportes", __name__, url_prefix="/app/reportes")

REPORT_OPTIONS = [
    ("ventas_contado", "Reporte de ventas de contado", "Ventas pagadas en efectivo, transferencia o tarjeta."),
    ("ventas_credito", "Reporte de ventas de crédito", "Facturas emitidas a crédito y sus saldos."),
    ("ventas_venta", "Ventas por venta", "Detalle individual de cada venta realizada en el periodo."),
    ("ventas_producto", "Ventas por producto", "Cantidades vendidas, valores y utilidad estimada por producto."),
    ("ventas_categoria", "Ventas por categoría", "Cantidades vendidas, valores y utilidad estimada por categoría."),
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
    warehouse_id = request.args.get("warehouse_id", type=int) or None
    product_id = request.args.get("product_id", type=int) or None
    category_id = request.args.get("category_id", type=int) or None
    columns, rows, totals = _custom_report_data(
        tenant,
        report_type,
        start_date,
        end_date,
        warehouse_id,
        product_id,
        category_id,
    )
    warehouses = active_warehouses(tenant.id)
    selected_warehouse = next((w for w in warehouses if w.id == warehouse_id), None)
    products = (
        Product.query.filter_by(tenant_id=tenant.id, is_active=True)
        .order_by(Product.name.asc())
        .all()
    )
    categories = (
        Category.query.filter_by(tenant_id=tenant.id)
        .order_by(Category.name.asc())
        .all()
    )
    selected_product = next((p for p in products if p.id == product_id), None)
    selected_category = next((c for c in categories if c.id == category_id), None)
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
            "ventas_venta",
            "ventas_producto",
            "ventas_categoria",
            "cierres_caja",
        },
        warehouses=warehouses,
        warehouse_id=warehouse_id,
        selected_warehouse=selected_warehouse,
        products=products,
        product_id=product_id,
        selected_product=selected_product,
        categories=categories,
        category_id=category_id,
        selected_category=selected_category,
    )


def _custom_report_data(
    tenant,
    report_type,
    start_date,
    end_date,
    warehouse_id=None,
    product_id=None,
    category_id=None,
):
    if not report_type:
        return [], [], {}
    period_start, period_end = local_date_range_to_utc(start_date, end_date, tenant)
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

    if report_type == "ventas_venta":
        item_totals = (
            db.session.query(
                InvoiceItem.invoice_id.label("invoice_id"),
                func.count(InvoiceItem.id).label("lineas"),
                func.coalesce(func.sum(InvoiceItem.quantity), 0).label("cantidad"),
                func.coalesce(
                    func.sum(
                        InvoiceItem.subtotal
                        - (InvoiceItem.quantity * func.coalesce(Product.cost, 0))
                    ),
                    0,
                ).label("utilidad"),
            )
            .outerjoin(Product, Product.id == InvoiceItem.product_id)
            .filter(InvoiceItem.tenant_id == tenant.id)
            .group_by(InvoiceItem.invoice_id)
            .subquery()
        )
        sales = (
            db.session.query(
                Invoice,
                Branch.name.label("sede"),
                func.coalesce(item_totals.c.lineas, 0).label("lineas"),
                func.coalesce(item_totals.c.cantidad, 0).label("cantidad"),
                func.coalesce(item_totals.c.utilidad, 0).label("utilidad"),
            )
            .outerjoin(Warehouse, Warehouse.id == Invoice.warehouse_id)
            .outerjoin(Branch, Branch.id == Warehouse.branch_id)
            .outerjoin(item_totals, item_totals.c.invoice_id == Invoice.id)
            .filter(
                Invoice.tenant_id == tenant.id,
                Invoice.status.in_(valid_statuses),
                Invoice.issue_date >= period_start,
                Invoice.issue_date < period_end,
            )
            .order_by(Invoice.issue_date.desc(), Invoice.number.desc())
            .all()
        )
        rows = []
        for invoice, branch_name, lines, quantity, profit in sales:
            rows.append({
                "fecha": format_local_datetime(invoice.issue_date, "%d/%m/%Y %H:%M", tenant),
                "factura": invoice.number,
                "cliente": invoice.customer.name if invoice.customer else (invoice.receptor_name or "Consumidor final"),
                "sede": branch_name or "Sin sede",
                "tipo": "Crédito" if invoice.payment_method == "credito" else "Contado",
                "metodo": _payment_label(invoice.payment_method),
                "total": float(invoice.total or 0),
                "lineas": int(lines or 0),
                "cantidad": float(quantity or 0),
                "subtotal": float(invoice.subtotal or 0),
                "descuento": float(invoice.discount_total or 0),
                "impuesto": float(invoice.tax_total or 0),
                "abonado": float(invoice.amount_paid or 0),
                "saldo": float(invoice.amount_due or 0),
                "utilidad": float(profit or 0),
                "estado": _status_label(invoice.status),
            })
        return (
            [
                ("fecha", "Fecha", "text"), ("factura", "Factura", "text"),
                ("cliente", "Cliente", "text"), ("sede", "Sede", "text"),
                ("tipo", "Tipo", "text"), ("metodo", "Método", "text"),
                ("total", "Total", "money"), ("lineas", "Productos", "number"),
                ("cantidad", "Cantidad", "number"), ("subtotal", "Subtotal", "money"),
                ("descuento", "Descuento", "money"), ("impuesto", "Impuesto", "money"),
                ("abonado", "Abonado", "money"), ("saldo", "Saldo", "money"),
                ("utilidad", "Utilidad est.", "money"), ("estado", "Estado", "text"),
            ],
            rows,
            _totals(rows, [
                "lineas", "cantidad", "subtotal", "descuento", "impuesto",
                "total", "abonado", "saldo", "utilidad",
            ]),
        )

    if report_type == "ventas_producto":
        product_name = func.coalesce(Product.name, InvoiceItem.description, "Producto sin catálogo")
        query = (
            db.session.query(
                product_name.label("producto"),
                func.coalesce(Product.sku, "").label("sku"),
                func.coalesce(Category.name, "Sin categoría").label("categoria"),
                func.coalesce(func.sum(InvoiceItem.quantity), 0).label("cantidad"),
                func.coalesce(func.sum(InvoiceItem.subtotal), 0).label("ventas"),
                func.coalesce(
                    func.sum(InvoiceItem.quantity * func.coalesce(Product.cost, 0)),
                    0,
                ).label("costo_estimado"),
                func.coalesce(
                    func.sum(
                        InvoiceItem.subtotal
                        - (InvoiceItem.quantity * func.coalesce(Product.cost, 0))
                    ),
                    0,
                ).label("utilidad"),
            )
            .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
            .outerjoin(Product, Product.id == InvoiceItem.product_id)
            .outerjoin(Category, Category.id == Product.category_id)
            .filter(
                InvoiceItem.tenant_id == tenant.id,
                Invoice.status.in_(valid_statuses),
                Invoice.issue_date >= period_start,
                Invoice.issue_date < period_end,
            )
        )
        if product_id:
            query = query.filter(Product.id == product_id)
        rows_raw = (
            query.group_by(product_name, Product.sku, Category.name)
            .order_by(func.sum(InvoiceItem.subtotal).desc())
            .all()
        )
        rows = []
        for item in rows_raw:
            quantity = float(item.cantidad or 0)
            sales_value = float(item.ventas or 0)
            rows.append({
                "producto": item.producto,
                "sku": item.sku or "",
                "categoria": item.categoria,
                "cantidad": quantity,
                "ventas": sales_value,
                "precio_promedio": (sales_value / quantity) if quantity else 0,
                "costo_estimado": float(item.costo_estimado or 0),
                "utilidad": float(item.utilidad or 0),
            })
        return (
            [
                ("producto", "Producto", "text"), ("sku", "SKU", "text"),
                ("categoria", "Categoría", "text"), ("cantidad", "Cantidad", "number"),
                ("ventas", "Valor vendido", "money"), ("precio_promedio", "Precio prom.", "money"),
                ("costo_estimado", "Costo estimado", "money"), ("utilidad", "Utilidad", "money"),
            ],
            rows,
            _totals(rows, ["cantidad", "ventas", "costo_estimado", "utilidad"]),
        )

    if report_type == "ventas_categoria":
        category_name = func.coalesce(Category.name, "Sin categoría")
        query = (
            db.session.query(
                category_name.label("categoria"),
                func.count(func.distinct(InvoiceItem.product_id)).label("productos"),
                func.coalesce(func.sum(InvoiceItem.quantity), 0).label("cantidad"),
                func.coalesce(func.sum(InvoiceItem.subtotal), 0).label("ventas"),
                func.coalesce(
                    func.sum(InvoiceItem.quantity * func.coalesce(Product.cost, 0)),
                    0,
                ).label("costo_estimado"),
                func.coalesce(
                    func.sum(
                        InvoiceItem.subtotal
                        - (InvoiceItem.quantity * func.coalesce(Product.cost, 0))
                    ),
                    0,
                ).label("utilidad"),
            )
            .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
            .outerjoin(Product, Product.id == InvoiceItem.product_id)
            .outerjoin(Category, Category.id == Product.category_id)
            .filter(
                InvoiceItem.tenant_id == tenant.id,
                Invoice.status.in_(valid_statuses),
                Invoice.issue_date >= period_start,
                Invoice.issue_date < period_end,
            )
        )
        if category_id:
            query = query.filter(Category.id == category_id)
        rows_raw = (
            query.group_by(category_name)
            .order_by(func.sum(InvoiceItem.subtotal).desc())
            .all()
        )
        rows = []
        for item in rows_raw:
            quantity = float(item.cantidad or 0)
            sales_value = float(item.ventas or 0)
            rows.append({
                "categoria": item.categoria,
                "productos": int(item.productos or 0),
                "cantidad": quantity,
                "ventas": sales_value,
                "precio_promedio": (sales_value / quantity) if quantity else 0,
                "costo_estimado": float(item.costo_estimado or 0),
                "utilidad": float(item.utilidad or 0),
            })
        return (
            [
                ("categoria", "Categoría", "text"), ("productos", "Productos", "number"),
                ("cantidad", "Cantidad", "number"), ("ventas", "Valor vendido", "money"),
                ("precio_promedio", "Precio prom.", "money"),
                ("costo_estimado", "Costo estimado", "money"),
                ("utilidad", "Utilidad", "money"),
            ],
            rows,
            _totals(rows, ["productos", "cantidad", "ventas", "costo_estimado", "utilidad"]),
        )

    if report_type == "cierres_caja":
        closures = (
            CashClosure.query
            .filter(
                CashClosure.tenant_id == tenant.id,
                CashClosure.closure_date >= start_date,
                CashClosure.closure_date <= end_date,
                CashClosure.status == "closed",
            )
            .order_by(CashClosure.closure_date.desc(), CashClosure.id.desc())
            .all()
        )
        expenses_total = (
            db.session.query(func.coalesce(func.sum(CashExpense.amount), 0))
            .filter(
                CashExpense.tenant_id == tenant.id,
                CashExpense.expense_date >= period_start,
                CashExpense.expense_date < period_end,
            )
            .scalar()
        )
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
            "diferencia": float(c.variance_amount or 0),
            "notas": c.notes or "",
        } for c in closures]
        totals = _totals(rows, [
            "apertura", "efectivo", "transferencia", "tarjeta", "credito",
            "gastos", "esperado", "contado", "entregado", "diferencia",
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
                ("diferencia", "Diferencia", "money"), ("notas", "Notas", "text"),
            ],
            rows,
            totals,
        )

    if report_type == "inventario":
        query = (
            db.session.query(WarehouseStock, Product, Warehouse, Branch)
            .join(Product, Product.id == WarehouseStock.product_id)
            .join(Warehouse, Warehouse.id == WarehouseStock.warehouse_id)
            .join(Branch, Branch.id == Warehouse.branch_id)
            .filter(WarehouseStock.tenant_id == tenant.id)
        )
        if warehouse_id:
            query = query.filter(WarehouseStock.warehouse_id == warehouse_id)
        stock_rows = query.order_by(Branch.name.asc(), Warehouse.name.asc(), Product.name.asc()).all()
        rows = [{
            "sede": branch.name,
            "bodega": warehouse.name,
            "sku": product.sku,
            "producto": product.name,
            "categoria": product.category.name if product.category else "Sin categoría",
            "lote": stock.batch.batch_number if stock.batch else "",
            "stock": float(stock.quantity or 0),
            "costo": float(product.cost or 0),
            "valor_costo": float((stock.quantity or 0) * (product.cost or 0)),
            "activo": "Activo" if product.is_active else "Inactivo",
        } for stock, product, warehouse, branch in stock_rows]
        return (
            [
                ("sede", "Sede", "text"), ("bodega", "Bodega", "text"),
                ("sku", "SKU", "text"), ("producto", "Producto", "text"),
                ("categoria", "Categoría", "text"), ("lote", "Lote", "text"),
                ("stock", "Stock", "number"), ("costo", "Costo", "money"),
                ("valor_costo", "Valor costo", "money"), ("activo", "Estado", "text"),
            ],
            rows,
            _totals(rows, ["stock", "valor_costo"]),
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
        if warehouse_id:
            query = query.filter(WarehouseStock.warehouse_id == warehouse_id)
        batches = query.order_by(ProductBatch.expiration_date.asc()).all()
        rows = [{
            "sede": branch.name,
            "bodega": warehouse.name,
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
                ("sede", "Sede", "text"), ("bodega", "Bodega", "text"),
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
        return f"{float(value or 0):.2f}".rstrip("0").rstrip(".")
    return "" if value is None else str(value)


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
