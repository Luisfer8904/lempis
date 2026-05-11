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
from models.catalog import Customer, Product
from models.country import TaxConfig


class CAIError(Exception):
    """El tenant no puede emitir facturas por falta o vencimiento de CAI."""


def validate_can_emit(tenant: Tenant) -> None:
    """Lanza CAIError si el tenant no puede emitir facturas."""
    if not tenant.has_cai_configured():
        raise CAIError(
            "Debes configurar tu CAI en Configuración → Facturación antes de emitir."
        )
    now = datetime.utcnow()
    if tenant.cai_valid_until and tenant.cai_valid_until < now:
        raise CAIError(
            f"Tu CAI venció el {tenant.cai_valid_until.strftime('%d/%m/%Y')}. "
            "Solicita uno nuevo en el SAR."
        )
    if tenant.next_invoice_number > tenant.cai_range_end:
        raise CAIError(
            f"Agotaste tu rango de facturas autorizado ({tenant.cai_range_end}). "
            "Solicita un nuevo CAI en el SAR."
        )


def next_invoice_number(tenant: Tenant) -> tuple[int, str]:
    """
    Devuelve (correlativo, número_formateado) para la próxima factura.
    NO incrementa el contador — solo lo lee.
    """
    correlativo = tenant.next_invoice_number or tenant.cai_range_start or 1
    return correlativo, tenant.format_invoice_number(correlativo)


def issue_invoice(
    tenant: Tenant,
    customer: Optional[Customer],
    items_data: list[dict],
    payment_method: str = "efectivo",
    payment_terms_days: int = 0,
    notes: str = "",
    issued_by_user_id: Optional[int] = None,
    status: str = "issued",
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

    inv = Invoice(
        tenant_id=tenant.id,
        number=formatted,
        issue_date=datetime.utcnow(),
        customer_id=customer.id if customer else None,
        issued_by_user_id=issued_by_user_id,
        currency=tenant.currency,
        status=status,
        payment_method=payment_method,
        payment_terms_days=int(payment_terms_days or 0),
        notes=notes or None,
        # Congelar datos SAR
        cai_code=tenant.cai_code,
        cai_range_start=tenant.cai_range_start,
        cai_range_end=tenant.cai_range_end,
        cai_valid_until=tenant.cai_valid_until,
        # Congelar emisor
        emisor_name=tenant.legal_name or tenant.name,
        emisor_tax_id=tenant.tax_id,
        emisor_address=tenant.address,
        # Congelar receptor
        receptor_name=customer.name if customer else None,
        receptor_tax_id=customer.tax_id if customer else None,
    )

    # Fecha de vencimiento si es crédito
    if payment_method == "credito" and payment_terms_days:
        inv.due_date = inv.issue_date + timedelta(days=int(payment_terms_days))

    # Líneas
    for data in items_data:
        item = InvoiceItem(
            tenant_id=tenant.id,
            product_id=data.get("product_id"),
            description=data.get("description") or "",
            quantity=Decimal(str(data.get("quantity") or 1)),
            unit_price=Decimal(str(data.get("unit_price") or 0)),
            tax_rate=Decimal(str(data.get("tax_rate") or 0)),
            discount_amount=Decimal(str(data.get("discount_amount") or 0)),
        )
        item.recalc()
        inv.items.append(item)

    inv.recalc_totals()

    db.session.add(inv)

    # Avanzar el correlativo solo si emitimos (no en draft)
    if status != "draft":
        tenant.next_invoice_number = correlativo + 1

    db.session.commit()
    return inv


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

    # Reemplazar líneas (más simple que diff)
    for old_item in list(invoice.items):
        db.session.delete(old_item)
    invoice.items = []

    for data in items_data:
        item = InvoiceItem(
            tenant_id=invoice.tenant_id,
            product_id=data.get("product_id"),
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
