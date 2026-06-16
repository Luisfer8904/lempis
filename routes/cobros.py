"""
Cuentas por cobrar:
- /app/cobros                  → vista general: clientes con saldo + aging
- /app/cobros/cliente/<id>     → detalle por cliente con todas sus facturas
- /app/cobros/factura/<id>/abono   → registrar abono (también accesible desde detalle de factura)
"""
from __future__ import annotations

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort, send_file,
    Response,
)
from flask_login import login_required, current_user
from sqlalchemy import or_

from models import db
from models.invoice import Invoice, InvoicePayment
from models.catalog import Customer
from services.tenant_context import current_tenant
from services.permissions import admin_required, permission_required, tenant_required
from services.receivables import (
    record_invoice_payment, revert_payment,
    customer_balances, aging_summary, update_overdue_invoices,
    ReceivableError,
)

cobros_bp = Blueprint("cobros", __name__, url_prefix="/app/cobros")


@cobros_bp.route("/")
@login_required
@tenant_required
@permission_required("receivables.view")
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
@permission_required("receivables.view")
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
        # NULLs al final (cross-DB MySQL/Postgres/SQLite):
        # is_(None) devuelve 0 para no nulos, 1 para nulos → ordenamos por eso primero
        .order_by(Invoice.due_date.is_(None).asc(), Invoice.due_date.asc(), Invoice.issue_date.desc())
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
@permission_required("receivables.manage")
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


@cobros_bp.route("/pago/<int:payment_id>/recibo.pdf")
@login_required
@tenant_required
@permission_required("receivables.view")
def recibo_pdf(payment_id):
    """Genera el PDF del recibo de pago."""
    from services.receipt_generator import generate_payment_receipt_pdf
    tenant = current_tenant()
    p = InvoicePayment.query.filter_by(id=payment_id, tenant_id=tenant.id).first_or_404()
    buf = generate_payment_receipt_pdf(p, tenant)
    return send_file(
        buf, mimetype="application/pdf",
        as_attachment=True,
        download_name=f"recibo_{p.id:06d}_factura_{p.invoice.number.replace('-', '_')}.pdf",
    )


@cobros_bp.route("/export-aging.csv")
@login_required
@tenant_required
@permission_required("receivables.view")
def export_aging():
    """Exporta el reporte aging de cuentas por cobrar como CSV (abre en Excel)."""
    import csv
    from io import StringIO
    from models.invoice import Invoice
    from datetime import datetime

    tenant = current_tenant()
    update_overdue_invoices(tenant.id)

    facturas = (
        Invoice.query
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.status.in_(["issued", "partially_paid", "overdue"]),
            Invoice.payment_method == "credito",
        )
        .order_by(Invoice.due_date.is_(None).asc(), Invoice.due_date.asc())
        .all()
    )

    output = StringIO()
    # BOM para que Excel detecte UTF-8 con acentos correctamente
    output.write("﻿")
    w = csv.writer(output)
    w.writerow([
        "Factura", "Cliente", "RTN", "Emitida", "Vence", "Días vencida",
        "Bucket", "Total", "Pagado", "Saldo", "Método", "Estado",
    ])

    now = datetime.utcnow()
    for f in facturas:
        if f.amount_due <= 0:
            continue
        if not f.due_date or f.due_date >= now:
            bucket = "Vigente"
            dias = 0
        else:
            dias = (now - f.due_date).days
            if dias <= 30: bucket = "1-30"
            elif dias <= 60: bucket = "31-60"
            elif dias <= 90: bucket = "61-90"
            else: bucket = "90+"

        w.writerow([
            f.number,
            f.receptor_name or (f.customer.name if f.customer else "Consumidor final"),
            f.receptor_tax_id or "",
            f.issue_date.strftime("%Y-%m-%d") if f.issue_date else "",
            f.due_date.strftime("%Y-%m-%d") if f.due_date else "",
            dias,
            bucket,
            f"{float(f.total):.2f}",
            f"{float(f.amount_paid):.2f}",
            f"{float(f.amount_due):.2f}",
            f.payment_method,
            f.status,
        ])

    csv_data = output.getvalue()
    filename = f"aging_{tenant.slug}_{now.strftime('%Y%m%d')}.csv"
    return Response(
        csv_data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@cobros_bp.route("/pago/<int:payment_id>/revertir", methods=["POST"])
@login_required
@tenant_required
@admin_required
def revertir(payment_id):
    tenant = current_tenant()
    p = InvoicePayment.query.filter_by(id=payment_id, tenant_id=tenant.id).first_or_404()
    invoice_id = p.invoice_id
    customer_id = p.invoice.customer_id
    revert_payment(p)
    flash("Cobro eliminado. Saldo recalculado.", "warning")
    back = request.form.get("back")
    if back == "factura":
        return redirect(url_for("facturas.detail", invoice_id=invoice_id))
    if back == "cliente" and customer_id:
        return redirect(url_for("cobros.cliente", customer_id=customer_id))
    return redirect(url_for("cobros.index"))
