"""
CRUD de Productos. Todo aislado por tenant_id.
"""
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required
from sqlalchemy import or_

from models import db
from models.catalog import Product, Category
from models.country import TaxConfig
from services.tenant_context import current_tenant
from services.permissions import tenant_required
from services.plan_limits import check_can_create_product, PlanLimitError

productos_bp = Blueprint("productos", __name__, url_prefix="/app/productos")


def _get_or_404(product_id: int) -> Product:
    tenant = current_tenant()
    prod = Product.query.filter_by(id=product_id, tenant_id=tenant.id).first()
    if prod is None:
        abort(404)
    return prod


def _safe_decimal(value, default="0") -> Decimal:
    try:
        return Decimal(str(value or default))
    except (InvalidOperation, ValueError):
        return Decimal(default)


@productos_bp.route("/")
@login_required
@tenant_required
def list():
    tenant = current_tenant()
    q = (request.args.get("q") or "").strip()
    category_id = request.args.get("category_id", type=int)

    query = Product.query.filter_by(tenant_id=tenant.id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Product.name.ilike(like),
            Product.sku.ilike(like),
            Product.description.ilike(like),
        ))
    if category_id:
        query = query.filter_by(category_id=category_id)

    productos = query.order_by(Product.name.asc()).all()
    categorias = Category.query.filter_by(tenant_id=tenant.id).order_by(Category.name).all()
    return render_template(
        "productos/list.html",
        productos=productos, categorias=categorias,
        q=q, selected_category=category_id,
    )


@productos_bp.route("/new", methods=["GET", "POST"])
@login_required
@tenant_required
def new():
    tenant = current_tenant()

    try:
        check_can_create_product(tenant)
    except PlanLimitError as e:
        flash(str(e), "warning")
        return redirect(url_for("billing.index"))

    if request.method == "POST":
        prod = Product(tenant_id=tenant.id)
        _populate_from_form(prod)
        db.session.add(prod)
        db.session.commit()
        flash(f"Producto '{prod.name}' creado correctamente.", "success")
        return redirect(url_for("productos.list"))

    categorias = Category.query.filter_by(tenant_id=tenant.id).order_by(Category.name).all()
    impuestos = TaxConfig.query.filter_by(country_code=tenant.country_code, is_active=True).all()
    return render_template(
        "productos/form.html",
        producto=None, categorias=categorias, impuestos=impuestos,
    )


@productos_bp.route("/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
@tenant_required
def edit(product_id):
    prod = _get_or_404(product_id)
    if request.method == "POST":
        _populate_from_form(prod)
        db.session.commit()
        flash(f"Producto '{prod.name}' actualizado.", "success")
        return redirect(url_for("productos.list"))

    tenant = current_tenant()
    categorias = Category.query.filter_by(tenant_id=tenant.id).order_by(Category.name).all()
    impuestos = TaxConfig.query.filter_by(country_code=tenant.country_code, is_active=True).all()
    return render_template(
        "productos/form.html",
        producto=prod, categorias=categorias, impuestos=impuestos,
    )


@productos_bp.route("/<int:product_id>/delete", methods=["POST"])
@login_required
@tenant_required
def delete(product_id):
    prod = _get_or_404(product_id)
    name = prod.name
    db.session.delete(prod)
    db.session.commit()
    flash(f"Producto '{name}' eliminado.", "info")
    return redirect(url_for("productos.list"))


def _populate_from_form(prod: Product) -> None:
    prod.sku = (request.form.get("sku") or "").strip()
    prod.name = (request.form.get("name") or "").strip()
    prod.description = (request.form.get("description") or "").strip() or None
    prod.kind = request.form.get("kind") or "product"
    prod.price = _safe_decimal(request.form.get("price"))
    prod.cost = _safe_decimal(request.form.get("cost"))
    prod.stock = int(request.form.get("stock") or 0)
    prod.track_stock = bool(request.form.get("track_stock"))
    prod.track_batches = bool(request.form.get("track_batches"))
    prod.is_active = bool(request.form.get("is_active"))

    cat = request.form.get("category_id")
    prod.category_id = int(cat) if cat else None

    tax = request.form.get("tax_config_id")
    prod.tax_config_id = int(tax) if tax else None
