"""
Lógica de compras: numeración interna, finalizar compra (alimenta inventario),
actualizar precios de venta, anular compra (revertir stock).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from models import db
from models.tenant import Tenant
from models.suppliers import Supplier
from models.purchases import Purchase, PurchaseItem
from models.catalog import Product, ProductBatch
from services.inventory import consume_from_batch, restore_to_batch, recompute_product_stock
from services.locations import add_stock, subtract_stock, warehouse_for_tenant


class PurchaseError(Exception):
    """Error al procesar una compra."""


# ---------------- Numeración interna ----------------

def next_purchase_number(tenant: Tenant) -> str:
    """Genera el siguiente número correlativo de compra (COMP-000001)."""
    last = (
        Purchase.query.filter_by(tenant_id=tenant.id)
        .order_by(Purchase.id.desc())
        .first()
    )
    if last and last.number and last.number.startswith("COMP-"):
        try:
            n = int(last.number.split("-")[1]) + 1
        except (ValueError, IndexError):
            n = (Purchase.query.filter_by(tenant_id=tenant.id).count() or 0) + 1
    else:
        n = (Purchase.query.filter_by(tenant_id=tenant.id).count() or 0) + 1
    return f"COMP-{n:06d}"


# ---------------- Crear / Actualizar ----------------

def create_purchase(
    tenant: Tenant,
    supplier: Optional[Supplier],
    items_data: list[dict],
    supplier_invoice_number: str = "",
    terms_days: int = 0,
    issue_date: Optional[datetime] = None,
    notes: str = "",
    received_by_user_id: Optional[int] = None,
    warehouse_id: Optional[int] = None,
) -> Purchase:
    """Crea una compra en estado 'draft'. NO afecta inventario."""
    issue_date = issue_date or datetime.utcnow()
    due = issue_date + timedelta(days=terms_days) if terms_days else None

    p = Purchase(
        tenant_id=tenant.id,
        number=next_purchase_number(tenant),
        supplier_invoice_number=supplier_invoice_number or None,
        supplier_id=supplier.id if supplier else None,
        received_by_user_id=received_by_user_id,
        warehouse_id=warehouse_id,
        issue_date=issue_date,
        due_date=due,
        terms_days=int(terms_days or 0),
        currency=tenant.currency,
        status="draft",
        notes=notes or None,
    )

    for d in items_data:
        item = _build_item(tenant.id, d)
        p.items.append(item)

    p.recalc_totals()
    db.session.add(p)
    db.session.commit()
    return p


def update_purchase_draft(
    purchase: Purchase,
    items_data: list[dict],
    supplier_id: Optional[int] = None,
    supplier_invoice_number: Optional[str] = None,
    terms_days: Optional[int] = None,
    notes: Optional[str] = None,
) -> Purchase:
    """Actualiza una compra en estado 'draft'."""
    if purchase.status != "draft":
        raise PurchaseError("Solo se pueden editar compras en borrador.")

    if supplier_id is not None:
        purchase.supplier_id = int(supplier_id) if supplier_id else None
    if supplier_invoice_number is not None:
        purchase.supplier_invoice_number = supplier_invoice_number or None
    if terms_days is not None:
        purchase.terms_days = int(terms_days or 0)
        purchase.due_date = (
            purchase.issue_date + timedelta(days=purchase.terms_days)
            if purchase.terms_days else None
        )
    if notes is not None:
        purchase.notes = notes or None

    # Reemplazar líneas
    for old in list(purchase.items):
        db.session.delete(old)
    purchase.items = []

    for d in items_data:
        purchase.items.append(_build_item(purchase.tenant_id, d))

    purchase.recalc_totals()
    db.session.commit()
    return purchase


def _build_item(tenant_id: int, d: dict) -> PurchaseItem:
    """Construye una PurchaseItem desde dict del formulario."""
    item = PurchaseItem(
        tenant_id=tenant_id,
        product_id=int(d["product_id"]) if d.get("product_id") else None,
        description=d.get("description") or "",
        quantity=Decimal(str(d.get("quantity") or 1)),
        unit_cost=Decimal(str(d.get("unit_cost") or 0)),
        tax_rate=Decimal(str(d.get("tax_rate") or 0)),
        batch_number=(d.get("batch_number") or "").strip() or None,
        manufacturing_date=_parse_date(d.get("manufacturing_date")),
        expiration_date=_parse_date(d.get("expiration_date")),
        new_sale_price=(
            Decimal(str(d["new_sale_price"]))
            if d.get("new_sale_price") not in (None, "", "0", "0.0", "0.00")
            else None
        ),
    )
    item.recalc()
    return item


def _parse_date(value):
    if not value:
        return None
    if hasattr(value, "year"):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------------- Finalizar (recibir mercadería) ----------------

def finalize_purchase(purchase: Purchase) -> Purchase:
    """
    Recibe la mercadería. Para cada línea:
    1. Si el producto maneja lotes y la línea trae batch_number → crear ProductBatch
       (o reusar uno existente con el mismo número y mismas fechas).
    2. Suma la cantidad al stock total del producto (vía recompute_product_stock
       si maneja lotes, o directo si no).
    3. Si la línea trae new_sale_price, actualiza product.price.
    Cambia el status de la compra a 'received'.
    """
    if purchase.status == "received":
        raise PurchaseError("Esta compra ya fue recibida.")
    if purchase.status == "void":
        raise PurchaseError("No puedes recibir una compra anulada.")

    warehouse = warehouse_for_tenant(purchase.tenant_id, purchase.warehouse_id)

    for item in purchase.items:
        if not item.product_id:
            continue
        product = db.session.get(Product, item.product_id)
        if product is None:
            continue

        qty = Decimal(item.quantity or 0)

        if product.track_batches:
            # Crear o reusar lote. Si no escribieron número, generar uno automático
            # para que la compra igualmente alimente inventario.
            batch = _get_or_create_batch(product, item, purchase)
            # Sumar al lote (initial + qty, remaining + qty)
            batch.initial_quantity = Decimal(batch.initial_quantity or 0) + qty
            batch.remaining_quantity = Decimal(batch.remaining_quantity or 0) + qty
            batch.cost = Decimal(item.unit_cost or 0)  # actualiza último costo
            db.session.flush()
            item.batch_id = batch.id
            if warehouse:
                add_stock(purchase.tenant_id, warehouse.id, product.id, qty, batch.id, purchase.number)

        # Actualizar stock del producto
        if product.track_batches:
            recompute_product_stock(product)
        else:
            product.stock = int((product.stock or 0) + qty)
            if warehouse:
                add_stock(purchase.tenant_id, warehouse.id, product.id, qty, None, purchase.number)

        # Actualizar costo de referencia del producto
        if Decimal(item.unit_cost or 0) > 0:
            product.cost = Decimal(item.unit_cost)

        # Actualizar precio de venta si se especificó
        if item.new_sale_price is not None and Decimal(item.new_sale_price) > 0:
            product.price = Decimal(item.new_sale_price)

    purchase.status = "received"
    purchase.received_at = datetime.utcnow()
    db.session.commit()
    return purchase


def repair_received_purchase_inventory(purchase: Purchase) -> int:
    """
    Repara compras ya marcadas como 'received' que quedaron sin reflejar stock
    porque alguna línea de producto con lotes no obtuvo batch_id al recibirla.

    Solo corrige líneas sin batch_id. No duplica las que ya fueron aplicadas.
    Devuelve cuántas líneas fueron reparadas.
    """
    if purchase.status != "received":
        raise PurchaseError("Solo se puede reprocesar inventario en compras recibidas.")

    repaired = 0
    touched_products: set[int] = set()
    warehouse = warehouse_for_tenant(purchase.tenant_id, purchase.warehouse_id)

    for item in purchase.items:
        if not item.product_id or item.batch_id:
            continue

        product = db.session.get(Product, item.product_id)
        if product is None:
            continue

        if not product.track_batches:
            continue

        qty = Decimal(item.quantity or 0)
        batch = _get_or_create_batch(product, item, purchase)
        batch.initial_quantity = Decimal(batch.initial_quantity or 0) + qty
        batch.remaining_quantity = Decimal(batch.remaining_quantity or 0) + qty
        batch.cost = Decimal(item.unit_cost or 0)
        db.session.flush()
        item.batch_id = batch.id
        if warehouse:
            add_stock(purchase.tenant_id, warehouse.id, product.id, qty, batch.id, purchase.number)
        touched_products.add(product.id)
        repaired += 1

    for product_id in touched_products:
        product = db.session.get(Product, product_id)
        if product is not None:
            recompute_product_stock(product)

    db.session.commit()
    return repaired


def _get_or_create_batch(product: Product, item: PurchaseItem, purchase: Purchase) -> ProductBatch:
    """Si ya existe un lote con el mismo número en este producto, lo reusa.
    Si no, crea uno nuevo. Inicializa con quantity=0 (luego sumamos en finalize)."""
    batch_number = (item.batch_number or "").strip() or f"AUTO-{purchase.number}-{item.id or 'X'}"
    item.batch_number = batch_number

    # Buscar lote existente directamente en DB para no depender del caché de la relación
    existing = ProductBatch.query.filter_by(
        tenant_id=product.tenant_id,
        product_id=product.id,
        batch_number=batch_number,
    ).first()
    if existing:
        if item.manufacturing_date:
            existing.manufacturing_date = item.manufacturing_date
        if item.expiration_date:
            existing.expiration_date = item.expiration_date
        return existing

    new_batch = ProductBatch(
        tenant_id=product.tenant_id,
        product_id=product.id,
        batch_number=batch_number,
        manufacturing_date=item.manufacturing_date,
        expiration_date=item.expiration_date,
        initial_quantity=Decimal(0),
        remaining_quantity=Decimal(0),
        cost=item.unit_cost or Decimal(0),
    )
    # Agregar A LA RELACIÓN del producto (no solo a la sesión) para que
    # product.batches refleje el nuevo lote inmediatamente.
    product.batches.append(new_batch)
    db.session.flush()
    return new_batch


# ---------------- Anular ----------------

def void_purchase(purchase: Purchase) -> Purchase:
    """
    Anula la compra. Si estaba recibida, revierte el stock de cada lote/producto.
    """
    if purchase.status == "void":
        return purchase

    if purchase.status == "received":
        warehouse = warehouse_for_tenant(purchase.tenant_id, purchase.warehouse_id)
        for item in purchase.items:
            if not item.product_id:
                continue
            product = db.session.get(Product, item.product_id)
            if product is None:
                continue
            qty = Decimal(item.quantity or 0)

            if item.batch_id:
                batch = db.session.get(ProductBatch, item.batch_id)
                if batch is not None:
                    # Restar lo que entró por esta compra
                    batch.initial_quantity = max(
                        Decimal(0), Decimal(batch.initial_quantity or 0) - qty
                    )
                    batch.remaining_quantity = max(
                        Decimal(0), Decimal(batch.remaining_quantity or 0) - qty
                    )
                    db.session.flush()
                    recompute_product_stock(product)
                    if warehouse:
                        subtract_stock(purchase.tenant_id, warehouse.id, product.id, qty, batch.id, purchase.number)
            else:
                product.stock = max(0, int((product.stock or 0) - qty))
                if warehouse:
                    subtract_stock(purchase.tenant_id, warehouse.id, product.id, qty, None, purchase.number)

    purchase.status = "void"
    db.session.commit()
    return purchase


# ---------------- Pagos a proveedor ----------------

def register_payment(purchase: Purchase, amount) -> Purchase:
    """Registra un pago/abono a la compra."""
    amount = Decimal(str(amount or 0))
    if amount <= 0:
        raise PurchaseError("El monto del pago debe ser mayor a cero.")
    if amount > purchase.amount_due:
        raise PurchaseError(
            f"El pago ({amount}) excede el saldo pendiente ({purchase.amount_due})."
        )
    purchase.amount_paid = Decimal(purchase.amount_paid or 0) + amount
    db.session.commit()
    return purchase
