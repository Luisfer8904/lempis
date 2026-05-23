"""
CRUD de Clientes (Customers). Todo aislado por tenant_id.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import login_required, current_user
from sqlalchemy import or_

from models import db
from models.catalog import Customer
from models.country import Country
from models.invoice import Invoice
from services.tenant_context import current_tenant
from services.permissions import tenant_required
from services.plan_limits import check_can_create_customer, PlanLimitError
from services.receivables import aging_summary, update_overdue_invoices

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
def list():
    tenant = current_tenant()
    q = (request.args.get("q") or "").strip()

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
    return render_template("clientes/list.html", clientes=clientes, q=q)


@clientes_bp.route("/new", methods=["GET", "POST"])
@login_required
@tenant_required
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
def resumen(client_id):
    """Resumen comercial usado al seleccionar cliente en facturación/POS."""
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

    methods = {
        "efectivo": {"count": 0, "total": 0.0},
        "transferencia": {"count": 0, "total": 0.0},
        "tarjeta": {"count": 0, "total": 0.0},
        "credito": {"count": 0, "total": 0.0},
    }
    total_invoiced = 0.0
    pending_balance = 0.0
    overdue_balance = 0.0
    pending_count = 0

    for inv in invoices:
        total = float(inv.total or 0)
        total_invoiced += total
        if inv.payment_method in methods:
            methods[inv.payment_method]["count"] += 1
            methods[inv.payment_method]["total"] += total
        if inv.payment_method == "credito" and inv.amount_due > 0:
            pending_balance += float(inv.amount_due or 0)
            pending_count += 1
            if inv.is_overdue:
                overdue_balance += float(inv.amount_due or 0)

    cash_total = (
        methods["efectivo"]["total"]
        + methods["transferencia"]["total"]
        + methods["tarjeta"]["total"]
    )
    cash_count = (
        methods["efectivo"]["count"]
        + methods["transferencia"]["count"]
        + methods["tarjeta"]["count"]
    )

    latest = invoices[0] if invoices else None
    aging = aging_summary(tenant.id, customer_id=cliente.id)

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
        "summary": {
            "invoice_count": len(invoices),
            "total_invoiced": total_invoiced,
            "cash_count": cash_count,
            "cash_total": cash_total,
            "pending_count": pending_count,
            "pending_balance": pending_balance,
            "overdue_balance": overdue_balance,
            "average_ticket": (total_invoiced / len(invoices)) if invoices else 0.0,
            "last_invoice_number": latest.number if latest else None,
            "last_invoice_date": latest.issue_date.strftime("%d/%m/%Y") if latest and latest.issue_date else None,
            "last_invoice_total": float(latest.total or 0) if latest else 0.0,
        },
        "methods": methods,
        "aging": {key: float(value or 0) for key, value in aging.items()},
        "recent_invoices": [{
            "number": inv.number,
            "date": inv.issue_date.strftime("%d/%m/%Y") if inv.issue_date else "",
            "method": inv.payment_method,
            "status": inv.status,
            "total": float(inv.total or 0),
            "amount_due": float(inv.amount_due or 0),
        } for inv in invoices[:3]],
    })


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
