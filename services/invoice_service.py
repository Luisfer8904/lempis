"""
Lógica de negocio para facturas:
- Asignación del próximo número correlativo respetando el rango del CAI
- Congelar datos del emisor/receptor/CAI al momento de emitir
- Recalcular totales con impuestos
"""
from __future__ import annotations  # type hints lazy (compatibilidad)

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from models import db
from models.tenant import Tenant
from models.invoice import Invoice, InvoiceItem
from models.catalog import Customer, Product, ProductBatch
from models.country import TaxConfig
from services.inventory import consume_from_batch, restore_to_batch, recompute_product_stock
from services.locations import subtract_stock, add_stock, warehouse_for_tenant


class CAIError(Exception):
    """Error de emisión (mantenido por compatibilidad con código existente).

    Internamente delega a InvoiceProviderError pero conserva el nombre
    para no romper imports en routes/facturas.py y routes/pos.py.
    """


def validate_can_emit(tenant: Tenant) -> None:
    """Valida si el tenant puede emitir según su modo de facturación."""
    from services.invoice_mode import get_provider, InvoiceProviderError
    try:
        get_provider(tenant).validate_can_emit(tenant)
    except InvoiceProviderError as e:
        raise CAIError(str(e))


def next_invoice_number(tenant: Tenant) -> tuple[int, str]:
    """
    Devuelve (correlativo, número_formateado) para la próxima factura.
    NO incrementa el contador — solo lo lee. Delega al provider del tenant.
    """
    from services.invoice_mode import get_provider
    return get_provider(tenant).next_number(tenant)


def issue_invoice(
    tenant: Tenant,
    customer: Optional[Customer],
    items_data: list[dict],
    payment_method: str = "efectivo",
    payment_terms_days: int = 0,
    notes: str = "",
    issued_by_user_id: Optional[int] = None,
    status: str = "issued",
    warehouse_id: Optional[int] = None,
) -> Invoice:
    """
    Crea una factura, asigna número correlativo, congela datos SAR
    y guarda en DB. Incrementa el contador del tenant atómicamente.

    items_data: lista de dicts con
      {product_id, description, quantity, unit_price, tax_rate, discount_amount}
    """
    if status != "draft":
        validate_can_emit(tenant)

    correlativo, formatted = next_invoice_number(tenant)
    warehouse = warehouse_for_tenant(tenant.id, warehouse_id)

    inv = Invoice(
        tenant_id=tenant.id,
        number=formatted,
        issue_date=datetime.utcnow(),
        customer_id=customer.id if customer else None,
        issued_by_user_id=issued_by_user_id,
        warehouse_id=warehouse.id if warehouse else None,
        currency=tenant.currency,
        status=status,
        payment_method=payment_method,
        payment_terms_days=int(payment_terms_days or 0),
        notes=notes or None,
        # Congelar receptor (común a todos los modos)
        receptor_name=customer.name if customer else None,
        receptor_tax_id=customer.tax_id if customer else None,
    )

    # Congelar datos específicos del modo (CAI, RTN, etc.)
    from services.invoice_mode import get_provider
    get_provider(tenant).freeze_invoice(tenant, inv)

    # Fecha de vencimiento si es crédito
    if payment_method == "credito" and payment_terms_days:
        inv.due_date = inv.issue_date + timedelta(days=int(payment_terms_days))

    # Líneas
    for data in items_data:
        product_id = data.get("product_id")
        batch_id = data.get("batch_id")
        qty = Decimal(str(data.get("quantity") or 1))

        # Resolver lote: si no se mandó, usar FIFO del producto
        batch = _resolve_batch(tenant.id, product_id, batch_id, qty, status, warehouse.id if warehouse else None)

        item = InvoiceItem(
            tenant_id=tenant.id,
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

        # Si emitimos, descontar inventario y actualizar stock del producto
        if status != "draft" and batch is not None:
            consume_from_batch(batch, qty, warehouse.id if warehouse else None)
            recompute_product_stock(batch.product)
        elif status != "draft" and product_id:
            product = db.session.get(Product, int(product_id))
            if product is not None and product.tenant_id == tenant.id and product.track_stock:
                product.stock = max(0, int((product.stock or 0) - qty))
                if warehouse:
                    subtract_stock(tenant.id, warehouse.id, product.id, qty, None, "venta")

    inv.recalc_totals()
    if status != "draft" and payment_method != "credito":
        inv.status = "paid"
        inv.amount_paid = inv.total

    db.session.add(inv)

    # Avanzar el correlativo solo si emitimos (no en draft)
    if status != "draft":
        tenant.next_invoice_number = correlativo + 1

    db.session.commit()
    return inv


def _resolve_batch(tenant_id: int, product_id, batch_id, qty: Decimal, status: str, warehouse_id: int | None = None):
    """
    Resuelve qué lote usar para una línea:
      - Si batch_id fue enviado, lo usa (validando que pertenezca al tenant/producto).
      - Si no, busca el próximo a vencer con stock suficiente (FIFO).
    Retorna ProductBatch o None (si el producto no maneja lotes).
    """
    if not product_id:
        return None
    product = db.session.get(Product, int(product_id))
    if product is None or product.tenant_id != tenant_id or not product.track_batches:
        return None

    if batch_id:
        b = db.session.get(ProductBatch, int(batch_id))
        if b and b.tenant_id == tenant_id and b.product_id == product.id:
            return b
        # batch_id inválido → caer a FIFO
    if warehouse_id:
        from models.locations import WarehouseStock
        row = (
            db.session.query(ProductBatch)
            .join(WarehouseStock, WarehouseStock.batch_id == ProductBatch.id)
            .filter(
                ProductBatch.tenant_id == tenant_id,
                ProductBatch.product_id == product.id,
                WarehouseStock.warehouse_id == warehouse_id,
                WarehouseStock.quantity > 0,
                ProductBatch.remaining_quantity > 0,
            )
            .order_by(ProductBatch.expiration_date.asc(), ProductBatch.id.asc())
            .first()
        )
        if row is not None:
            return row
    return product.next_batch_to_consume()


def update_invoice(
    invoice: Invoice,
    items_data: list[dict],
    payment_method: str = None,
    payment_terms_days: int = None,
    notes: str = None,
    status: str = None,
) -> Invoice:
    """Actualiza una factura en estado draft (no se permite editar emitidas)."""
    if invoice.status != "draft" and status != "void":
        raise CAIError(
            "Solo puedes editar facturas en borrador. "
            "Para corregir una emitida, anúlala con Nota de Crédito."
        )

    if payment_method is not None:
        invoice.payment_method = payment_method
    if payment_terms_days is not None:
        invoice.payment_terms_days = int(payment_terms_days or 0)
    if notes is not None:
        invoice.notes = notes or None
    if status is not None:
        invoice.status = status

    # Reemplazar líneas (más simple que diff). Como solo se editan drafts
    # y los drafts NO descuentan stock, no hay que devolver al lote.
    for old_item in list(invoice.items):
        db.session.delete(old_item)
    invoice.items = []

    for data in items_data:
        item = InvoiceItem(
            tenant_id=invoice.tenant_id,
            product_id=data.get("product_id"),
            batch_id=data.get("batch_id") or None,
            description=data.get("description") or "",
            quantity=Decimal(str(data.get("quantity") or 1)),
            unit_price=Decimal(str(data.get("unit_price") or 0)),
            tax_rate=Decimal(str(data.get("tax_rate") or 0)),
            discount_amount=Decimal(str(data.get("discount_amount") or 0)),
        )
        item.recalc()
        invoice.items.append(item)

    invoice.recalc_totals()
    db.session.commit()
    return invoice


def void_invoice_and_restore_stock(invoice: Invoice) -> None:
    """Anula la factura y devuelve el stock a los lotes correspondientes."""
    if invoice.status == "void":
        return
    for it in invoice.items:
        if it.batch_id:
            b = db.session.get(ProductBatch, it.batch_id)
            if b is not None:
                restore_to_batch(b, it.quantity, invoice.warehouse_id)
                if b.product:
                    recompute_product_stock(b.product)
        elif it.product_id:
            product = db.session.get(Product, it.product_id)
            if product is not None and product.track_stock:
                product.stock = int(product.stock or 0) + int(it.quantity or 0)
                if invoice.warehouse_id:
                    add_stock(invoice.tenant_id, invoice.warehouse_id, product.id, it.quantity, None, "anulacion")
    invoice.status = "void"
    db.session.commit()
