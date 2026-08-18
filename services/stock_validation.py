"""Validación transaccional de inventario antes de emitir ventas."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from models import db
from models.catalog import Product
from models.locations import WarehouseStock


class InventoryAvailabilityError(Exception):
    """La venta solicita más inventario del disponible en la bodega."""


def sale_stock_map_for_warehouse(tenant_id: int, warehouse_id: int) -> dict[int, Decimal]:
    """Existencia realmente vendible por producto desde una bodega.

    Para productos con lotes se muestra la suma disponible de todos sus lotes
    en la sede. Cada lote se limita por su existencia local y su remanente real
    para no inflar el total cuando alguno de los dos valores está desfasado.
    """
    products = Product.query.filter_by(tenant_id=tenant_id, is_active=True).all()
    rows = WarehouseStock.query.filter_by(
        tenant_id=tenant_id,
        warehouse_id=warehouse_id,
    ).all()
    rows_by_product: dict[int, list[WarehouseStock]] = defaultdict(list)
    for row in rows:
        rows_by_product[row.product_id].append(row)

    result: dict[int, Decimal] = {}
    for product in products:
        product_rows = rows_by_product.get(product.id, [])
        batch_rows = [row for row in product_rows if row.batch_id and row.batch is not None]
        if product.track_batches and batch_rows:
            result[product.id] = sum(
                (
                    max(
                        min(Decimal(row.quantity or 0), Decimal(row.batch.remaining_quantity or 0)),
                        Decimal("0.00"),
                    )
                    for row in batch_rows
                ),
                Decimal("0.00"),
            )
        else:
            result[product.id] = sum(
                (Decimal(row.quantity or 0) for row in product_rows if row.batch_id is None),
                Decimal("0.00"),
            )
    return result


def validate_sale_inventory(tenant_id: int, warehouse_id: int | None, items_data: list[dict]) -> None:
    """Bloquea existencias y valida cada línea antes de descontar inventario.

    Para productos con lotes, asigna en ``items_data`` el lote FIFO que puede
    cubrir la línea completa. Los bloqueos se mantienen hasta el commit o
    rollback de la transacción que emite la factura.
    """
    if not warehouse_id:
        raise InventoryAvailabilityError("Selecciona una bodega para validar el inventario.")

    requested_product_ids = {
        int(item["product_id"])
        for item in items_data
        if item.get("product_id")
    }
    products = {
        product.id: product
        for product in Product.query.filter(
            Product.tenant_id == tenant_id,
            Product.id.in_(requested_product_ids),
        ).all()
    }
    stock_rows = (
        WarehouseStock.query
        .filter(
            WarehouseStock.tenant_id == tenant_id,
            WarehouseStock.warehouse_id == warehouse_id,
            WarehouseStock.product_id.in_(requested_product_ids),
        )
        .with_for_update()
        .all()
    )
    rows_by_product: dict[int, list[WarehouseStock]] = defaultdict(list)
    for row in stock_rows:
        rows_by_product[row.product_id].append(row)

    reserved_by_row: dict[int, Decimal] = defaultdict(lambda: Decimal("0.00"))
    reserved_without_batch: dict[int, Decimal] = defaultdict(lambda: Decimal("0.00"))

    for item in items_data:
        product_id = item.get("product_id")
        if not product_id:
            continue
        product = products.get(int(product_id))
        if product is None:
            raise InventoryAvailabilityError("Uno de los productos de la venta ya no está disponible.")
        if not product.track_stock:
            continue

        try:
            quantity = Decimal(str(item.get("quantity") or 0))
        except (InvalidOperation, TypeError, ValueError):
            quantity = Decimal("0.00")
        if quantity <= 0:
            raise InventoryAvailabilityError(f"La cantidad de {product.name} debe ser mayor a cero.")

        product_rows = rows_by_product.get(product.id, [])
        batch_rows = [row for row in product_rows if row.batch_id and row.batch is not None]
        if product.track_batches and batch_rows:
            _reserve_batch_stock(product, item, quantity, batch_rows, reserved_by_row)
            continue

        non_batch_rows = [row for row in product_rows if row.batch_id is None]
        available = sum(Decimal(row.quantity or 0) for row in non_batch_rows)
        remaining = available - reserved_without_batch[product.id]
        if remaining < quantity:
            raise InventoryAvailabilityError(
                _insufficient_message(product.name, quantity, max(remaining, Decimal("0.00")))
            )
        reserved_without_batch[product.id] += quantity


def _reserve_batch_stock(
    product: Product,
    item: dict,
    quantity: Decimal,
    rows: list[WarehouseStock],
    reserved_by_row: dict[int, Decimal],
) -> None:
    requested_batch_id = item.get("batch_id")
    candidates = [row for row in rows if not requested_batch_id or row.batch_id == int(requested_batch_id)]
    candidates.sort(key=lambda row: (
        row.batch.expiration_date is None,
        row.batch.expiration_date,
        row.batch.id,
    ))

    total_available = Decimal("0.00")
    for row in candidates:
        row_available = Decimal(row.quantity or 0) - reserved_by_row[row.id]
        batch_available = Decimal(row.batch.remaining_quantity or 0) - reserved_by_row[row.id]
        available = max(min(row_available, batch_available), Decimal("0.00"))
        total_available += available
        if available >= quantity:
            reserved_by_row[row.id] += quantity
            item["batch_id"] = row.batch_id
            return

    raise InventoryAvailabilityError(
        _insufficient_message(product.name, quantity, total_available, require_single_batch=True)
    )


def _insufficient_message(
    product_name: str,
    requested: Decimal,
    available: Decimal,
    require_single_batch: bool = False,
) -> str:
    suffix = " en un mismo lote" if require_single_batch else ""
    return (
        f"Inventario insuficiente para {product_name}: solicitaste {requested:,.2f} "
        f"y hay {available:,.2f} disponible{suffix}."
    )
