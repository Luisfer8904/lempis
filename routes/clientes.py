"""
CRUD de Clientes (Customers). Todo aislado por tenant_id.
"""
from __future__ import annotations

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import login_required
from sqlalchemy import or_

from models import db
from models.catalog import Customer
from models.country import Country
from models.invoice import Invoice, InvoicePayment
from services.tenant_context import current_tenant
from services.permissions import permission_required, tenant_required
from services.plan_limits import check_can_create_customer, PlanLimitError
from services.receivables import update_overdue_invoices

clientes_bp = Blueprint("clientes", __name__, url_prefix="/app/clientes")


def _get_or_404(client_id: int) -> Customer:
    """Carga un cliente del tenant actual o 404."""
    tenant = current_tenant()
    cliente = Customer.query.filter_by(id=client_id, tenant_id=tenant.id).first()
    if cliente is None:
        abort(404)
    return cliente


@clientes_bp.route("/")
@login_required
@tenant_required
@permission_required("customers.view")
def list():
    tenant = current_tenant()
    q = (request.args.get("q") or "").strip()
    update_overdue_invoices(tenant.id)

    query = Customer.query.filter_by(tenant_id=tenant.id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Customer.name.ilike(like),
            Customer.tax_id.ilike(like),
            Customer.email.ilike(like),
            Customer.phone.ilike(like),
        ))

    clientes = query.order_by(Customer.name.asc()).all()
    summaries = _customer_summaries(tenant, clientes)
    return render_template("clientes/list.html", clientes=clientes, q=q, summaries=summaries)


@clientes_bp.route("/<int:client_id>")
@login_required
@tenant_required
@permission_required("customers.view")
def detail(client_id):
    tenant = current_tenant()
    cliente = _get_or_404(client_id)
    update_overdue_invoices(tenant.id)

    invoices = (
        Invoice.query
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.customer_id == cliente.id,
            Invoice.status.notin_(["draft", "void"]),
        )
        .order_by(Invoice.issue_date.desc())
        .all()
    )
    pending_invoices = [
        inv for inv in invoices
        if inv.payment_method == "credito" and inv.amount_due > 0
    ]
    paid_invoices = [
        inv for inv in invoices
        if inv.payment_method == "credito" and inv.amount_due <= 0
    ]
    cash_invoices = [
        inv for inv in invoices
        if inv.payment_method in ("efectivo", "transferencia", "tarjeta")
    ]
    payments = (
        InvoicePayment.query
        .join(Invoice, Invoice.id == InvoicePayment.invoice_id)
        .filter(
            InvoicePayment.tenant_id == tenant.id,
            Invoice.customer_id == cliente.id,
        )
        .order_by(InvoicePayment.paid_at.desc())
        .all()
    )
    summary = _customer_summaries(tenant, [cliente]).get(cliente.id, _empty_summary())

    return render_template(
        "clientes/detail.html",
        cliente=cliente,
        invoices=invoices,
        pending_invoices=pending_invoices,
        paid_invoices=paid_invoices,
        cash_invoices=cash_invoices,
        payments=payments,
        summary=summary,
        tenant=tenant,
    )


@clientes_bp.route("/new", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("customers.manage")
def new():
    tenant = current_tenant()

    # Validar límite del plan ANTES de procesar el form
    try:
        check_can_create_customer(tenant)
    except PlanLimitError as e:
        flash(str(e), "warning")
        return redirect(url_for("billing.index"))

    if request.method == "POST":
        cliente = Customer(tenant_id=tenant.id)
        _populate_from_form(cliente)
        db.session.add(cliente)
        db.session.commit()
        flash(f"Cliente '{cliente.name}' creado correctamente.", "success")
        return redirect(url_for("clientes.list"))

    countries = Country.query.filter_by(is_active=True).order_by(Country.name).all()
    return render_template(
        "clientes/form.html",
        cliente=None,
        countries=countries,
        default_country=tenant.country_code,
    )


@clientes_bp.route("/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("customers.manage")
def edit(client_id):
    cliente = _get_or_404(client_id)
    if request.method == "POST":
        _populate_from_form(cliente)
        db.session.commit()
        flash(f"Cliente '{cliente.name}' actualizado.", "success")
        return redirect(url_for("clientes.list"))

    countries = Country.query.filter_by(is_active=True).order_by(Country.name).all()
    return render_template(
        "clientes/form.html",
        cliente=cliente,
        countries=countries,
        default_country=cliente.country_code or current_tenant().country_code,
    )


@clientes_bp.route("/<int:client_id>/delete", methods=["POST"])
@login_required
@tenant_required
@permission_required("customers.delete")
def delete(client_id):
    cliente = _get_or_404(client_id)
    name = cliente.name
    db.session.delete(cliente)
    db.session.commit()
    flash(f"Cliente '{name}' eliminado.", "info")
    return redirect(url_for("clientes.list"))


@clientes_bp.route("/<int:client_id>/resumen")
@login_required
@tenant_required
@permission_required("customers.view")
def resumen(client_id):
    """Resumen comercial usado al seleccionar cliente en facturación/POS."""
    tenant = current_tenant()
    cliente = _get_or_404(client_id)
    update_overdue_invoices(tenant.id)
    data = _customer_summaries(tenant, [cliente]).get(cliente.id, _empty_summary())

    return jsonify({
        "customer": {
            "id": cliente.id,
            "name": cliente.name,
            "tax_id": cliente.tax_id,
            "email": cliente.email,
            "phone": cliente.phone,
            "city": cliente.city,
            "address": cliente.address,
            "preferred_price_tier": cliente.preferred_price_tier,
            "notes": cliente.notes,
        },
        "currency": tenant.currency,
        **data,
    })


def _customer_summaries(tenant, customers: list[Customer]) -> dict[int, dict]:
    customer_ids = [c.id for c in customers]
    summaries = {customer_id: _empty_summary() for customer_id in customer_ids}
    if not customer_ids:
        return summaries

    invoices = (
        Invoice.query
        .filter(
            Invoice.tenant_id == tenant.id,
            Invoice.customer_id.in_(customer_ids),
            Invoice.status.notin_(["draft", "void"]),
        )
        .order_by(Invoice.issue_date.desc())
        .all()
    )

    for inv in invoices:
        data = summaries[inv.customer_id]
        methods = data["methods"]
        summary = data["summary"]
        recent = data["recent_invoices"]

        total = float(inv.total or 0)
        summary["invoice_count"] += 1
        summary["total_invoiced"] += total
        if inv.payment_method in methods:
            methods[inv.payment_method]["count"] += 1
            methods[inv.payment_method]["total"] += total
        if inv.payment_method in ("efectivo", "transferencia", "tarjeta"):
            summary["cash_count"] += 1
            summary["cash_total"] += total
        if inv.payment_method == "credito" and inv.amount_due > 0:
            summary["pending_balance"] += float(inv.amount_due or 0)
            summary["pending_count"] += 1
            if inv.is_overdue:
                summary["overdue_balance"] += float(inv.amount_due or 0)
        if summary["last_invoice_number"] is None:
            summary["last_invoice_number"] = inv.number
            summary["last_invoice_date"] = inv.issue_date.strftime("%d/%m/%Y") if inv.issue_date else None
            summary["last_invoice_total"] = total
        if len(recent) < 3:
            recent.append({
                "number": inv.number,
                "date": inv.issue_date.strftime("%d/%m/%Y") if inv.issue_date else "",
                "method": inv.payment_method,
                "status": inv.status,
                "total": total,
                "amount_due": float(inv.amount_due or 0),
            })

    for data in summaries.values():
        count = data["summary"]["invoice_count"]
        if count:
            data["summary"]["average_ticket"] = data["summary"]["total_invoiced"] / count
    return summaries


def _empty_summary() -> dict:
    return {
        "summary": {
            "invoice_count": 0,
            "total_invoiced": 0.0,
            "cash_count": 0,
            "cash_total": 0.0,
            "pending_count": 0,
            "pending_balance": 0.0,
            "overdue_balance": 0.0,
            "average_ticket": 0.0,
            "last_invoice_number": None,
            "last_invoice_date": None,
            "last_invoice_total": 0.0,
        },
        "methods": {
            "efectivo": {"count": 0, "total": 0.0},
            "transferencia": {"count": 0, "total": 0.0},
            "tarjeta": {"count": 0, "total": 0.0},
            "credito": {"count": 0, "total": 0.0},
        },
        "recent_invoices": [],
    }


def _populate_from_form(cliente: Customer) -> None:
    """Lee request.form y rellena los campos del cliente."""
    cliente.name = (request.form.get("name") or "").strip()
    cliente.tax_id = (request.form.get("tax_id") or "").strip() or None
    cliente.email = (request.form.get("email") or "").strip() or None
    cliente.phone = (request.form.get("phone") or "").strip() or None
    cliente.address = (request.form.get("address") or "").strip() or None
    cliente.city = (request.form.get("city") or "").strip() or None
    cliente.preferred_price_tier = request.form.get("preferred_price_tier") or "general"
    cliente.country_code = (request.form.get("country_code") or "").strip() or None
    cliente.notes = (request.form.get("notes") or "").strip() or None
    cliente.is_active = bool(request.form.get("is_active"))
