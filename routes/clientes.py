"""
CRUD de Clientes (Customers). Todo aislado por tenant_id.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import or_

from models import db
from models.catalog import Customer
from models.country import Country
from services.tenant_context import current_tenant
from services.permissions import tenant_required
from services.plan_limits import check_can_create_customer, PlanLimitError

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
