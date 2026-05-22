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
from services.uploads import save_product_image, delete_product_image

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
    view = request.args.get("view", "grid")  # grid | table

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
        q=q, selected_category=category_id, view=view,
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
        db.session.flush()  # para tener prod.id antes de guardar la imagen

        # Imagen: subida desde archivo > URL manual
        file = request.files.get("image_file")
        if file and file.filename:
            url = save_product_image(file, tenant.id, prod.id)
            if url:
                prod.image_url = url

        db.session.commit()
        flash(f"Producto '{prod.name}' creado correctamente.", "success")
        return redirect(url_for("productos.list"))

    categorias = Category.query.filter_by(tenant_id=tenant.id).order_by(Category.name).all()
    impuestos = TaxConfig.query.filter_by(country_code=tenant.country_code, is_active=True).all()
    return render_template(
        "productos/form.html",
        producto=None, categorias=categorias, impuestos=impuestos,
    )


@productos_bp.route("/<int:product_id>")
@login_required
@tenant_required
def detail(product_id):
    """Vista detalle del producto: info, lotes, últimas compras y ventas."""
    from models.invoice import Invoice, InvoiceItem
    from models.purchases import Purchase, PurchaseItem
    from models.catalog import ProductBatch
    from sqlalchemy import func

    prod = _get_or_404(product_id)
    tenant = current_tenant()

    # Lotes ordenados por vencimiento (más próximos primero, nulos al final)
    lotes = (
        ProductBatch.query
        .filter_by(tenant_id=tenant.id, product_id=prod.id)
        .order_by(
            ProductBatch.expiration_date.is_(None).asc(),
            ProductBatch.expiration_date.asc(),
            ProductBatch.created_at.desc(),
        )
        .all()
    )

    # Últimas 10 compras del producto
    ultimas_compras = (
        db.session.query(Purchase, PurchaseItem)
        .join(PurchaseItem, PurchaseItem.purchase_id == Purchase.id)
        .filter(
            PurchaseItem.product_id == prod.id,
            PurchaseItem.tenant_id == tenant.id,
        )
        .order_by(Purchase.issue_date.desc())
        .limit(10).all()
    )

    # Últimas 10 ventas del producto
    ultimas_ventas = (
        db.session.query(Invoice, InvoiceItem)
        .join(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
        .filter(
            InvoiceItem.product_id == prod.id,
            InvoiceItem.tenant_id == tenant.id,
            Invoice.status != "draft",
        )
        .order_by(Invoice.issue_date.desc())
        .limit(10).all()
    )

    # Resumen
    total_vendido = (
        db.session.query(func.coalesce(func.sum(InvoiceItem.quantity), 0))
        .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .filter(
            InvoiceItem.product_id == prod.id,
            InvoiceItem.tenant_id == tenant.id,
            Invoice.status.in_(["issued", "paid", "partially_paid", "overdue"]),
        ).scalar() or 0
    )
    total_comprado = (
        db.session.query(func.coalesce(func.sum(PurchaseItem.quantity), 0))
        .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .filter(
            PurchaseItem.product_id == prod.id,
            PurchaseItem.tenant_id == tenant.id,
            Purchase.status == "received",
        ).scalar() or 0
    )

    return render_template(
        "productos/detail.html",
        producto=prod,
        lotes=lotes,
        ultimas_compras=ultimas_compras,
        ultimas_ventas=ultimas_ventas,
        total_vendido=float(total_vendido),
        total_comprado=float(total_comprado),
        tenant=tenant,
    )


@productos_bp.route("/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
@tenant_required
def edit(product_id):
    prod = _get_or_404(product_id)
    tenant = current_tenant()

    if request.method == "POST":
        _populate_from_form(prod)

        # Si subieron nueva imagen, reemplazar
        file = request.files.get("image_file")
        if file and file.filename:
            old_url = prod.image_url
            new_url = save_product_image(file, tenant.id, prod.id)
            if new_url:
                # Borrar archivo viejo solo si estaba en nuestra carpeta y cambió
                if old_url and old_url != new_url:
                    delete_product_image(old_url)
                prod.image_url = new_url

        # Si marcó "quitar imagen"
        if request.form.get("remove_image"):
            delete_product_image(prod.image_url)
            prod.image_url = None

        db.session.commit()
        flash(f"Producto '{prod.name}' actualizado.", "success")
        return redirect(url_for("productos.list"))

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
    # Borrar imagen del disco si está en nuestra carpeta
    delete_product_image(prod.image_url)
    db.session.delete(prod)
    db.session.commit()
    flash(f"Producto '{name}' eliminado.", "info")
    return redirect(url_for("productos.list"))


@productos_bp.route("/recompute-stocks", methods=["POST"])
@login_required
@tenant_required
def recompute_stocks():
    """Recalcula el stock de todos los productos del tenant sumando sus lotes."""
    from services.inventory import recompute_all_stocks
    tenant = current_tenant()
    n = recompute_all_stocks(tenant.id)
    flash(f"Stock recalculado para {n} producto(s) según sus lotes.", "success")
    return redirect(url_for("productos.list"))


def _populate_from_form(prod: Product) -> None:
    prod.sku = (request.form.get("sku") or "").strip()
    prod.name = (request.form.get("name") or "").strip()
    prod.description = (request.form.get("description") or "").strip() or None
    prod.kind = request.form.get("kind") or "product"
    prod.price = _safe_decimal(request.form.get("price"))
    prod.price_wholesale = _safe_decimal(request.form.get("price_wholesale"))
    prod.price_special = _safe_decimal(request.form.get("price_special"))
    prod.cost = _safe_decimal(request.form.get("cost"))
    prod.stock = int(request.form.get("stock") or 0)
    prod.track_stock = bool(request.form.get("track_stock"))
    prod.track_batches = bool(request.form.get("track_batches"))
    prod.is_active = bool(request.form.get("is_active"))

    cat = request.form.get("category_id")
    prod.category_id = int(cat) if cat else None

    tax = request.form.get("tax_config_id")
    prod.tax_config_id = int(tax) if tax else None
