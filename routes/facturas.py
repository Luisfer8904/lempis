"""
CRUD de Facturas con líneas de detalle.
- Numeración automática SAR (EST-PV-TD-CORRELATIVO)
- Métodos de pago: efectivo, transferencia, tarjeta, crédito
- Estados: draft, issued, paid, partially_paid, void, overdue
"""
from __future__ import annotations  # type hints lazy (evita conflicto con la ruta list())

from datetime import datetime, timedelta
from decimal import Decimal

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort, send_file
)
from flask_login import login_required, current_user
from sqlalchemy import and_, or_
from sqlalchemy.orm import selectinload

from models import db
from models.invoice import Invoice, InvoiceItem, InvoicePayment
from models.catalog import Customer, Product, ProductBatch
from models.country import TaxConfig
from models.printing import TenantPrintSettings
from models.user import User
from services.tenant_context import current_tenant
from services.currency import format_money
from services.permissions import admin_required, permission_required, tenant_required
from services.invoice_service import (
    issue_invoice, update_invoice, next_invoice_number,
    validate_can_emit, CAIError, _resolve_batch,
)
from services.pdf_generator import _number_to_letters
from services.stock_validation import InventoryAvailabilityError, validate_sale_inventory
from services.inventory import consume_from_batch, recompute_product_stock
from services.locations import (
    add_stock,
    can_user_sell_from_warehouse,
    sale_warehouses_for_user,
    subtract_stock,
    sync_default_warehouse_stock,
)

facturas_bp = Blueprint("facturas", __name__, url_prefix="/app/facturas")


def _get_or_404(invoice_id: int) -> Invoice:
    tenant = current_tenant()
    inv = Invoice.query.filter_by(id=invoice_id, tenant_id=tenant.id).first()
    if inv is None:
        abort(404)
    return inv


def _get_print_settings(tenant):
    settings = TenantPrintSettings.query.filter_by(tenant_id=tenant.id).first()
    if settings is None:
        settings = TenantPrintSettings(tenant_id=tenant.id)
        db.session.add(settings)
        db.session.flush()
    return settings


# ---------------- LISTAR ----------------

@facturas_bp.route("/")
@login_required
@tenant_required
@permission_required("sales.view")
def list():
    tenant = current_tenant()
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status")
    sale_type = request.args.get("sale_type")
    if sale_type not in {"contado", "credito"}:
        sale_type = None
    payment_method = request.args.get("payment_method")
    if payment_method not in {"efectivo", "transferencia", "tarjeta", "cheque"}:
        payment_method = None
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 30

    query = Invoice.query.options(selectinload(Invoice.payments)).filter_by(tenant_id=tenant.id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Invoice.number.ilike(like),
            Invoice.receptor_name.ilike(like),
            Invoice.receptor_tax_id.ilike(like),
        ))
    if status:
        query = query.filter_by(status=status)
    if sale_type == "credito":
        query = query.filter(Invoice.payment_method == "credito")
    elif sale_type == "contado":
        query = query.filter(Invoice.payment_method != "credito")
    if payment_method:
        recorded_payment = Invoice.payments.any(
            InvoicePayment.payment_method == payment_method
        )
        if payment_method == "cheque":
            query = query.filter(recorded_payment)
        else:
            legacy_payment = and_(
                ~Invoice.payments.any(),
                Invoice.payment_method == payment_method,
            )
            query = query.filter(or_(recorded_payment, legacy_payment))

    pagination = query.order_by(Invoice.issue_date.desc(), Invoice.id.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    if pagination.pages and page > pagination.pages:
        return redirect(url_for(
            "facturas.list",
            q=q or None,
            status=status or None,
            sale_type=sale_type,
            payment_method=payment_method,
            page=pagination.pages,
        ))

    # ¿Puede emitir según su modo? (CAI para SAR, plan limits para simple)
    can_emit_now = True
    try:
        validate_can_emit(tenant)
    except CAIError:
        can_emit_now = False

    return render_template(
        "facturas/list.html",
        facturas=pagination.items, pagination=pagination, q=q, status=status,
        sale_type=sale_type, payment_method=payment_method,
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
            label = "guardada como borrador" if inv.status == "draft" else "emitida"
            flash(f"Factura {inv.number} {label}.", "success")
            return redirect(_invoice_detail_url(inv))
        except CAIError as e:
            db.session.rollback()
            flash(str(e), "danger")
            return redirect(url_for("configuracion.facturacion"))
        except InventoryAvailabilityError as e:
            db.session.rollback()
            flash(str(e), "danger")
            return redirect(url_for("facturas.new"))

    clientes = Customer.query.filter_by(tenant_id=tenant.id, is_active=True).order_by(Customer.name).all()
    productos = Product.query.filter_by(tenant_id=tenant.id, is_active=True).order_by(Product.name).all()
    impuestos = TaxConfig.query.filter_by(country_code=tenant.country_code, is_active=True).all()
    default_warehouse = sync_default_warehouse_stock(tenant)
    warehouses = sale_warehouses_for_user(tenant.id, current_user) or [default_warehouse]
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
        warehouses=warehouses, selected_warehouse_id=warehouses[0].id,
    )


# ---------------- EDITAR (solo draft) ----------------

@facturas_bp.route("/<int:invoice_id>/edit", methods=["GET", "POST"])
@login_required
@tenant_required
def edit(invoice_id):
    inv = _get_or_404(invoice_id)
    tenant = current_tenant()
    can_edit_issued = inv.status != "draft" and inv.status != "void" and current_user.has_permission("sales.edit_issued")

    if inv.status == "draft":
        if not current_user.has_permission("sales.create"):
            flash("No tienes permisos para realizar esa acción.", "warning")
            return redirect(url_for("facturas.detail", invoice_id=inv.id))
    elif not can_edit_issued:
        flash("Solo puedes editar facturas en borrador.", "warning")
        return redirect(url_for("facturas.detail", invoice_id=inv.id))

    if request.method == "GET" and inv.status == "draft":
        return redirect(url_for("pos.quick_sale", draft_id=inv.id))

    if request.method == "POST":
        try:
            items = _parse_items()
            warehouse_id = request.form.get("warehouse_id", type=int) or inv.warehouse_id
            if not can_user_sell_from_warehouse(tenant.id, current_user, warehouse_id):
                flash("No puedes facturar desde una bodega de otra sede.", "warning")
                return redirect(url_for("facturas.edit", invoice_id=inv.id))

            if can_edit_issued:
                _admin_update_issued_invoice(inv, tenant, items, warehouse_id)
                flash(f"Venta {inv.number} actualizada.", "success")
                return redirect(url_for("facturas.detail", invoice_id=inv.id))

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
            inv.warehouse_id = warehouse_id
            # Si pasó a issued, asignar número definitivo
            if request.form.get("action") == "emit":
                _emit_draft(inv, tenant)
            flash(f"Factura {inv.number} actualizada.", "success")
            return redirect(_invoice_detail_url(inv))
        except (CAIError, InventoryAvailabilityError) as e:
            db.session.rollback()
            flash(str(e), "danger")

    clientes = Customer.query.filter_by(tenant_id=tenant.id, is_active=True).order_by(Customer.name).all()
    productos = Product.query.filter_by(tenant_id=tenant.id, is_active=True).order_by(Product.name).all()
    impuestos = TaxConfig.query.filter_by(country_code=tenant.country_code, is_active=True).all()
    default_warehouse = sync_default_warehouse_stock(tenant)
    warehouses = sale_warehouses_for_user(tenant.id, current_user) or [default_warehouse]
    selected_warehouse_id = inv.warehouse_id if inv.warehouse_id in {w.id for w in warehouses} else warehouses[0].id

    return render_template(
        "facturas/form.html",
        factura=inv, clientes=clientes, productos=productos, impuestos=impuestos,
        next_number=inv.number, tenant=tenant,
        can_emit=True, reason=None,
        warehouses=warehouses, selected_warehouse_id=selected_warehouse_id,
        admin_edit_issued=can_edit_issued,
    )


# ---------------- DETALLE ----------------

@facturas_bp.route("/<int:invoice_id>")
@login_required
@tenant_required
@permission_required("sales.view")
def detail(invoice_id):
    inv = _get_or_404(invoice_id)
    tenant = current_tenant()
    return render_template(
        "facturas/detail.html",
        factura=inv,
        tenant=tenant,
        print_settings=_get_print_settings(tenant),
    )


@facturas_bp.route("/<int:invoice_id>/ticket")
@login_required
@tenant_required
@permission_required("sales.view")
def ticket(invoice_id):
    inv = _get_or_404(invoice_id)
    tenant = current_tenant()
    settings = _get_print_settings(tenant)
    width = settings.receipt_paper_width if settings.receipt_paper_width in ("58mm", "80mm") else "80mm"
    issued_user = None
    if inv.issued_by_user_id:
        issued_user = User.query.filter_by(id=inv.issued_by_user_id, tenant_id=tenant.id).first()
    cash_received, cash_change, display_notes = _split_ticket_notes(inv.notes or "")
    return render_template(
        "facturas/ticket.html",
        factura=inv,
        tenant=tenant,
        settings=settings,
        issued_user=issued_user,
        total_letras=_number_to_letters(inv.total, inv.currency),
        cash_received=cash_received,
        cash_change=cash_change,
        display_notes=display_notes,
        receipt_width=width,
        auto_print=request.args.get("print") == "1",
        open_drawer=request.args.get("drawer", "0") == "1",
        next_url=request.args.get("next") or "",
    )


@facturas_bp.route("/<int:invoice_id>/orden-carga")
@login_required
@tenant_required
@permission_required("sales.view")
def orden_carga(invoice_id):
    inv = _get_or_404(invoice_id)
    tenant = current_tenant()
    settings = _get_print_settings(tenant)
    return render_template(
        "facturas/orden_carga.html",
        factura=inv,
        tenant=tenant,
        receipt_width=settings.receipt_paper_width if settings.receipt_paper_width in ("58mm", "80mm") else "80mm",
        auto_print=request.args.get("print") == "1",
    )


def _split_ticket_notes(notes: str):
    cash_received = None
    cash_change = None
    remaining = []
    for line in (notes or "").splitlines():
        clean = line.strip()
        lower = clean.lower()
        if lower.startswith("efectivo recibido:"):
            cash_received = clean.split(":", 1)[1].strip()
        elif lower.startswith("cambio entregado:"):
            cash_change = clean.split(":", 1)[1].strip()
        elif clean:
            remaining.append(clean)
    return cash_received, cash_change, "\n".join(remaining)


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
@admin_required
def delete(invoice_id):
    from services.invoice_service import void_invoice_and_restore_stock
    inv = _get_or_404(invoice_id)
    number = inv.number
    if inv.status not in ("draft", "void"):
        void_invoice_and_restore_stock(inv)
        inv = _get_or_404(invoice_id)
    db.session.delete(inv)
    db.session.commit()
    flash(f"Venta {number} eliminada.", "info")
    return redirect(url_for("facturas.list"))


# ---------------- AJAX: lotes de un producto ----------------

@facturas_bp.route("/api/productos/<int:product_id>/lotes")
@login_required
@tenant_required
def api_lotes_de_producto(product_id):
    """Devuelve JSON con los lotes disponibles de un producto (para el form de facturas)."""
    from flask import jsonify
    from models.catalog import Product, ProductBatch

    if not (current_user.has_permission("sales.create") or current_user.has_permission("sales.edit_issued")):
        return jsonify({"lotes": []}), 403

    tenant = current_tenant()
    p = Product.query.filter_by(id=product_id, tenant_id=tenant.id).first()
    if p is None or not p.track_batches:
        return jsonify({"lotes": []})

    warehouse_id = request.args.get("warehouse_id", type=int)
    if warehouse_id and not can_user_sell_from_warehouse(tenant.id, current_user, warehouse_id):
        return jsonify({"lotes": []})
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
    manual_receptor_name = (request.form.get("receptor_name") or "").strip() if not customer else ""
    manual_receptor_tax_id = (request.form.get("receptor_tax_id") or "").strip() if not customer else ""
    items = _parse_items()
    if not items:
        raise CAIError("Debes agregar al menos una línea a la factura.")

    action = request.form.get("action", "draft")
    status = "issued" if action == "emit" else "draft"

    payment_method = request.form.get("payment_method", "efectivo")
    warehouse_id = request.form.get("warehouse_id", type=int)
    if status == "issued" and not can_user_sell_from_warehouse(tenant.id, current_user, warehouse_id):
        raise CAIError("No puedes facturar desde una bodega de otra sede.")
    if status == "issued":
        validate_sale_inventory(tenant.id, warehouse_id, items)

    return issue_invoice(
        tenant=tenant,
        customer=customer,
        items_data=items,
        payment_method=payment_method,
        payment_terms_days=request.form.get("payment_terms_days", 0),
        notes=_notes_with_cash_details(request.form.get("notes", ""), payment_method),
        issued_by_user_id=current_user.id,
        status=status,
        warehouse_id=warehouse_id,
        receptor_name=manual_receptor_name,
        receptor_tax_id=manual_receptor_tax_id,
    )


def _customer_from_form(tenant):
    customer_id = request.form.get("customer_id")
    if not customer_id:
        return None
    return Customer.query.filter_by(id=customer_id, tenant_id=tenant.id).first()


def _apply_invoice_header_from_form(inv: Invoice, tenant, warehouse_id: int | None) -> None:
    customer = _customer_from_form(tenant)
    payment_method = request.form.get("payment_method", "efectivo")
    payment_terms_days = int(request.form.get("payment_terms_days", 0) or 0)

    inv.customer_id = customer.id if customer else None
    inv.warehouse_id = warehouse_id
    inv.currency = tenant.currency
    inv.payment_method = payment_method
    inv.payment_terms_days = payment_terms_days
    inv.notes = _notes_with_cash_details(request.form.get("notes", ""), payment_method)
    inv.receptor_name = customer.name if customer else (request.form.get("receptor_name") or "").strip() or None
    inv.receptor_tax_id = customer.tax_id if customer else (request.form.get("receptor_tax_id") or "").strip() or None
    inv.due_date = (
        inv.issue_date + timedelta(days=payment_terms_days)
        if payment_method == "credito" and payment_terms_days
        else None
    )


def _restore_invoice_stock(inv: Invoice) -> None:
    for item in tuple(inv.items):
        qty = Decimal(item.quantity or 0)
        if qty <= 0 or not item.product_id:
            continue
        if item.batch_id:
            batch = db.session.get(ProductBatch, item.batch_id)
            if batch is not None:
                batch.remaining_quantity = Decimal(batch.remaining_quantity or 0) + qty
                if inv.warehouse_id:
                    add_stock(inv.tenant_id, inv.warehouse_id, batch.product_id, qty, batch.id, "edicion_venta")
                if batch.product:
                    recompute_product_stock(batch.product)
            continue

        product = db.session.get(Product, int(item.product_id))
        if product is not None and product.tenant_id == inv.tenant_id and product.track_stock:
            product.stock = Decimal(product.stock or 0) + qty
            if inv.warehouse_id:
                add_stock(inv.tenant_id, inv.warehouse_id, product.id, qty, None, "edicion_venta")


def _replace_invoice_items_and_consume(inv: Invoice, tenant, items_data: list[dict]) -> None:
    for old_item in tuple(inv.items):
        db.session.delete(old_item)
    inv.items = []
    db.session.flush()

    for data in items_data:
        product_id = data.get("product_id")
        qty = Decimal(str(data.get("quantity") or 1))
        batch = _resolve_batch(tenant.id, product_id, data.get("batch_id"), qty, inv.status, inv.warehouse_id)

        item = InvoiceItem(
            tenant_id=inv.tenant_id,
            product_id=product_id,
            batch_id=batch.id if batch else None,
            description=data.get("description") or "",
            quantity=qty,
            unit_price=Decimal(str(data.get("unit_price") or 0)),
            tax_rate=Decimal(str(data.get("tax_rate") or 0)),
            discount_amount=Decimal(str(data.get("discount_amount") or 0)),
        )
        item.recalc()
        inv.items.append(item)

        if not product_id:
            continue
        if batch is not None:
            consume_from_batch(batch, qty, inv.warehouse_id)
            if batch.product:
                recompute_product_stock(batch.product)
            continue

        product = db.session.get(Product, int(product_id))
        if product is not None and product.tenant_id == tenant.id and product.track_stock:
            product.stock = max(Decimal(0), Decimal(product.stock or 0) - qty)
            if inv.warehouse_id:
                subtract_stock(inv.tenant_id, inv.warehouse_id, product.id, qty, None, "edicion_venta")


def _refresh_invoice_status_after_admin_edit(inv: Invoice) -> None:
    paid = Decimal(inv.amount_paid or 0)
    total = Decimal(inv.total or 0)
    tolerance = Decimal("0.005")

    if inv.payment_method != "credito":
        inv.amount_paid = total
        inv.status = "paid"
        return

    if paid - total > tolerance:
        raise CAIError(
            "No se puede guardar: los cobros registrados superan el nuevo total de la factura."
        )
    if total - paid <= tolerance:
        inv.amount_paid = total
        inv.status = "paid"
    elif paid > tolerance:
        inv.status = "partially_paid"
    else:
        inv.status = "issued"

    if inv.status in ("issued", "partially_paid") and inv.due_date and inv.due_date < datetime.utcnow():
        inv.status = "overdue"


def _admin_update_issued_invoice(inv: Invoice, tenant, items_data: list[dict], warehouse_id: int | None) -> None:
    if not current_user.has_permission("sales.edit_issued"):
        raise CAIError("No tienes permiso para editar ventas emitidas.")
    if inv.status in ("draft", "void"):
        raise CAIError("Esta acción solo aplica a ventas emitidas.")
    if not items_data:
        raise CAIError("Debes agregar al menos una línea a la factura.")

    _restore_invoice_stock(inv)
    validate_sale_inventory(tenant.id, warehouse_id, items_data)
    _apply_invoice_header_from_form(inv, tenant, warehouse_id)
    _replace_invoice_items_and_consume(inv, tenant, items_data)
    inv.recalc_totals()
    _refresh_invoice_status_after_admin_edit(inv)
    db.session.commit()


def _emit_draft(inv: Invoice, tenant) -> None:
    """Convierte un draft a emitido: asigna número definitivo + congela CAI."""
    validate_can_emit(tenant)
    inventory_items = [
        {
            "product_id": item.product_id,
            "batch_id": item.batch_id,
            "quantity": item.quantity,
        }
        for item in inv.items
    ]
    validate_sale_inventory(tenant.id, inv.warehouse_id, inventory_items)
    for item, inventory_data in zip(inv.items, inventory_items):
        item.batch_id = inventory_data.get("batch_id")
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
    for item in inv.items:
        if not item.product_id:
            continue
        batch = _resolve_batch(tenant.id, item.product_id, item.batch_id, item.quantity, inv.status, inv.warehouse_id)
        if batch is not None:
            item.batch_id = batch.id
            consume_from_batch(batch, item.quantity, inv.warehouse_id)
            if batch.product:
                recompute_product_stock(batch.product)
            continue

        product = db.session.get(Product, int(item.product_id))
        if product is not None and product.tenant_id == tenant.id and product.track_stock:
            product.stock = max(Decimal(0), Decimal(product.stock or 0) - Decimal(item.quantity or 0))
            if inv.warehouse_id:
                subtract_stock(inv.tenant_id, inv.warehouse_id, product.id, item.quantity, None, "venta")
    if inv.payment_method != "credito":
        inv.status = "paid"
        inv.amount_paid = inv.total
    tenant.next_invoice_number = correlativo + 1
    db.session.commit()


def _invoice_detail_url(inv: Invoice) -> str:
    if request.form.get("print_invoice") == "1":
        settings = _get_print_settings(current_tenant())
        if settings.thermal_printer_enabled and settings.detailed_sale_format == "thermal_receipt":
            return url_for(
                "facturas.ticket",
                invoice_id=inv.id,
                print=1,
                drawer=0,
            )
        return url_for("facturas.detail", invoice_id=inv.id, print=1)
    return url_for("facturas.detail", invoice_id=inv.id)


def _notes_with_cash_details(notes: str, payment_method: str) -> str:
    """Agrega recibido/cambio a las notas de la factura cuando la venta es en efectivo."""
    base = (notes or "").strip()
    if payment_method != "efectivo":
        return base

    received = _money_or_none(request.form.get("cash_received"))
    change = _money_or_none(request.form.get("cash_change"))
    if received is None:
        return base

    currency = current_tenant().currency
    cash_lines = [f"Efectivo recibido: {format_money(received, currency)}"]
    if change is not None:
        cash_lines.append(f"Cambio entregado: {format_money(change, currency)}")

    cash_note = "\n".join(cash_lines)
    return f"{base}\n\n{cash_note}" if base else cash_note


def _money_or_none(value: str | None) -> Decimal | None:
    try:
        return Decimal(str(value or "").strip()).quantize(Decimal("0.01"))
    except Exception:
        return None
