"""
Servicios de inventario y vencimientos por tenant.
"""
from __future__ import annotations

from datetime import date, timedelta

from models import db
from models.catalog import ProductBatch, Product


def batches_by_status(tenant_id: int, days_ahead: int = 30) -> dict:
    """
    Devuelve dict con los lotes agrupados por estado:
      - expired:   vencidos con stock todavía
      - expiring:  vencen en los próximos `days_ahead` días
      - active:    vigentes con stock
    Solo cuenta lotes con stock disponible (remaining > 0).
    """
    today = date.today()
    horizon = today + timedelta(days=days_ahead)

    base = (
        db.session.query(ProductBatch)
        .filter(ProductBatch.tenant_id == tenant_id, ProductBatch.remaining_quantity > 0)
    )

    expired = (
        base.filter(ProductBatch.expiration_date.isnot(None),
                    ProductBatch.expiration_date < today)
        .order_by(ProductBatch.expiration_date.asc())
        .all()
    )
    expiring = (
        base.filter(ProductBatch.expiration_date >= today,
                    ProductBatch.expiration_date <= horizon)
        .order_by(ProductBatch.expiration_date.asc())
        .all()
    )

    return {
        "expired": expired,
        "expiring": expiring,
        "expired_count": len(expired),
        "expiring_count": len(expiring),
    }


def consume_from_batch(batch: ProductBatch, qty, warehouse_id: int | None = None) -> None:
    """Resta `qty` del remaining del lote. No deja negativo."""
    from decimal import Decimal
    from services.locations import subtract_stock
    q = Decimal(str(qty or 0))
    new_remaining = Decimal(batch.remaining_quantity or 0) - q
    batch.remaining_quantity = max(new_remaining, Decimal(0))
    if warehouse_id:
        subtract_stock(batch.tenant_id, warehouse_id, batch.product_id, q, batch.id, "venta")


def restore_to_batch(batch: ProductBatch, qty, warehouse_id: int | None = None) -> None:
    """Suma `qty` de regreso al lote (cuando se anula factura)."""
    from decimal import Decimal
    from services.locations import add_stock
    q = Decimal(str(qty or 0))
    batch.remaining_quantity = Decimal(batch.remaining_quantity or 0) + q
    if warehouse_id:
        add_stock(batch.tenant_id, warehouse_id, batch.product_id, q, batch.id, "anulacion")


def recompute_product_stock(product: Product) -> None:
    """
    Refresca product.stock con la suma real de sus lotes.
    Consulta directo a la DB para evitar problemas con relationships en caché.
    """
    from sqlalchemy import func
    total = (
        db.session.query(func.coalesce(func.sum(ProductBatch.remaining_quantity), 0))
        .filter(
            ProductBatch.product_id == product.id,
            ProductBatch.tenant_id == product.tenant_id,
        )
        .scalar()
    )
    product.stock = int(total or 0)


def recompute_all_stocks(tenant_id: int) -> int:
    """
    Recalcula el stock de TODOS los productos del tenant en base a sus lotes.
    Útil para arreglar datos viejos donde el stock quedó desincronizado.
    Retorna cuántos productos se actualizaron.
    """
    products = Product.query.filter_by(tenant_id=tenant_id, track_batches=True).all()
    count = 0
    for p in products:
        recompute_product_stock(p)
        count += 1
    db.session.commit()
    return count
