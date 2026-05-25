"""
CRUD de Proveedores.
"""
from __future__ import annotations

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required
from sqlalchemy import or_

from models import db
from models.suppliers import Supplier
from models.country import Country
from services.tenant_context import current_tenant
from services.permissions import permission_required, tenant_required
from services.plan_limits import PlanLimitError, check_can_create_supplier

proveedores_bp = Blueprint("proveedores", __name__, url_prefix="/app/proveedores")


def _get_or_404(supplier_id: int) -> Supplier:
    tenant = current_tenant()
    s = Supplier.query.filter_by(id=supplier_id, tenant_id=tenant.id).first()
    if s is None:
        abort(404)
    return s


def _next_code(tenant_id: int) -> str:
    n = (Supplier.query.filter_by(tenant_id=tenant_id).count() or 0) + 1
    return f"P-{n:04d}"


@proveedores_bp.route("/")
@login_required
@tenant_required
@permission_required("purchases.view")
def list():
    tenant = current_tenant()
    q = (request.args.get("q") or "").strip()

    query = Supplier.query.filter_by(tenant_id=tenant.id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Supplier.name.ilike(like),
            Supplier.legal_name.ilike(like),
            Supplier.tax_id.ilike(like),
            Supplier.email.ilike(like),
            Supplier.code.ilike(like),
            Supplier.phone.ilike(like),
        ))

    proveedores = query.order_by(Supplier.name.asc()).all()
    return render_template("proveedores/list.html", proveedores=proveedores, q=q)


@proveedores_bp.route("/new", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("purchases.manage")
def new():
    tenant = current_tenant()
    try:
        check_can_create_supplier(tenant)
    except PlanLimitError as e:
        flash(str(e), "warning")
        return redirect(url_for("billing.index"))

    if request.method == "POST":
        s = Supplier(tenant_id=tenant.id, code=_next_code(tenant.id))
        _populate(s)
        db.session.add(s)
        db.session.commit()
        flash(f"Proveedor '{s.name}' creado correctamente.", "success")
        return redirect(url_for("proveedores.list"))

    countries = Country.query.filter_by(is_active=True).order_by(Country.name).all()
    return render_template(
        "proveedores/form.html",
        proveedor=None, countries=countries,
        default_country=tenant.country_code,
    )


@proveedores_bp.route("/<int:supplier_id>/edit", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("purchases.manage")
def edit(supplier_id):
    s = _get_or_404(supplier_id)
    if request.method == "POST":
        _populate(s)
        db.session.commit()
        flash(f"Proveedor '{s.name}' actualizado.", "success")
        return redirect(url_for("proveedores.list"))

    countries = Country.query.filter_by(is_active=True).order_by(Country.name).all()
    return render_template(
        "proveedores/form.html",
        proveedor=s, countries=countries,
        default_country=s.country_code or current_tenant().country_code,
    )


@proveedores_bp.route("/<int:supplier_id>/delete", methods=["POST"])
@login_required
@tenant_required
@permission_required("purchases.manage")
def delete(supplier_id):
    s = _get_or_404(supplier_id)
    if s.purchases.count() > 0:
        flash(
            f"No se puede eliminar a '{s.name}': tiene {s.purchases.count()} compra(s) asociada(s). "
            "Márcalo como inactivo en su lugar.",
            "warning",
        )
        return redirect(url_for("proveedores.list"))

    name = s.name
    db.session.delete(s)
    db.session.commit()
    flash(f"Proveedor '{name}' eliminado.", "info")
    return redirect(url_for("proveedores.list"))


def _populate(s: Supplier) -> None:
    s.name = (request.form.get("name") or "").strip()
    s.legal_name = (request.form.get("legal_name") or "").strip() or None
    s.tax_id = (request.form.get("tax_id") or "").strip() or None
    s.contact_name = (request.form.get("contact_name") or "").strip() or None
    s.email = (request.form.get("email") or "").strip() or None
    s.phone = (request.form.get("phone") or "").strip() or None
    s.address = (request.form.get("address") or "").strip() or None
    s.city = (request.form.get("city") or "").strip() or None
    s.country_code = (request.form.get("country_code") or "").strip() or None
    s.payment_terms_days = int(request.form.get("payment_terms_days") or 0)
    try:
        s.credit_limit = float(request.form.get("credit_limit") or 0)
    except ValueError:
        s.credit_limit = 0
    s.price_includes_tax = bool(request.form.get("price_includes_tax"))
    s.notes = (request.form.get("notes") or "").strip() or None
    s.is_active = bool(request.form.get("is_active"))
