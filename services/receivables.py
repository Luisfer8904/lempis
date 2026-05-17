"""
Cuentas por cobrar:
- Registrar abonos a facturas
- Calcular saldos por cliente
- Aging (0-30 / 31-60 / 61-90 / 90+)
- Auto-marcar facturas como overdue
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, and_

from models import db
from models.invoice import Invoice, InvoicePayment
from models.catalog import Customer


class ReceivableError(Exception):
    """Error al registrar/procesar un cobro."""


# ============================================================
#  Registrar abono
# ============================================================

def record_invoice_payment(
    invoice: Invoice,
    amount,
    payment_method: str = "efectivo",
    reference: str = "",
    notes: str = "",
    user_id: Optional[int] = None,
    paid_at: Optional[datetime] = None,
) -> InvoicePayment:
    """Registra un pago/abono contra una factura.
    Actualiza amount_paid y el status (paid / partially_paid)."""
    if invoice.status == "void":
        raise ReceivableError("No se puede abonar a una factura anulada.")
    if invoice.status == "draft":
        raise ReceivableError("La factura está en borrador; emítela antes de cobrar.")

    amt = Decimal(str(amount or 0))
    if amt <= 0:
        raise ReceivableError("El monto del abono debe ser mayor a cero.")
    if amt > invoice.amount_due:
        raise ReceivableError(
            f"El abono ({amt}) excede el saldo pendiente ({invoice.amount_due})."
        )

    payment = InvoicePayment(
        tenant_id=invoice.tenant_id,
        invoice_id=invoice.id,
        received_by_user_id=user_id,
        amount=amt,
        payment_method=payment_method or "efectivo",
        reference=(reference or "").strip() or None,
        notes=(notes or "").strip() or None,
        paid_at=paid_at or datetime.utcnow(),
    )
    db.session.add(payment)

    # Actualizar acumulado en la factura
    invoice.amount_paid = Decimal(invoice.amount_paid or 0) + amt

    # Auto-status
    if invoice.amount_due <= Decimal("0.005"):  # tolerancia de centavos
        invoice.status = "paid"
    else:
        invoice.status = "partially_paid"

    db.session.commit()
    return payment


def revert_payment(payment: InvoicePayment) -> None:
    """Elimina un pago y revierte el saldo. Útil para correcciones."""
    inv = payment.invoice
    inv.amount_paid = max(
        Decimal(0),
        Decimal(inv.amount_paid or 0) - Decimal(payment.amount or 0),
    )
    # Recalcular status
    if inv.amount_paid <= Decimal("0.005"):
        inv.status = "issued" if not inv.is_overdue else "overdue"
    elif inv.amount_due <= Decimal("0.005"):
        inv.status = "paid"
    else:
        inv.status = "partially_paid"

    db.session.delete(payment)
    db.session.commit()


# ============================================================
#  Auto-actualizar overdue
# ============================================================

def update_overdue_invoices(tenant_id: int) -> int:
    """Marca como 'overdue' todas las facturas a crédito con saldo y vencidas.
    Retorna cuántas se actualizaron."""
    now = datetime.utcnow()
    candidates = Invoice.query.filter(
        Invoice.tenant_id == tenant_id,
        Invoice.status.in_(["issued", "partially_paid"]),
        Invoice.due_date.isnot(None),
        Invoice.due_date < now,
    ).all()
    n = 0
    for inv in candidates:
        if inv.amount_due > 0:
            inv.status = "overdue"
            n += 1
    if n:
        db.session.commit()
    return n


# ============================================================
#  Resumen y aging
# ============================================================

def customer_balances(tenant_id: int):
    """
    Devuelve lista de clientes con saldo pendiente.
    Cada item: {customer, invoices_count, total_due, oldest_due_date, overdue_amount}
    """
    rows = (
        db.session.query(
            Customer,
            func.count(Invoice.id).label("invoices_count"),
            func.coalesce(func.sum(Invoice.total - Invoice.amount_paid), 0).label("total_due"),
            func.min(Invoice.due_date).label("oldest_due_date"),
        )
        .join(Invoice, Invoice.customer_id == Customer.id)
        .filter(
            Customer.tenant_id == tenant_id,
            Invoice.status.in_(["issued", "partially_paid", "overdue"]),
            Invoice.payment_method == "credito",
        )
        .group_by(Customer.id)
        .having(func.sum(Invoice.total - Invoice.amount_paid) > 0)
        .order_by(func.sum(Invoice.total - Invoice.amount_paid).desc())
        .all()
    )
    return rows


def aging_summary(tenant_id: int, customer_id: Optional[int] = None) -> dict:
    """
    Resumen aging: cuánto está pendiente por bucket de edad.
    Buckets: 'current' (no vencido), '0-30', '31-60', '61-90', '90+'.
    """
    now = datetime.utcnow()
    q = Invoice.query.filter(
        Invoice.tenant_id == tenant_id,
        Invoice.status.in_(["issued", "partially_paid", "overdue"]),
        Invoice.payment_method == "credito",
    )
    if customer_id:
        q = q.filter(Invoice.customer_id == customer_id)

    buckets = {"current": Decimal(0), "0_30": Decimal(0),
               "31_60": Decimal(0), "61_90": Decimal(0), "90_plus": Decimal(0)}
    for inv in q.all():
        due = inv.amount_due
        if due <= 0:
            continue
        if not inv.due_date or inv.due_date >= now:
            buckets["current"] += due
            continue
        days = (now - inv.due_date).days
        if days <= 30:
            buckets["0_30"] += due
        elif days <= 60:
            buckets["31_60"] += due
        elif days <= 90:
            buckets["61_90"] += due
        else:
            buckets["90_plus"] += due

    buckets["total"] = sum(buckets.values())
    buckets["overdue_total"] = (
        buckets["0_30"] + buckets["31_60"] + buckets["61_90"] + buckets["90_plus"]
    )
    return buckets


def receivables_summary(tenant_id: int) -> dict:
    """Resumen rápido para el dashboard."""
    update_overdue_invoices(tenant_id)
    total_due = (
        db.session.query(func.coalesce(func.sum(Invoice.total - Invoice.amount_paid), 0))
        .filter(
            Invoice.tenant_id == tenant_id,
            Invoice.status.in_(["issued", "partially_paid", "overdue"]),
            Invoice.payment_method == "credito",
        )
        .scalar()
    )
    overdue = (
        db.session.query(func.coalesce(func.sum(Invoice.total - Invoice.amount_paid), 0))
        .filter(
            Invoice.tenant_id == tenant_id,
            Invoice.status == "overdue",
        )
        .scalar()
    )
    overdue_count = (
        db.session.query(func.count(Invoice.id))
        .filter(
            Invoice.tenant_id == tenant_id,
            Invoice.status == "overdue",
        )
        .scalar()
    )
    return {
        "total_due": float(total_due or 0),
        "overdue_amount": float(overdue or 0),
        "overdue_count": int(overdue_count or 0),
    }
