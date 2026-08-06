"""
CRUD de Compras (entrada de mercadería).
- Borrador → Recibida → (opcional) Anulada
- Al recibir: crea lotes, suma stock, actualiza precios de venta si aplica
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify,
)
from flask_login import login_required, current_user
from sqlalchemy import or_

from models import db
from models.suppliers import Supplier
from models.purchases import Purchase, PurchaseItem
from models.catalog import Product
from models.country import TaxConfig
from services.tenant_context import current_tenant
from services.permissions import permission_required, tenant_required
from services.purchase_service import (
    create_purchase, update_purchase_draft, finalize_purchase,
    void_purchase, register_payment, next_purchase_number, PurchaseError,
    repair_received_purchase_inventory,
)
from services.locations import active_warehouses, sync_default_warehouse_stock

compras_bp = Blueprint("compras", __name__, url_prefix="/app/compras")


def _get_or_404(purchase_id: int) -> Purchase:
    tenant = current_tenant()
    p = Purchase.query.filter_by(id=purchase_id, tenant_id=tenant.id).first()
    if p is None:
        abort(404)
    return p


def _amount_due(purchase: Purchase) -> Decimal:
    return Decimal(purchase.amount_due or 0)


def _due_day(purchase: Purchase):
    if not purchase.due_date:
        return None
    return purchase.due_date.date() if hasattr(purchase.due_date, "date") else purchase.due_date


def _pending_credit_purchases(query):
    return [purchase for purchase in query.all() if _amount_due(purchase) > 0]


# ---------------- LISTAR ----------------

@compras_bp.route("/")
@login_required
@tenant_required
@permission_required("purchases.view")
def list():
    tenant = current_tenant()
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status")

    query = Purchase.query.filter_by(tenant_id=tenant.id)
    if q:
        like = f"%{q}%"
        query = (
            query.outerjoin(Supplier)
            .filter(or_(
                Purchase.number.ilike(like),
                Purchase.supplier_invoice_number.ilike(like),
                Supplier.name.ilike(like),
                Supplier.tax_id.ilike(like),
            ))
        )
    if status:
        query = query.filter_by(status=status)

    compras = query.order_by(Purchase.issue_date.desc()).all()
    return render_template("compras/list.html", compras=compras, q=q, status=status)


@compras_bp.route("/por-pagar")
@login_required
@tenant_required
@permission_required("purchases.view")
def payables():
    tenant = current_tenant()
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    today = datetime.now().date()
    soon_limit = today + timedelta(days=7)

    base_query = (
        Purchase.query.outerjoin(Supplier)
        .filter(
            Purchase.tenant_id == tenant.id,
            Purchase.status == "received",
            Purchase.terms_days > 0,
        )
    )
    all_credit = _pending_credit_purchases(base_query)

    query = base_query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Purchase.number.ilike(like),
            Purchase.supplier_invoice_number.ilike(like),
            Supplier.name.ilike(like),
            Supplier.tax_id.ilike(like),
        ))

    compras = _pending_credit_purchases(query)
    if status == "vencidas":
        compras = [c for c in compras if _due_day(c) and _due_day(c) < today]
    elif status == "proximas":
        compras = [c for c in compras if _due_day(c) and today <= _due_day(c) <= soon_limit]
    elif status == "sin_fecha":
        compras = [c for c in compras if not _due_day(c)]

    compras.sort(key=lambda c: (_due_day(c) is None, _due_day(c) or today, c.issue_date or datetime.min))
    due_days = {c.id: _due_day(c) for c in compras}
    overdue_all = [c for c in all_credit if _due_day(c) and _due_day(c) < today]
    soon_all = [c for c in all_credit if _due_day(c) and today <= _due_day(c) <= soon_limit]
    supplier_count = len({c.supplier_id for c in all_credit if c.supplier_id})
    total_pending = sum((_amount_due(c) for c in all_credit), Decimal("0"))
    total_overdue = sum((_amount_due(c) for c in overdue_all), Decimal("0"))
    total_soon = sum((_amount_due(c) for c in soon_all), Decimal("0"))

    return render_template(
        "compras/payables.html",
        compras=compras,
        due_days=due_days,
        q=q,
        status=status,
        today=today,
        soon_limit=soon_limit,
        total_pending=total_pending,
        total_overdue=total_overdue,
        total_soon=total_soon,
        overdue_count=len(overdue_all),
        soon_count=len(soon_all),
        supplier_count=supplier_count,
    )


# ---------------- CREAR ----------------

@compras_bp.route("/new", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("purchases.manage")
def new():
    tenant = current_tenant()

    if request.method == "POST":
        try:
            inv = _create_from_form(tenant)
            action = request.form.get("action", "draft")
            if action == "receive":
                finalize_purchase(inv)
                flash(f"Compra {inv.number} recibida. Inventario actualizado.", "success")
            else:
                flash(f"Compra {inv.number} guardada como borrador.", "success")
            return redirect(url_for("compras.detail", purchase_id=inv.id))
        except PurchaseError as e:
            flash(str(e), "danger")
            return redirect(url_for("compras.new"))

    suppliers = Supplier.query.filter_by(tenant_id=tenant.id, is_active=True).order_by(Supplier.name).all()
    productos = Product.query.filter_by(tenant_id=tenant.id, is_active=True).order_by(Product.name).all()
    impuestos = TaxConfig.query.filter_by(country_code=tenant.country_code, is_active=True).all()
    default_warehouse = sync_default_warehouse_stock(tenant)
    warehouses = active_warehouses(tenant.id)
    return render_template(
        "compras/form.html",
        compra=None, suppliers=suppliers, productos=productos,
        impuestos=impuestos, tenant=tenant,
        next_number=next_purchase_number(tenant),
        warehouses=warehouses, selected_warehouse_id=default_warehouse.id,
    )


# ---------------- EDITAR (solo borrador) ----------------

@compras_bp.route("/<int:purchase_id>/edit", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("purchases.manage")
def edit(purchase_id):
    inv = _get_or_404(purchase_id)
    tenant = current_tenant()

    if inv.status != "draft":
        flash("Solo se pueden editar compras en borrador.", "warning")
        return redirect(url_for("compras.detail", purchase_id=inv.id))

    if request.method == "POST":
        try:
            items = _parse_items()
            update_purchase_draft(
                inv,
                items_data=items,
                supplier_id=request.form.get("supplier_id") or None,
                supplier_invoice_number=request.form.get("supplier_invoice_number") or None,
                terms_days=request.form.get("terms_days") or 0,
                notes=request.form.get("notes") or None,
            )
            inv.warehouse_id = request.form.get("warehouse_id", type=int) or inv.warehouse_id
            if request.form.get("action") == "receive":
                finalize_purchase(inv)
                flash(f"Compra {inv.number} recibida. Inventario actualizado.", "success")
            else:
                flash(f"Compra {inv.number} actualizada.", "success")
            return redirect(url_for("compras.detail", purchase_id=inv.id))
        except PurchaseError as e:
            flash(str(e), "danger")

    suppliers = Supplier.query.filter_by(tenant_id=tenant.id, is_active=True).order_by(Supplier.name).all()
    productos = Product.query.filter_by(tenant_id=tenant.id, is_active=True).order_by(Product.name).all()
    impuestos = TaxConfig.query.filter_by(country_code=tenant.country_code, is_active=True).all()
    default_warehouse = sync_default_warehouse_stock(tenant)
    warehouses = active_warehouses(tenant.id)
    return render_template(
        "compras/form.html",
        compra=inv, suppliers=suppliers, productos=productos,
        impuestos=impuestos, tenant=tenant,
        next_number=inv.number,
        warehouses=warehouses, selected_warehouse_id=inv.warehouse_id or default_warehouse.id,
    )


# ---------------- DETALLE ----------------

@compras_bp.route("/<int:purchase_id>")
@login_required
@tenant_required
@permission_required("purchases.view")
def detail(purchase_id):
    inv = _get_or_404(purchase_id)
    return render_template("compras/detail.html", compra=inv, tenant=current_tenant())


# ---------------- FINALIZAR / ANULAR / PAGO ----------------

@compras_bp.route("/<int:purchase_id>/receive", methods=["POST"])
@login_required
@tenant_required
@permission_required("purchases.manage")
def receive(purchase_id):
    inv = _get_or_404(purchase_id)
    try:
        finalize_purchase(inv)
        flash(f"Compra {inv.number} recibida. Inventario actualizado.", "success")
    except PurchaseError as e:
        flash(str(e), "danger")
    return redirect(url_for("compras.detail", purchase_id=inv.id))


@compras_bp.route("/<int:purchase_id>/repair-inventory", methods=["POST"])
@login_required
@tenant_required
@permission_required("purchases.manage")
def repair_inventory(purchase_id):
    inv = _get_or_404(purchase_id)
    try:
        repaired = repair_received_purchase_inventory(inv)
        if repaired:
            flash(
                f"Inventario reprocesado. Se corrigieron {repaired} línea(s) sin lote y el stock fue sincronizado.",
                "success",
            )
        else:
            flash("No había líneas pendientes por reparar en esta compra.", "info")
    except PurchaseError as e:
        flash(str(e), "danger")
    return redirect(url_for("compras.detail", purchase_id=inv.id))


@compras_bp.route("/<int:purchase_id>/void", methods=["POST"])
@login_required
@tenant_required
@permission_required("purchases.manage")
def void(purchase_id):
    inv = _get_or_404(purchase_id)
    void_purchase(inv)
    flash(f"Compra {inv.number} anulada. Stock revertido.", "warning")
    return redirect(url_for("compras.detail", purchase_id=inv.id))


@compras_bp.route("/<int:purchase_id>/payment", methods=["POST"])
@login_required
@tenant_required
@permission_required("purchases.manage")
def payment(purchase_id):
    inv = _get_or_404(purchase_id)
    amount = request.form.get("amount") or 0
    next_view = request.form.get("next") or request.args.get("next")
    try:
        register_payment(inv, amount)
        flash(f"Pago registrado. Saldo pendiente: {inv.currency} {inv.amount_due:.2f}", "success")
    except PurchaseError as e:
        flash(str(e), "danger")
    if next_view == "payables":
        return redirect(url_for("compras.payables"))
    return redirect(url_for("compras.detail", purchase_id=inv.id))


@compras_bp.route("/<int:purchase_id>/delete", methods=["POST"])
@login_required
@tenant_required
@permission_required("purchases.manage")
def delete(purchase_id):
    inv = _get_or_404(purchase_id)
    if inv.status != "draft":
        flash("Solo puedes eliminar borradores. Las recibidas se anulan.", "warning")
        return redirect(url_for("compras.detail", purchase_id=inv.id))
    db.session.delete(inv)
    db.session.commit()
    flash("Borrador eliminado.", "info")
    return redirect(url_for("compras.list"))


# ---------------- Helpers ----------------

def _parse_items() -> list[dict]:
    items = []
    descs = request.form.getlist("item_description[]")
    prods = request.form.getlist("item_product_id[]")
    qtys = request.form.getlist("item_quantity[]")
    costs = request.form.getlist("item_unit_cost[]")
    taxes = request.form.getlist("item_tax_rate[]")
    batches = request.form.getlist("item_batch_number[]")
    mfg = request.form.getlist("item_mfg_date[]")
    exp = request.form.getlist("item_exp_date[]")
    new_prices = request.form.getlist("item_new_sale_price[]")

    for i, d in enumerate(descs):
        if not d.strip() and not (i < len(prods) and prods[i]):
            continue
        items.append({
            "product_id": int(prods[i]) if (i < len(prods) and prods[i]) else None,
            "description": d,
            "quantity": qtys[i] if i < len(qtys) else 1,
            "unit_cost": costs[i] if i < len(costs) else 0,
            "tax_rate": taxes[i] if i < len(taxes) else 0,
            "batch_number": batches[i] if i < len(batches) else "",
            "manufacturing_date": mfg[i] if i < len(mfg) else "",
            "expiration_date": exp[i] if i < len(exp) else "",
            "new_sale_price": new_prices[i] if i < len(new_prices) else None,
        })
    return items


def _create_from_form(tenant) -> Purchase:
    supplier_id = request.form.get("supplier_id")
    supplier = None
    if supplier_id:
        supplier = Supplier.query.filter_by(id=supplier_id, tenant_id=tenant.id).first()

    items = _parse_items()
    if not items:
        raise PurchaseError("Debes agregar al menos una línea a la compra.")

    return create_purchase(
        tenant=tenant,
        supplier=supplier,
        items_data=items,
        supplier_invoice_number=request.form.get("supplier_invoice_number", ""),
        terms_days=int(request.form.get("terms_days") or 0),
        notes=request.form.get("notes", ""),
        received_by_user_id=current_user.id,
        warehouse_id=request.form.get("warehouse_id", type=int),
    )
