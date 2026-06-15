"""
POS — Pantalla de Nueva Venta estilo Treinta.
Layout dual: grid de productos a la izquierda, carrito a la derecha.
"""
from __future__ import annotations

from decimal import Decimal
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify
)
from flask_login import login_required, current_user
from sqlalchemy import or_

from models import db
from models.catalog import Product, Category, Customer
from models.printing import TenantPrintSettings
from services.tenant_context import current_tenant
from services.permissions import permission_required, tenant_required
from services.invoice_service import issue_invoice, validate_can_emit, CAIError, next_invoice_number
from services.locations import (
    can_user_sell_from_warehouse,
    sale_warehouses_for_user,
    stock_map_for_warehouse,
    sync_default_warehouse_stock,
)

pos_bp = Blueprint("pos", __name__, url_prefix="/app/venta")


def _get_print_settings(tenant):
    settings = TenantPrintSettings.query.filter_by(tenant_id=tenant.id).first()
    if settings is None:
        settings = TenantPrintSettings(tenant_id=tenant.id)
        db.session.add(settings)
        db.session.flush()
    return settings


@pos_bp.route("/")
@login_required
@tenant_required
@permission_required("sales.create")
def index():
    return redirect(url_for("pos.quick_sale"))


@pos_bp.route("/rapida")
@login_required
@tenant_required
@permission_required("sales.create")
def quick_sale():
    tenant = current_tenant()
    q = (request.args.get("q") or "").strip()
    category_id = request.args.get("category_id", type=int)
    default_warehouse = sync_default_warehouse_stock(tenant)
    warehouses = sale_warehouses_for_user(tenant.id, current_user) or [default_warehouse]
    selected_warehouse_id = request.args.get("warehouse_id", type=int) or warehouses[0].id
    if selected_warehouse_id not in {w.id for w in warehouses}:
        selected_warehouse_id = warehouses[0].id

    productos_q = Product.query.filter_by(tenant_id=tenant.id, is_active=True)
    if q:
        like = f"%{q}%"
        productos_q = productos_q.filter(or_(
            Product.name.ilike(like),
            Product.sku.ilike(like),
        ))
    productos = productos_q.order_by(Product.name.asc()).all()
    stock_map = stock_map_for_warehouse(tenant.id, selected_warehouse_id)
    for product in productos:
        product.warehouse_stock = int(stock_map.get(product.id, 0))

    categorias = Category.query.filter_by(tenant_id=tenant.id).order_by(Category.name).all()
    clientes = Customer.query.filter_by(tenant_id=tenant.id, is_active=True).order_by(Customer.name).all()
    _, next_number = next_invoice_number(tenant)

    # Validar antes de mostrar el form
    can_emit = True
    reason = None
    try:
        validate_can_emit(tenant)
    except CAIError as e:
        can_emit = False
        reason = str(e)

    return render_template(
        "pos/index.html",
        productos=productos, categorias=categorias, clientes=clientes,
        q=q, selected_category=category_id,
        tenant=tenant, next_number=next_number,
        can_emit=can_emit, reason=reason,
        warehouses=warehouses, selected_warehouse_id=selected_warehouse_id,
    )


@pos_bp.route("/detallada")
@login_required
@tenant_required
@permission_required("sales.create")
def detailed_sale():
    return redirect(url_for("facturas.new"))


@pos_bp.route("/cobrar", methods=["POST"])
@login_required
@tenant_required
@permission_required("sales.create")
def cobrar():
    """Guarda o emite la factura con los items del carrito enviados via form."""
    tenant = current_tenant()
    try:
        action = request.form.get("action", "charge")
        items_data = _parse_cart()
        if not items_data:
            flash("Agrega al menos un producto al carrito.", "warning")
            return redirect(url_for("pos.quick_sale"))

        customer_id = request.form.get("customer_id")
        customer = None
        if customer_id:
            customer = Customer.query.filter_by(
                id=customer_id, tenant_id=tenant.id,
            ).first()

        payment_method = request.form.get("payment_method", "efectivo")
        default_warehouse = sync_default_warehouse_stock(tenant)
        warehouses = sale_warehouses_for_user(tenant.id, current_user) or [default_warehouse]
        allowed_warehouse_ids = {w.id for w in warehouses}
        warehouse_id = request.form.get("warehouse_id", type=int)
        if warehouse_id not in allowed_warehouse_ids:
            warehouse_id = warehouses[0].id if warehouses else None
        if not can_user_sell_from_warehouse(tenant.id, current_user, warehouse_id):
            flash("No puedes facturar desde una bodega de otra sede.", "warning")
            return redirect(url_for("pos.quick_sale"))
        notes = _notes_with_cash_details(request.form.get("notes", ""), payment_method)
        status = "draft" if action == "draft" else "issued"

        inv = issue_invoice(
            tenant=tenant,
            customer=customer,
            items_data=items_data,
            payment_method=payment_method,
            payment_terms_days=int(request.form.get("payment_terms_days") or 0),
            notes=notes,
            issued_by_user_id=current_user.id,
            status=status,
            warehouse_id=warehouse_id,
        )
        if status == "draft":
            flash("Factura guardada como borrador. Puedes recuperarla en Facturas > Borradores.", "success")
            return redirect(url_for("pos.quick_sale"))

        flash(f"Venta {inv.number} emitida correctamente.", "success")
        next_sale_url = url_for("pos.quick_sale")
        if request.form.get("print_invoice") == "1":
            settings = _get_print_settings(tenant)
            if settings.thermal_printer_enabled and settings.quick_sale_format == "thermal_receipt":
                return redirect(url_for(
                    "facturas.ticket",
                    invoice_id=inv.id,
                    print=1,
                    drawer=1 if settings.open_cash_drawer_on_print else 0,
                    next=next_sale_url,
                ))
            return redirect(url_for("facturas.detail", invoice_id=inv.id, print=1, next=next_sale_url))
        return redirect(next_sale_url)
    except CAIError as e:
        flash(str(e), "danger")
        return redirect(url_for("pos.quick_sale"))


def _parse_cart() -> list[dict]:
    """Lee los items del POST y devuelve lista de dicts compatibles con issue_invoice."""
    items = []
    product_ids = request.form.getlist("cart_product_id[]")
    quantities = request.form.getlist("cart_quantity[]")
    prices = request.form.getlist("cart_unit_price[]")
    taxes = request.form.getlist("cart_tax_rate[]")
    names = request.form.getlist("cart_name[]")

    for i, pid in enumerate(product_ids):
        if not pid:
            continue
        items.append({
            "product_id": int(pid),
            "description": names[i] if i < len(names) else "",
            "quantity": quantities[i] if i < len(quantities) else 1,
            "unit_price": prices[i] if i < len(prices) else 0,
            "tax_rate": taxes[i] if i < len(taxes) else 0,
            "discount_amount": 0,
            "batch_id": None,  # FIFO automático
        })
    return items


def _notes_with_cash_details(notes: str, payment_method: str) -> str:
    """Agrega recibido/cambio a las notas de la factura cuando la venta es en efectivo."""
    base = (notes or "").strip()
    if payment_method != "efectivo":
        return base

    received = _money_or_none(request.form.get("cash_received"))
    change = _money_or_none(request.form.get("cash_change"))
    if received is None:
        return base

    cash_lines = [f"Efectivo recibido: {received:.2f}"]
    if change is not None:
        cash_lines.append(f"Cambio entregado: {change:.2f}")

    cash_note = "\n".join(cash_lines)
    return f"{base}\n\n{cash_note}" if base else cash_note


def _money_or_none(value: str | None) -> Decimal | None:
    try:
        return Decimal(str(value or "").strip()).quantize(Decimal("0.01"))
    except Exception:
        return None
