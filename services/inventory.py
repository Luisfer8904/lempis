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


def consume_from_batch(batch: ProductBatch, qty) -> None:
    """Resta `qty` del remaining del lote. No deja negativo."""
    from decimal import Decimal
    q = Decimal(str(qty or 0))
    new_remaining = Decimal(batch.remaining_quantity or 0) - q
    batch.remaining_quantity = max(new_remaining, Decimal(0))


def restore_to_batch(batch: ProductBatch, qty) -> None:
    """Suma `qty` de regreso al lote (cuando se anula factura)."""
    from decimal import Decimal
    q = Decimal(str(qty or 0))
    batch.remaining_quantity = Decimal(batch.remaining_quantity or 0) + q


def recompute_product_stock(product: Product) -> None:
    """Refresca product.stock con la suma de sus lotes."""
    product.stock = product.total_stock_from_batches()
