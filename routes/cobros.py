"""
Cuentas por cobrar:
- /app/cobros                  → vista general: clientes con saldo + aging
- /app/cobros/cliente/<id>     → detalle por cliente con todas sus facturas
- /app/cobros/factura/<id>/abono   → registrar abono (también accesible desde detalle de factura)
"""
from __future__ import annotations

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import or_

from models import db
from models.invoice import Invoice, InvoicePayment
from models.catalog import Customer
from services.tenant_context import current_tenant
from services.permissions import tenant_required
from services.receivables import (
    record_invoice_payment, revert_payment,
    customer_balances, aging_summary, update_overdue_invoices,
    ReceivableError,
)

cobros_bp = Blueprint("cobros", __name__, url_prefix="/app/cobros")


@cobros_bp.route("/")
@login_required
@tenant_required
def index():
    tenant = current_tenant()
    update_overdue_invoices(tenant.id)
    balances = customer_balances(tenant.id)
    aging = aging_summary(tenant.id)
    return render_template(
        "cobros/index.html",
        balances=balances,
        aging=aging,
        tenant=tenant,
    )


@cobros_bp.route("/cliente/<int:customer_id>")
@login_required
@tenant_required
def cliente(customer_id):
    tenant = current_tenant()
    update_overdue_invoices(tenant.id)
    customer = Customer.query.filter_by(id=customer_id, tenant_id=tenant.id).first_or_404()

    facturas = (
        Invoice.query.filter(
            Invoice.tenant_id == tenant.id,
            Invoice.customer_id == customer.id,
            Invoice.payment_method == "credito",
        )
        .order_by(Invoice.due_date.asc().nullslast(), Invoice.issue_date.desc())
        .all()
    )
    pendientes = [f for f in facturas if f.amount_due > 0]
    aging = aging_summary(tenant.id, customer_id=customer.id)

    return render_template(
        "cobros/cliente.html",
        customer=customer,
        facturas=facturas,
        pendientes=pendientes,
        aging=aging,
        tenant=tenant,
    )


@cobros_bp.route("/factura/<int:invoice_id>/abono", methods=["POST"])
@login_required
@tenant_required
def abonar(invoice_id):
    tenant = current_tenant()
    inv = Invoice.query.filter_by(id=invoice_id, tenant_id=tenant.id).first_or_404()
    try:
        record_invoice_payment(
            inv,
            amount=request.form.get("amount") or 0,
            payment_method=request.form.get("payment_method") or "efectivo",
            reference=request.form.get("reference") or "",
            notes=request.form.get("notes") or "",
            user_id=current_user.id,
        )
        msg = f"Abono registrado. Saldo: {tenant.currency} {inv.amount_due:.2f}"
        flash(msg, "success")
    except ReceivableError as e:
        flash(str(e), "danger")

    # Redireccionar a donde venía
    back = request.form.get("back")
    if back == "cliente" and inv.customer_id:
        return redirect(url_for("cobros.cliente", customer_id=inv.customer_id))
    if back == "factura":
        return redirect(url_for("facturas.detail", invoice_id=inv.id))
    return redirect(url_for("cobros.index"))


@cobros_bp.route("/pago/<int:payment_id>/revertir", methods=["POST"])
@login_required
@tenant_required
def revertir(payment_id):
    tenant = current_tenant()
    p = InvoicePayment.query.filter_by(id=payment_id, tenant_id=tenant.id).first_or_404()
    invoice_id = p.invoice_id
    customer_id = p.invoice.customer_id
    revert_payment(p)
    flash("Pago revertido. Saldo recalculado.", "warning")
    back = request.form.get("back")
    if back == "factura":
        return redirect(url_for("facturas.detail", invoice_id=invoice_id))
    if back == "cliente" and customer_id:
        return redirect(url_for("cobros.cliente", customer_id=customer_id))
    return redirect(url_for("cobros.index"))
