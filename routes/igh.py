"""
Sub-app privada para Inversiones Guevara Herrera.
Acceso separado usando usuarios IVG desde el mismo login de Lempis.
"""
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func, inspect

from models import db
from models.ivg import IVGClient, IVGPayment, IVGSale, IVGUser


igh_bp = Blueprint("igh", __name__, url_prefix="/igh")


def _table_exists(model) -> bool:
    return inspect(db.engine).has_table(model.__tablename__)


def _current_igh_user():
    user_id = session.get("igh_user_id")
    if not user_id or not _table_exists(IVGUser):
        return None
    user = db.session.get(IVGUser, user_id)
    if user is None or not user.is_active:
        return None
    return user


def igh_login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _current_igh_user()
        if user is None:
            session.pop("igh_user_id", None)
            session.pop("igh_user", None)
            session.pop("igh_role", None)
            return redirect(url_for("auth.login"))
        return fn(*args, **kwargs)
    return wrapper


def igh_superadmin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _current_igh_user()
        if user is None:
            return redirect(url_for("auth.login"))
        if not user.is_superadmin():
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


def _safe_count(model) -> int:
    if not _table_exists(model):
        return 0
    return model.query.count()


def _safe_sum(model, column, *filters):
    if not _table_exists(model):
        return 0
    query = db.session.query(func.coalesce(func.sum(column), 0))
    if filters:
        query = query.filter(*filters)
    return float(query.scalar() or 0)


def _base_context():
    current_igh_user = _current_igh_user()
    return {
        "igh_user": current_igh_user.username if current_igh_user else session.get("igh_user"),
        "igh_current_user": current_igh_user,
        "igh_can_manage_users": bool(current_igh_user and current_igh_user.is_superadmin()),
        "company_name": "Inversiones Guevara Herrera",
        "ivg_counts": {
            "users": _safe_count(IVGUser),
            "clients": _safe_count(IVGClient),
            "sales": _safe_count(IVGSale),
            "payments": _safe_count(IVGPayment),
            "credit_sales": _safe_count(IVGSale) if not _table_exists(IVGSale) else IVGSale.query.filter_by(sale_type="credito").count(),
            "cash_sales": _safe_count(IVGSale) if not _table_exists(IVGSale) else IVGSale.query.filter_by(sale_type="contado").count(),
        },
        "ivg_metrics": {
            "receivable_total": _safe_sum(IVGSale, IVGSale.balance_due, IVGSale.sale_type == "credito") if _table_exists(IVGSale) else 0,
            "cash_total": _safe_sum(IVGSale, IVGSale.gross_amount, IVGSale.sale_type == "contado") if _table_exists(IVGSale) else 0,
            "transfer_total": _safe_sum(IVGSale, IVGSale.gross_amount, IVGSale.sale_type == "contado", IVGSale.payment_method == "transferencia") if _table_exists(IVGSale) else 0,
            "efectivo_total": _safe_sum(IVGSale, IVGSale.gross_amount, IVGSale.sale_type == "contado", IVGSale.payment_method == "efectivo") if _table_exists(IVGSale) else 0,
            "herbicidas_credito": _safe_sum(IVGSale, IVGSale.gross_amount, IVGSale.sale_type == "credito", IVGSale.category == "herbicidas") if _table_exists(IVGSale) else 0,
            "concentrados_credito": _safe_sum(IVGSale, IVGSale.gross_amount, IVGSale.sale_type == "credito", IVGSale.category == "concentrados") if _table_exists(IVGSale) else 0,
        },
        "ivg_tables": [
            "ivg_usuarios",
            "ivg_clientes",
            "ivg_ventas",
            "ivg_pagos",
        ],
    }


def _render_section(title: str, eyebrow: str, description: str, cta: str):
    context = _base_context()
    context.update({
        "section_title": title,
        "section_eyebrow": eyebrow,
        "section_description": description,
        "section_cta": cta,
    })
    return render_template("igh/section.html", **context)


def _get_ivg_user_or_404(user_id: int) -> IVGUser:
    if not _table_exists(IVGUser):
        abort(404)
    user = db.session.get(IVGUser, user_id)
    if user is None:
        abort(404)
    return user


@igh_bp.route("/")
@igh_login_required
def dashboard():
    context = _base_context()
    context["page_heading"] = "Dashboard"
    return render_template("igh/dashboard.html", **context)


@igh_bp.route("/dashboard")
@igh_login_required
def dashboard_alias():
    return redirect(url_for("igh.dashboard"))


@igh_bp.route("/usuarios")
@igh_login_required
def usuarios():
    users = IVGUser.query.order_by(IVGUser.created_at.asc()).all() if _table_exists(IVGUser) else []
    context = _base_context()
    context["users"] = users
    return render_template("igh/users_list.html", **context)


@igh_bp.route("/usuarios/new", methods=["GET", "POST"])
@igh_login_required
@igh_superadmin_required
def usuarios_new():
    context = _base_context()
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip().lower() or None
        password = request.form.get("password") or ""
        role = request.form.get("role") or "cajero"

        if not username or not password:
            flash("Usuario y contraseña son obligatorios.", "danger")
            return redirect(url_for("igh.usuarios_new"))

        if len(password) < 4:
            flash("La contraseña debe tener al menos 4 caracteres.", "danger")
            return redirect(url_for("igh.usuarios_new"))

        if _table_exists(IVGUser):
            exists = IVGUser.query.filter_by(username=username).first()
            if exists:
                flash("Ya existe un usuario IVG con ese nombre.", "danger")
                return redirect(url_for("igh.usuarios_new"))

        user = IVGUser(
            username=username,
            full_name=full_name or None,
            email=email,
            role=role,
            is_active=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash(f"Usuario IVG {username} creado.", "success")
        return redirect(url_for("igh.usuarios"))

    context["user"] = None
    return render_template("igh/users_form.html", **context)


@igh_bp.route("/usuarios/<int:user_id>/edit", methods=["GET", "POST"])
@igh_login_required
@igh_superadmin_required
def usuarios_edit(user_id: int):
    user = _get_ivg_user_or_404(user_id)
    context = _base_context()

    if request.method == "POST":
        user.full_name = (request.form.get("full_name") or "").strip() or None
        user.email = (request.form.get("email") or "").strip().lower() or None
        user.role = request.form.get("role") or user.role
        user.is_active = bool(request.form.get("is_active"))

        new_password = request.form.get("password") or ""
        if new_password:
            if len(new_password) < 4:
                flash("La contraseña debe tener al menos 4 caracteres.", "danger")
                return redirect(url_for("igh.usuarios_edit", user_id=user.id))
            user.set_password(new_password)

        db.session.commit()
        flash(f"Usuario IVG {user.username} actualizado.", "success")
        return redirect(url_for("igh.usuarios"))

    context["user"] = user
    return render_template("igh/users_form.html", **context)


@igh_bp.route("/clientes")
@igh_login_required
def clientes():
    return _render_section(
        "Clientes IVG",
        "ivg_clientes",
        "Espacio dedicado para cartera comercial, prospectos y cuentas activas sin mezclar estructuras con Lempis.",
        "Crear cliente IVG",
    )


@igh_bp.route("/ventas")
@igh_login_required
def ventas():
    return _render_section(
        "Ventas IVG",
        "ivg_ventas",
        "Registro de venta bruta del negocio. Aquí vivirán las ventas al contado, las ventas al crédito y sus categorías herbicidas o concentrados.",
        "Registrar venta",
    )


@igh_bp.route("/pagos")
@igh_login_required
def pagos():
    return _render_section(
        "Pagos y abonos",
        "ivg_pagos",
        "Los pagos y abonos se registrarán sobre facturas de crédito existentes para llevar saldo, historial y forma de pago.",
        "Registrar pago",
    )


@igh_bp.route("/agenda")
@igh_login_required
def agenda():
    return _render_section(
        "Agenda operativa",
        "ivg_agenda",
        "Vista para tareas, seguimiento comercial y próximos pasos del equipo ejecutivo.",
        "Agregar actividad",
    )


@igh_bp.route("/reportes")
@igh_login_required
def reportes():
    return _render_section(
        "Reportes ejecutivos",
        "ivg_reportes",
        "Este módulo consolidará ventas de contado, facturas de crédito, pagos, abonos y cartera por categoría dentro del workspace IVG.",
        "Generar reporte",
    )


@igh_bp.route("/productos")
@igh_login_required
def productos_alias():
    return redirect(url_for("igh.ventas"))


@igh_bp.route("/cobros")
@igh_login_required
def cobros_alias():
    return redirect(url_for("igh.pagos"))


@igh_bp.route("/logout")
def logout():
    session.pop("igh_user_id", None)
    session.pop("igh_user", None)
    session.pop("igh_role", None)
    session.pop("tenant_id", None)
    flash("Sesión de Inversiones Guevara Herrera cerrada.", "info")
    return redirect(url_for("auth.login"))
