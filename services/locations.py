"""
Servicios para sedes, bodegas e inventario por ubicación.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func

from models import db
from models.catalog import Product, ProductBatch
from models.locations import Branch, Warehouse, WarehouseStock, StockMovement


def ensure_default_locations(tenant) -> Warehouse:
    """Crea una sede y bodega principal si el tenant todavía no tiene ubicaciones."""
    branch = Branch.query.filter_by(tenant_id=tenant.id, is_default=True).first()
    if branch is None:
        branch = Branch(
            tenant_id=tenant.id,
            name="Sede principal",
            code="PRINCIPAL",
            city=getattr(tenant, "city", None),
            address=getattr(tenant, "address", None),
            is_default=True,
            is_active=True,
        )
        db.session.add(branch)
        db.session.flush()

    warehouse = Warehouse.query.filter_by(tenant_id=tenant.id, is_default=True).first()
    if warehouse is None:
        warehouse = Warehouse(
            tenant_id=tenant.id,
            branch_id=branch.id,
            name="Bodega principal",
            code="PRINCIPAL",
            is_default=True,
            is_active=True,
        )
        db.session.add(warehouse)
        db.session.flush()

    return warehouse


def active_warehouses(tenant_id: int) -> list[Warehouse]:
    return (
        Warehouse.query
        .join(Branch)
        .filter(Warehouse.tenant_id == tenant_id, Warehouse.is_active.is_(True), Branch.is_active.is_(True))
        .order_by(Branch.name.asc(), Warehouse.name.asc())
        .all()
    )


def warehouse_for_tenant(tenant_id: int, warehouse_id) -> Warehouse | None:
    if not warehouse_id:
        return None
    return Warehouse.query.filter_by(id=int(warehouse_id), tenant_id=tenant_id, is_active=True).first()


def stock_for_product(warehouse_id: int, product_id: int) -> Decimal:
    total = (
        db.session.query(func.coalesce(func.sum(WarehouseStock.quantity), 0))
        .filter(WarehouseStock.warehouse_id == warehouse_id, WarehouseStock.product_id == product_id)
        .scalar()
    )
    return Decimal(total or 0)


def stock_map_for_warehouse(tenant_id: int, warehouse_id: int) -> dict[int, Decimal]:
    rows = (
        db.session.query(WarehouseStock.product_id, func.coalesce(func.sum(WarehouseStock.quantity), 0))
        .filter(WarehouseStock.tenant_id == tenant_id, WarehouseStock.warehouse_id == warehouse_id)
        .group_by(WarehouseStock.product_id)
        .all()
    )
    return {product_id: Decimal(qty or 0) for product_id, qty in rows}


def get_or_create_stock(tenant_id: int, warehouse_id: int, product_id: int, batch_id=None) -> WarehouseStock:
    stock = WarehouseStock.query.filter_by(
        tenant_id=tenant_id,
        warehouse_id=warehouse_id,
        product_id=product_id,
        batch_id=batch_id,
    ).first()
    if stock is None:
        stock = WarehouseStock(
            tenant_id=tenant_id,
            warehouse_id=warehouse_id,
            product_id=product_id,
            batch_id=batch_id,
            quantity=Decimal(0),
        )
        db.session.add(stock)
        db.session.flush()
    return stock


def add_stock(tenant_id: int, warehouse_id: int, product_id: int, qty, batch_id=None, reference: str | None = None) -> None:
    quantity = Decimal(str(qty or 0))
    if quantity <= 0:
        return
    stock = get_or_create_stock(tenant_id, warehouse_id, product_id, batch_id)
    stock.add(quantity)
    _movement(tenant_id, product_id, batch_id, None, warehouse_id, quantity, "entrada", reference)


def subtract_stock(tenant_id: int, warehouse_id: int, product_id: int, qty, batch_id=None, reference: str | None = None) -> None:
    quantity = Decimal(str(qty or 0))
    if quantity <= 0:
        return
    stock = get_or_create_stock(tenant_id, warehouse_id, product_id, batch_id)
    stock.subtract(quantity)
    _movement(tenant_id, product_id, batch_id, warehouse_id, None, quantity, "salida", reference)


def sync_default_warehouse_stock(tenant) -> Warehouse:
    """
    Inicializa inventario por bodega desde el stock/lotes actuales.
    Solo llena productos que todavía no tienen registros por bodega.
    """
    warehouse = ensure_default_locations(tenant)
    existing_products = {
        row[0]
        for row in db.session.query(WarehouseStock.product_id)
        .filter(WarehouseStock.tenant_id == tenant.id)
        .distinct()
        .all()
    }

    products = Product.query.filter_by(tenant_id=tenant.id, is_active=True).all()
    for product in products:
        if product.id in existing_products:
            continue
        if product.track_batches:
            batches = ProductBatch.query.filter(
                ProductBatch.tenant_id == tenant.id,
                ProductBatch.product_id == product.id,
                ProductBatch.remaining_quantity > 0,
            ).all()
            for batch in batches:
                add_stock(tenant.id, warehouse.id, product.id, batch.remaining_quantity, batch.id, "migracion")
        elif product.track_stock and (product.stock or 0) > 0:
            add_stock(tenant.id, warehouse.id, product.id, product.stock, None, "migracion")

    return warehouse


def _movement(tenant_id, product_id, batch_id, source_id, target_id, quantity, movement_type, reference=None) -> None:
    db.session.add(StockMovement(
        tenant_id=tenant_id,
        product_id=product_id,
        batch_id=batch_id,
        source_warehouse_id=source_id,
        target_warehouse_id=target_id,
        quantity=quantity,
        movement_type=movement_type,
        reference=reference,
        moved_at=datetime.utcnow(),
    ))
