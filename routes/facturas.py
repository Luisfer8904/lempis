"""
CRUD de Facturas con líneas de detalle.
- Numeración automática SAR (EST-PV-TD-CORRELATIVO)
- Métodos de pago: efectivo, transferencia, tarjeta, crédito
- Estados: draft, issued, paid, partially_paid, void, overdue
"""
from __future__ import annotations  # type hints lazy (evita conflicto con la ruta list())

from datetime import datetime
from decimal import Decimal

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort, send_file
)
from flask_login import login_required, current_user
from sqlalchemy import or_

from models import db
from models.invoice import Invoice
from models.catalog import Customer, Product
from models.country import TaxConfig
from services.tenant_context import current_tenant
from services.permissions import permission_required, tenant_required
from services.invoice_service import (
    issue_invoice, update_invoice, next_invoice_number,
    validate_can_emit, CAIError,
)
from services.locations import active_warehouses, sync_default_warehouse_stock

facturas_bp = Blueprint("facturas", __name__, url_prefix="/app/facturas")


def _get_or_404(invoice_id: int) -> Invoice:
    tenant = current_tenant()
    inv = Invoice.query.filter_by(id=invoice_id, tenant_id=tenant.id).first()
    if inv is None:
        abort(404)
    return inv


# ---------------- LISTAR ----------------

@facturas_bp.route("/")
@login_required
@tenant_required
@permission_required("sales.view")
def list():
    tenant = current_tenant()
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status")

    query = Invoice.query.filter_by(tenant_id=tenant.id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Invoice.number.ilike(like),
            Invoice.receptor_name.ilike(like),
            Invoice.receptor_tax_id.ilike(like),
        ))
    if status:
        query = query.filter_by(status=status)

    facturas = query.order_by(Invoice.issue_date.desc()).all()

    # ¿Puede emitir según su modo? (CAI para SAR, plan limits para simple)
    can_emit_now = True
    try:
        validate_can_emit(tenant)
    except CAIError:
        can_emit_now = False

    return render_template(
        "facturas/list.html",
        facturas=facturas, q=q, status=status,
        cai_ok=can_emit_now,
    )


# ---------------- CREAR ----------------

@facturas_bp.route("/new", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("sales.create")
def new():
    tenant = current_tenant()

    if request.method == "POST":
        try:
            inv = _create_from_form(tenant)
            flash(f"Factura {inv.number} {'emitida' if inv.status == 'issued' else 'guardada como borrador'}.", "success")
            return redirect(_invoice_detail_url(inv))
        except CAIError as e:
            flash(str(e), "danger")
            return redirect(url_for("configuracion.facturacion"))

    clientes = Customer.query.filter_by(tenant_id=tenant.id, is_active=True).order_by(Customer.name).all()
    productos = Product.query.filter_by(tenant_id=tenant.id, is_active=True).order_by(Product.name).all()
    impuestos = TaxConfig.query.filter_by(country_code=tenant.country_code, is_active=True).all()
    default_warehouse = sync_default_warehouse_stock(tenant)
    warehouses = active_warehouses(tenant.id)
    correlativo, formatted = next_invoice_number(tenant)

    # Validar antes de mostrar el form
    can_emit = True
    reason = None
    try:
        validate_can_emit(tenant)
    except CAIError as e:
        can_emit = False
        reason = str(e)

    return render_template(
        "facturas/form.html",
        factura=None, clientes=clientes, productos=productos, impuestos=impuestos,
        next_number=formatted, tenant=tenant,
        can_emit=can_emit, reason=reason,
        warehouses=warehouses, selected_warehouse_id=default_warehouse.id,
    )


# ---------------- EDITAR (solo draft) ----------------

@facturas_bp.route("/<int:invoice_id>/edit", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("sales.create")
def edit(invoice_id):
    inv = _get_or_404(invoice_id)
    tenant = current_tenant()

    if inv.status != "draft":
        flash("Solo puedes editar facturas en borrador.", "warning")
        return redirect(url_for("facturas.detail", invoice_id=inv.id))

    if request.method == "POST":
        try:
            items = _parse_items()
            update_invoice(
                inv,
                items_data=items,
                payment_method=request.form.get("payment_method"),
                payment_terms_days=request.form.get("payment_terms_days", 0),
                notes=_notes_with_cash_details(
                    request.form.get("notes", ""),
                    request.form.get("payment_method", "efectivo"),
                ),
                status=request.form.get("status", "draft"),
            )
            inv.warehouse_id = request.form.get("warehouse_id", type=int) or inv.warehouse_id
            # Si pasó a issued, asignar número definitivo
            if request.form.get("action") == "emit":
                _emit_draft(inv, tenant)
            flash(f"Factura {inv.number} actualizada.", "success")
            return redirect(_invoice_detail_url(inv))
        except CAIError as e:
            flash(str(e), "danger")

    clientes = Customer.query.filter_by(tenant_id=tenant.id, is_active=True).order_by(Customer.name).all()
    productos = Product.query.filter_by(tenant_id=tenant.id, is_active=True).order_by(Product.name).all()
    impuestos = TaxConfig.query.filter_by(country_code=tenant.country_code, is_active=True).all()
    default_warehouse = sync_default_warehouse_stock(tenant)
    warehouses = active_warehouses(tenant.id)

    return render_template(
        "facturas/form.html",
        factura=inv, clientes=clientes, productos=productos, impuestos=impuestos,
        next_number=inv.number, tenant=tenant,
        can_emit=True, reason=None,
        warehouses=warehouses, selected_warehouse_id=inv.warehouse_id or default_warehouse.id,
    )


# ---------------- DETALLE ----------------

@facturas_bp.route("/<int:invoice_id>")
@login_required
@tenant_required
@permission_required("sales.view")
def detail(invoice_id):
    inv = _get_or_404(invoice_id)
    return render_template("facturas/detail.html", factura=inv, tenant=current_tenant())


# ---------------- CAMBIAR ESTADO ----------------

@facturas_bp.route("/<int:invoice_id>/mark-paid", methods=["POST"])
@login_required
@tenant_required
@permission_required("receivables.manage")
def mark_paid(invoice_id):
    inv = _get_or_404(invoice_id)
    if inv.status in ("issued", "partially_paid", "overdue"):
        inv.status = "paid"
        db.session.commit()
        flash(f"Factura {inv.number} marcada como pagada.", "success")
    return redirect(url_for("facturas.detail", invoice_id=inv.id))


@facturas_bp.route("/<int:invoice_id>/void", methods=["POST"])
@login_required
@tenant_required
@permission_required("sales.manage")
def void(invoice_id):
    from services.invoice_service import void_invoice_and_restore_stock
    inv = _get_or_404(invoice_id)
    void_invoice_and_restore_stock(inv)
    flash(f"Factura {inv.number} anulada. El stock de los lotes fue restituido.", "warning")
    return redirect(url_for("facturas.detail", invoice_id=inv.id))


@facturas_bp.route("/<int:invoice_id>/delete", methods=["POST"])
@login_required
@tenant_required
@permission_required("sales.manage")
def delete(invoice_id):
    inv = _get_or_404(invoice_id)
    if inv.status != "draft":
        flash("Solo puedes eliminar borradores. Las emitidas se anulan.", "warning")
        return redirect(url_for("facturas.detail", invoice_id=inv.id))
    db.session.delete(inv)
    db.session.commit()
    flash("Borrador eliminado.", "info")
    return redirect(url_for("facturas.list"))


# ---------------- AJAX: lotes de un producto ----------------

@facturas_bp.route("/api/productos/<int:product_id>/lotes")
@login_required
@tenant_required
@permission_required("sales.create")
def api_lotes_de_producto(product_id):
    """Devuelve JSON con los lotes disponibles de un producto (para el form de facturas)."""
    from flask import jsonify
    from models.catalog import Product, ProductBatch

    tenant = current_tenant()
    p = Product.query.filter_by(id=product_id, tenant_id=tenant.id).first()
    if p is None or not p.track_batches:
        return jsonify({"lotes": []})

    warehouse_id = request.args.get("warehouse_id", type=int)
    actives = p.active_batches()
    if warehouse_id:
        from models.locations import WarehouseStock
        actives = (
            db.session.query(ProductBatch)
            .join(WarehouseStock, WarehouseStock.batch_id == ProductBatch.id)
            .filter(
                ProductBatch.tenant_id == tenant.id,
                ProductBatch.product_id == p.id,
                WarehouseStock.warehouse_id == warehouse_id,
                WarehouseStock.quantity > 0,
                ProductBatch.remaining_quantity > 0,
            )
            .order_by(ProductBatch.expiration_date.asc(), ProductBatch.id.asc())
            .all()
        )
    return jsonify({
        "lotes": [{
            "id": b.id,
            "batch_number": b.batch_number,
            "expiration": b.expiration_date.strftime("%d/%m/%Y") if b.expiration_date else None,
            "remaining": float(b.remaining_quantity or 0),
            "status": b.status_label(),
        } for b in actives],
    })


# ---------------- PDF ----------------

@facturas_bp.route("/<int:invoice_id>/pdf")
@login_required
@tenant_required
@permission_required("sales.view")
def pdf(invoice_id):
    from services.invoice_mode import get_provider
    inv = _get_or_404(invoice_id)
    tenant = current_tenant()
    buffer = get_provider(tenant).generate_pdf(inv, tenant)
    safe_num = inv.number.replace("/", "_").replace("-", "_").replace(" ", "_")
    filename = f"factura_{safe_num}.pdf"
    return send_file(buffer, mimetype="application/pdf",
                     as_attachment=True, download_name=filename)


# ---------------- Helpers ----------------

def _parse_items() -> list[dict]:
    """Lee las líneas del formulario y devuelve lista de dicts."""
    items = []
    descriptions = request.form.getlist("item_description[]")
    products = request.form.getlist("item_product_id[]")
    batches = request.form.getlist("item_batch_id[]")
    quantities = request.form.getlist("item_quantity[]")
    prices = request.form.getlist("item_unit_price[]")
    taxes = request.form.getlist("item_tax_rate[]")
    discounts = request.form.getlist("item_discount[]")

    for i, desc in enumerate(descriptions):
        if not desc.strip():
            continue
        items.append({
            "product_id": int(products[i]) if (i < len(products) and products[i]) else None,
            "batch_id": int(batches[i]) if (i < len(batches) and batches[i]) else None,
            "description": desc,
            "quantity": quantities[i] if i < len(quantities) else 1,
            "unit_price": prices[i] if i < len(prices) else 0,
            "tax_rate": taxes[i] if i < len(taxes) else 0,
            "discount_amount": discounts[i] if i < len(discounts) else 0,
        })
    return items


def _create_from_form(tenant) -> Invoice:
    customer_id = request.form.get("customer_id")
    customer = Customer.query.filter_by(id=customer_id, tenant_id=tenant.id).first() if customer_id else None
    items = _parse_items()
    if not items:
        raise CAIError("Debes agregar al menos una línea a la factura.")

    action = request.form.get("action", "draft")
    status = "issued" if action == "emit" else "draft"

    payment_method = request.form.get("payment_method", "efectivo")

    return issue_invoice(
        tenant=tenant,
        customer=customer,
        items_data=items,
        payment_method=payment_method,
        payment_terms_days=request.form.get("payment_terms_days", 0),
        notes=_notes_with_cash_details(request.form.get("notes", ""), payment_method),
        issued_by_user_id=current_user.id,
        status=status,
        warehouse_id=request.form.get("warehouse_id", type=int),
    )


def _emit_draft(inv: Invoice, tenant) -> None:
    """Convierte un draft a emitido: asigna número definitivo + congela CAI."""
    validate_can_emit(tenant)
    correlativo, formatted = next_invoice_number(tenant)
    inv.number = formatted
    inv.status = "issued"
    inv.cai_code = tenant.cai_code
    inv.cai_range_start = tenant.cai_range_start
    inv.cai_range_end = tenant.cai_range_end
    inv.cai_valid_until = tenant.cai_valid_until
    inv.emisor_name = tenant.legal_name or tenant.name
    inv.emisor_tax_id = tenant.tax_id
    inv.emisor_address = tenant.address
    tenant.next_invoice_number = correlativo + 1
    db.session.commit()


def _invoice_detail_url(inv: Invoice) -> str:
    args = {"invoice_id": inv.id}
    if request.form.get("print_invoice") == "1":
        args["print"] = 1
    return url_for("facturas.detail", **args)


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
