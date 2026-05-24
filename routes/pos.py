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
from services.tenant_context import current_tenant
from services.permissions import permission_required, tenant_required
from services.invoice_service import issue_invoice, validate_can_emit, CAIError, next_invoice_number

pos_bp = Blueprint("pos", __name__, url_prefix="/app/venta")


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

    productos_q = Product.query.filter_by(tenant_id=tenant.id, is_active=True)
    if q:
        like = f"%{q}%"
        productos_q = productos_q.filter(or_(
            Product.name.ilike(like),
            Product.sku.ilike(like),
        ))
    if category_id:
        productos_q = productos_q.filter_by(category_id=category_id)
    productos = productos_q.order_by(Product.name.asc()).all()

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
    """Emite la factura con los items del carrito enviados via form."""
    tenant = current_tenant()
    try:
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
        notes = _notes_with_cash_details(request.form.get("notes", ""), payment_method)

        inv = issue_invoice(
            tenant=tenant,
            customer=customer,
            items_data=items_data,
            payment_method=payment_method,
            payment_terms_days=int(request.form.get("payment_terms_days") or 0),
            notes=notes,
            issued_by_user_id=current_user.id,
            status="issued",
        )
        flash(f"Venta {inv.number} emitida correctamente.", "success")
        detail_args = {"invoice_id": inv.id}
        if request.form.get("print_invoice") == "1":
            detail_args["print"] = 1
        return redirect(url_for("facturas.detail", **detail_args))
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
            "tax_rate": taxes[i] if i < len(taxes) else 15,
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
