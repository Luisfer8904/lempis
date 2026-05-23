"""
CRUD de Categorías de productos. Pequeño y simple.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required

from models import db
from models.catalog import Category, Product
from services.tenant_context import current_tenant
from services.permissions import permission_required, tenant_required

categorias_bp = Blueprint("categorias", __name__, url_prefix="/app/categorias")


def _get_or_404(cat_id: int) -> Category:
    tenant = current_tenant()
    cat = Category.query.filter_by(id=cat_id, tenant_id=tenant.id).first()
    if cat is None:
        abort(404)
    return cat


@categorias_bp.route("/")
@login_required
@tenant_required
@permission_required("products.manage")
def list():
    tenant = current_tenant()
    categorias = Category.query.filter_by(tenant_id=tenant.id).order_by(Category.name).all()
    # Contar productos por categoría
    counts = {}
    for cat in categorias:
        counts[cat.id] = Product.query.filter_by(tenant_id=tenant.id, category_id=cat.id).count()
    return render_template("categorias/list.html", categorias=categorias, counts=counts)


@categorias_bp.route("/new", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("products.manage")
def new():
    if request.method == "POST":
        tenant = current_tenant()
        cat = Category(tenant_id=tenant.id)
        _populate_from_form(cat)
        db.session.add(cat)
        db.session.commit()
        flash(f"Categoría '{cat.name}' creada.", "success")
        return redirect(url_for("categorias.list"))
    return render_template("categorias/form.html", categoria=None)


@categorias_bp.route("/<int:cat_id>/edit", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("products.manage")
def edit(cat_id):
    cat = _get_or_404(cat_id)
    if request.method == "POST":
        _populate_from_form(cat)
        db.session.commit()
        flash(f"Categoría '{cat.name}' actualizada.", "success")
        return redirect(url_for("categorias.list"))
    return render_template("categorias/form.html", categoria=cat)


@categorias_bp.route("/<int:cat_id>/delete", methods=["POST"])
@login_required
@tenant_required
@permission_required("products.delete")
def delete(cat_id):
    cat = _get_or_404(cat_id)
    name = cat.name
    db.session.delete(cat)
    db.session.commit()
    flash(f"Categoría '{name}' eliminada.", "info")
    return redirect(url_for("categorias.list"))


def _populate_from_form(cat: Category) -> None:
    cat.name = (request.form.get("name") or "").strip()
    cat.description = (request.form.get("description") or "").strip() or None
    cat.color = (request.form.get("color") or "").strip() or None
