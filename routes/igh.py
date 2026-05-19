"""
Sub-app privada para Inversiones Guevara Herrera.
Acceso separado usando una credencial especial desde el mismo login de Lempis.
"""
from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import inspect

from models import db
from models.ivg import IVGClient, IVGProduct, IVGUser


igh_bp = Blueprint("igh", __name__, url_prefix="/igh")


def igh_login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("igh_user"):
            return redirect(url_for("auth.login"))
        return fn(*args, **kwargs)
    return wrapper


def _table_exists(model) -> bool:
    return inspect(db.engine).has_table(model.__tablename__)


def _safe_count(model) -> int:
    if not _table_exists(model):
        return 0
    return model.query.count()


def _base_context():
    return {
        "igh_user": session.get("igh_user"),
        "company_name": "Inversiones Guevara Herrera",
        "ivg_counts": {
            "users": _safe_count(IVGUser),
            "clients": _safe_count(IVGClient),
            "products": _safe_count(IVGProduct),
        },
        "ivg_tables": [
            "ivg_usuarios",
            "ivg_clientes",
            "ivg_productos",
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
    return _render_section(
        "Usuarios IVG",
        "ivg_usuarios",
        "Base preparada para llevar equipo, roles internos y accesos del entorno privado dentro de la misma base de datos.",
        "Crear usuario IVG",
    )


@igh_bp.route("/clientes")
@igh_login_required
def clientes():
    return _render_section(
        "Clientes IVG",
        "ivg_clientes",
        "Espacio dedicado para cartera comercial, prospectos y cuentas activas sin mezclar estructuras con Lempis.",
        "Crear cliente IVG",
    )


@igh_bp.route("/productos")
@igh_login_required
def productos():
    return _render_section(
        "Productos IVG",
        "ivg_productos",
        "Catálogo propio para servicios, líneas comerciales o activos que necesite Inversiones Guevara Herrera.",
        "Crear producto IVG",
    )


@igh_bp.route("/cobros")
@igh_login_required
def cobros():
    return _render_section(
        "Cobros y cartera",
        "ivg_cobros",
        "Área lista para conectar cobranza, seguimiento de compromisos y control de saldos con el mismo estilo del dashboard principal.",
        "Registrar cobro",
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
        "Este módulo puede consumir la misma base de datos y consolidar indicadores de usuarios, clientes y productos prefijados como `ivg_*`.",
        "Generar reporte",
    )


@igh_bp.route("/logout")
def logout():
    session.pop("igh_user", None)
    session.pop("tenant_id", None)
    flash("Sesión de Inversiones Guevara Herrera cerrada.", "info")
    return redirect(url_for("auth.login"))
