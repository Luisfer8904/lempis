"""
Sedes, bodegas y transferencias de inventario.
"""
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func

from models import db
from models.catalog import Product
from models.locations import Branch, Warehouse, WarehouseStock, StockMovement
from services.locations import adjust_stock_entry, adjust_stock_exit, sync_default_warehouse_stock, transfer_stock
from services.permissions import permission_required, tenant_required
from services.plan_limits import (
    PlanLimitError,
    check_can_create_branch,
    check_can_create_warehouse,
)
from services.tenant_context import current_tenant

ubicaciones_bp = Blueprint("ubicaciones", __name__, url_prefix="/app/ubicaciones")


@ubicaciones_bp.route("/", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("products.manage")
def index():
    tenant = current_tenant()
    sync_default_warehouse_stock(tenant)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "branch":
            return _create_branch(tenant)
        if action == "warehouse":
            return _create_warehouse(tenant)
        if action == "toggle_branch":
            return _toggle_branch(tenant)
        if action == "toggle_warehouse":
            return _toggle_warehouse(tenant)
        if action == "transfer":
            return _transfer(tenant)
        if action == "adjustment":
            return _adjustment(tenant)

    branches = Branch.query.filter_by(tenant_id=tenant.id).order_by(Branch.is_default.desc(), Branch.name.asc()).all()
    warehouses = Warehouse.query.filter_by(tenant_id=tenant.id).order_by(Warehouse.is_default.desc(), Warehouse.name.asc()).all()
    products = Product.query.filter_by(tenant_id=tenant.id, is_active=True, kind="product").order_by(Product.name.asc()).all()
    stock_rows = _stock_rows(tenant.id)
    movement_page = max(request.args.get("movement_page", 1, type=int), 1)
    movement_pagination = (
        StockMovement.query
        .filter(StockMovement.tenant_id == tenant.id)
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        .paginate(page=movement_page, per_page=15, error_out=False)
    )
    if movement_pagination.pages and movement_page > movement_pagination.pages:
        return redirect(url_for(
            "ubicaciones.index",
            movement_page=movement_pagination.pages,
            _anchor="movimientos-recientes",
        ))
    return render_template(
        "ubicaciones/index.html",
        tenant=tenant,
        branches=branches,
        warehouses=warehouses,
        products=products,
        stock_rows=stock_rows,
        movements=movement_pagination.items,
        movement_pagination=movement_pagination,
    )


def _create_branch(tenant):
    try:
        check_can_create_branch(tenant)
    except PlanLimitError as e:
        flash(str(e), "warning")
        return redirect(url_for("billing.index"))

    name = (request.form.get("branch_name") or "").strip()
    if not name:
        flash("Escribe el nombre de la sede.", "warning")
        return redirect(url_for("ubicaciones.index"))
    db.session.add(Branch(
        tenant_id=tenant.id,
        name=name,
        code=(request.form.get("branch_code") or "").strip() or None,
        city=(request.form.get("branch_city") or "").strip() or None,
        address=(request.form.get("branch_address") or "").strip() or None,
        is_active=True,
    ))
    db.session.commit()
    flash("Sede creada correctamente.", "success")
    return redirect(url_for("ubicaciones.index"))


def _create_warehouse(tenant):
    try:
        check_can_create_warehouse(tenant)
    except PlanLimitError as e:
        flash(str(e), "warning")
        return redirect(url_for("billing.index"))

    branch_id = request.form.get("warehouse_branch_id", type=int)
    branch = Branch.query.filter_by(id=branch_id, tenant_id=tenant.id, is_active=True).first()
    name = (request.form.get("warehouse_name") or "").strip()
    if branch is None or not name:
        flash("Selecciona una sede y escribe el nombre de la bodega.", "warning")
        return redirect(url_for("ubicaciones.index"))
    db.session.add(Warehouse(
        tenant_id=tenant.id,
        branch_id=branch.id,
        name=name,
        code=(request.form.get("warehouse_code") or "").strip() or None,
        is_active=True,
    ))
    db.session.commit()
    flash("Bodega creada correctamente.", "success")
    return redirect(url_for("ubicaciones.index"))


def _toggle_branch(tenant):
    branch = Branch.query.filter_by(id=request.form.get("branch_id", type=int), tenant_id=tenant.id).first()
    if branch and not branch.is_default:
        branch.is_active = not branch.is_active
        db.session.commit()
        flash("Estado de la sede actualizado.", "success")
    return redirect(url_for("ubicaciones.index"))


def _toggle_warehouse(tenant):
    warehouse = Warehouse.query.filter_by(id=request.form.get("warehouse_id", type=int), tenant_id=tenant.id).first()
    if warehouse and not warehouse.is_default:
        warehouse.is_active = not warehouse.is_active
        db.session.commit()
        flash("Estado de la bodega actualizado.", "success")
    return redirect(url_for("ubicaciones.index"))


def _transfer(tenant):
    try:
        product_id = request.form.get("product_id", type=int)
        source_id = request.form.get("source_warehouse_id", type=int)
        target_id = request.form.get("target_warehouse_id", type=int)
        batch_id = request.form.get("batch_id", type=int) or None
        quantity = Decimal(str(request.form.get("quantity") or "0"))
        if not product_id or not source_id or not target_id:
            raise ValueError("Selecciona producto, origen y destino.")
        transfer_stock(
            tenant.id,
            source_id,
            target_id,
            product_id,
            quantity,
            batch_id=batch_id,
            notes=(request.form.get("notes") or "").strip() or None,
        )
        db.session.commit()
        flash("Transferencia registrada correctamente.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("ubicaciones.index"))


def _adjustment(tenant):
    try:
        adjustment_type = request.form.get("adjustment_type")
        product_id = request.form.get("adjustment_product_id", type=int)
        warehouse_id = request.form.get("adjustment_warehouse_id", type=int)
        batch_id = request.form.get("adjustment_batch_id", type=int) or None
        quantity = Decimal(str(request.form.get("adjustment_quantity") or "0"))
        notes = (request.form.get("adjustment_notes") or "").strip() or None
        if adjustment_type == "entrada":
            adjust_stock_entry(tenant.id, warehouse_id, product_id, quantity, batch_id=batch_id, notes=notes)
            flash("Sobrante de inventario registrado correctamente.", "success")
        elif adjustment_type == "salida":
            adjust_stock_exit(tenant.id, warehouse_id, product_id, quantity, batch_id=batch_id, notes=notes)
            flash("Faltante de inventario registrado correctamente.", "success")
        else:
            raise ValueError("Selecciona si en el conteo sobró o faltó producto.")
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("ubicaciones.index"))


def _stock_rows(tenant_id: int):
    return (
        db.session.query(
            WarehouseStock,
            Product.name.label("product_name"),
            Product.sku.label("sku"),
            Branch.name.label("branch_name"),
            Warehouse.name.label("warehouse_name"),
            func.coalesce(WarehouseStock.quantity, 0).label("quantity"),
        )
        .join(Product, Product.id == WarehouseStock.product_id)
        .join(Warehouse, Warehouse.id == WarehouseStock.warehouse_id)
        .join(Branch, Branch.id == Warehouse.branch_id)
        .filter(WarehouseStock.tenant_id == tenant_id, WarehouseStock.quantity > 0)
        .order_by(Branch.name.asc(), Warehouse.name.asc(), Product.name.asc())
        .all()
    )
