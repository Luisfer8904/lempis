"""Sedes y transferencias de inventario entre sus ubicaciones internas."""
from datetime import datetime
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
        if action == "consolidate_warehouses":
            return _consolidate_warehouses(tenant)
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
    active_warehouses_by_branch = {
        branch.id: [warehouse for warehouse in warehouses if warehouse.branch_id == branch.id and warehouse.is_active]
        for branch in branches
    }
    canonical_warehouse_by_branch = {
        branch_id: _canonical_warehouse(branch_warehouses)
        for branch_id, branch_warehouses in active_warehouses_by_branch.items()
    }
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
        active_warehouses_by_branch=active_warehouses_by_branch,
        canonical_warehouse_by_branch=canonical_warehouse_by_branch,
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
    branch = Branch(
        tenant_id=tenant.id,
        name=name,
        code=(request.form.get("branch_code") or "").strip() or None,
        city=(request.form.get("branch_city") or "").strip() or None,
        address=(request.form.get("branch_address") or "").strip() or None,
        is_active=True,
    )
    db.session.add(branch)
    db.session.flush()
    db.session.add(Warehouse(
        tenant_id=tenant.id,
        branch_id=branch.id,
        name="Principal",
        code=(branch.code or f"SEDE-{branch.id}")[:30],
        is_active=True,
    ))
    db.session.commit()
    flash("Sede creada correctamente.", "success")
    return redirect(url_for("ubicaciones.index"))


def _create_warehouse(tenant):
    branch_id = request.form.get("warehouse_branch_id", type=int)
    branch = Branch.query.filter_by(id=branch_id, tenant_id=tenant.id, is_active=True).first()
    name = (request.form.get("warehouse_name") or "").strip()
    if branch is None or not name:
        flash("Selecciona una sede y escribe el nombre de la ubicación.", "warning")
        return redirect(url_for("ubicaciones.index"))
    existing = Warehouse.query.filter_by(tenant_id=tenant.id, branch_id=branch.id, is_active=True).first()
    if existing is not None:
        flash("Cada sede utiliza una sola ubicación de inventario.", "warning")
        return redirect(url_for("ubicaciones.index"))
    db.session.add(Warehouse(
        tenant_id=tenant.id,
        branch_id=branch.id,
        name=name,
        code=(request.form.get("warehouse_code") or "").strip() or None,
        is_active=True,
    ))
    db.session.commit()
    flash("Ubicación de inventario creada correctamente.", "success")
    return redirect(url_for("ubicaciones.index"))


def _toggle_branch(tenant):
    branch = Branch.query.filter_by(id=request.form.get("branch_id", type=int), tenant_id=tenant.id).first()
    if branch and not branch.is_default:
        branch.is_active = not branch.is_active
        if branch.is_active and not any(warehouse.is_active for warehouse in branch.warehouses):
            warehouse = _canonical_warehouse(branch.warehouses)
            if warehouse is None:
                warehouse = Warehouse(
                    tenant_id=tenant.id,
                    branch_id=branch.id,
                    name="Principal",
                    code=(branch.code or f"SEDE-{branch.id}")[:30],
                )
                db.session.add(warehouse)
            warehouse.is_active = True
        db.session.commit()
        flash("Estado de la sede actualizado.", "success")
    return redirect(url_for("ubicaciones.index"))


def _toggle_warehouse(tenant):
    warehouse = Warehouse.query.filter_by(id=request.form.get("warehouse_id", type=int), tenant_id=tenant.id).first()
    if warehouse and not warehouse.is_default:
        if not warehouse.is_active:
            active_exists = Warehouse.query.filter(
                Warehouse.tenant_id == tenant.id,
                Warehouse.branch_id == warehouse.branch_id,
                Warehouse.is_active.is_(True),
                Warehouse.id != warehouse.id,
            ).first()
            if active_exists:
                flash("La sede ya tiene una ubicación de inventario activa.", "warning")
                return redirect(url_for("ubicaciones.index"))
        warehouse.is_active = not warehouse.is_active
        db.session.commit()
        flash("Estado de la ubicación de inventario actualizado.", "success")
    return redirect(url_for("ubicaciones.index"))


def _consolidate_warehouses(tenant):
    """Une el inventario de una sede en su ubicación principal sin borrar historial."""
    branch = Branch.query.filter_by(
        id=request.form.get("branch_id", type=int),
        tenant_id=tenant.id,
    ).first()
    target = Warehouse.query.filter_by(
        id=request.form.get("target_warehouse_id", type=int),
        tenant_id=tenant.id,
    ).first()
    if branch is None or target is None or target.branch_id != branch.id:
        flash("La sede o ubicación principal no es válida.", "warning")
        return redirect(url_for("ubicaciones.index"))

    sources = Warehouse.query.filter(
        Warehouse.tenant_id == tenant.id,
        Warehouse.branch_id == branch.id,
        Warehouse.id != target.id,
        Warehouse.is_active.is_(True),
    ).all()
    if not sources:
        flash("Esta sede ya utiliza una sola ubicación de inventario.", "info")
        return redirect(url_for("ubicaciones.index"))

    moved_rows = 0
    try:
        target.is_active = True
        for source in sources:
            stocks = WarehouseStock.query.filter_by(
                tenant_id=tenant.id,
                warehouse_id=source.id,
            ).filter(WarehouseStock.quantity != 0).all()
            for source_stock in stocks:
                quantity = Decimal(source_stock.quantity or 0)
                target_stock = WarehouseStock.query.filter_by(
                    tenant_id=tenant.id,
                    warehouse_id=target.id,
                    product_id=source_stock.product_id,
                    batch_id=source_stock.batch_id,
                ).first()
                if target_stock is None:
                    target_stock = WarehouseStock(
                        tenant_id=tenant.id,
                        warehouse_id=target.id,
                        product_id=source_stock.product_id,
                        batch_id=source_stock.batch_id,
                        quantity=0,
                    )
                    db.session.add(target_stock)
                target_stock.quantity = Decimal(target_stock.quantity or 0) + quantity
                source_stock.quantity = 0
                db.session.add(StockMovement(
                    tenant_id=tenant.id,
                    product_id=source_stock.product_id,
                    batch_id=source_stock.batch_id,
                    source_warehouse_id=source.id,
                    target_warehouse_id=target.id,
                    quantity=quantity,
                    movement_type="consolidacion",
                    reference="unificacion_sede",
                    notes=f"Inventario unificado en {target.name}",
                    moved_at=datetime.utcnow(),
                ))
                moved_rows += 1
            source.is_active = False
        db.session.commit()
        flash(
            f"Inventario de {branch.name} unificado correctamente en {target.name} ({moved_rows} registros trasladados).",
            "success",
        )
    except Exception as exc:
        db.session.rollback()
        flash(f"No se pudo unificar el inventario: {exc}", "danger")
    return redirect(url_for("ubicaciones.index"))


def _canonical_warehouse(warehouses):
    warehouses = list(warehouses or [])
    if not warehouses:
        return None
    return sorted(
        warehouses,
        key=lambda warehouse: (
            not warehouse.is_default,
            "principal" not in (warehouse.name or "").lower(),
            warehouse.id,
        ),
    )[0]


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
        .filter(
            WarehouseStock.tenant_id == tenant_id,
            WarehouseStock.quantity > 0,
            Warehouse.is_active.is_(True),
        )
        .order_by(Branch.name.asc(), Warehouse.name.asc(), Product.name.asc())
        .all()
    )
