"""
CRUD de lotes (ProductBatch) de cada producto.

Rutas anidadas bajo productos:
  /app/productos/<product_id>/lotes
  /app/productos/<product_id>/lotes/new
  /app/productos/<product_id>/lotes/<batch_id>/edit
  /app/productos/<product_id>/lotes/<batch_id>/delete
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required

from models import db
from models.catalog import Product, ProductBatch
from services.inventory import sync_missing_batch_warehouse_stock
from services.tenant_context import current_tenant
from services.permissions import permission_required, tenant_required

lotes_bp = Blueprint("lotes", __name__, url_prefix="/app/productos/<int:product_id>/lotes")


def _get_product_or_404(product_id: int) -> Product:
    tenant = current_tenant()
    p = Product.query.filter_by(id=product_id, tenant_id=tenant.id).first()
    if p is None:
        abort(404)
    return p


def _get_batch_or_404(product_id: int, batch_id: int) -> ProductBatch:
    tenant = current_tenant()
    b = ProductBatch.query.filter_by(
        id=batch_id, tenant_id=tenant.id, product_id=product_id,
    ).first()
    if b is None:
        abort(404)
    return b


def _safe_decimal(value, default="0") -> Decimal:
    try:
        return Decimal(str(value or default))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


@lotes_bp.route("/")
@login_required
@tenant_required
@permission_required("products.view")
def list(product_id):
    producto = _get_product_or_404(product_id)
    lotes = (
        ProductBatch.query
        .filter_by(tenant_id=current_tenant().id, product_id=producto.id)
        # NULLs al final cross-DB (MySQL no soporta NULLS LAST nativo)
        .order_by(ProductBatch.expiration_date.is_(None).asc(),
                  ProductBatch.expiration_date.asc(),
                  ProductBatch.created_at.desc())
        .all()
    )
    return render_template("lotes/list.html", producto=producto, lotes=lotes)


@lotes_bp.route("/new", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("products.manage")
def new(product_id):
    producto = _get_product_or_404(product_id)

    if request.method == "POST":
        tenant = current_tenant()
        b = ProductBatch(tenant_id=tenant.id, product_id=producto.id)
        _populate_from_form(b)
        # Al crear, la cantidad inicial == remanente
        b.remaining_quantity = b.initial_quantity

        # Actualizar stock total del producto
        producto.stock = (producto.stock or 0) + int(b.initial_quantity or 0)

        db.session.add(b)
        db.session.flush()
        sync_missing_batch_warehouse_stock(producto)
        db.session.commit()
        flash(f"Lote '{b.batch_number}' agregado.", "success")
        return redirect(url_for("lotes.list", product_id=producto.id))

    return render_template("lotes/form.html", producto=producto, lote=None)


@lotes_bp.route("/<int:batch_id>/edit", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("products.manage")
def edit(product_id, batch_id):
    producto = _get_product_or_404(product_id)
    lote = _get_batch_or_404(product_id, batch_id)

    if request.method == "POST":
        old_initial = Decimal(lote.initial_quantity or 0)
        old_remaining = Decimal(lote.remaining_quantity or 0)
        consumed = old_initial - old_remaining  # lo que se vendió

        _populate_from_form(lote)

        # Ajustar remanente: si cambia la cantidad inicial, mantener lo consumido
        new_remaining = Decimal(lote.initial_quantity or 0) - consumed
        if new_remaining < 0:
            new_remaining = Decimal(0)
        lote.remaining_quantity = new_remaining

        # Recalcular stock total del producto
        producto.stock = producto.total_stock_from_batches()
        sync_missing_batch_warehouse_stock(producto)

        db.session.commit()
        flash(f"Lote '{lote.batch_number}' actualizado.", "success")
        return redirect(url_for("lotes.list", product_id=producto.id))

    return render_template("lotes/form.html", producto=producto, lote=lote)


@lotes_bp.route("/<int:batch_id>/delete", methods=["POST"])
@login_required
@tenant_required
@permission_required("products.delete")
def delete(product_id, batch_id):
    producto = _get_product_or_404(product_id)
    lote = _get_batch_or_404(product_id, batch_id)

    # No permitir borrar lotes con facturas asociadas (para mantener trazabilidad)
    if lote.invoice_items:
        flash(
            f"No se puede borrar el lote '{lote.batch_number}': tiene {len(lote.invoice_items)} "
            "línea(s) de factura asociada(s). Marca el lote como agotado en su lugar.",
            "warning",
        )
        return redirect(url_for("lotes.list", product_id=producto.id))

    num = lote.batch_number
    db.session.delete(lote)
    # Recalcular stock total
    producto.stock = producto.total_stock_from_batches()
    db.session.commit()
    flash(f"Lote '{num}' eliminado.", "info")
    return redirect(url_for("lotes.list", product_id=producto.id))


def _populate_from_form(b: ProductBatch) -> None:
    b.batch_number = (request.form.get("batch_number") or "").strip()
    b.manufacturing_date = _parse_date(request.form.get("manufacturing_date"))
    b.expiration_date = _parse_date(request.form.get("expiration_date"))
    b.initial_quantity = _safe_decimal(request.form.get("initial_quantity"))
    b.cost = _safe_decimal(request.form.get("cost"))
    b.supplier = (request.form.get("supplier") or "").strip() or None
    b.notes = (request.form.get("notes") or "").strip() or None
