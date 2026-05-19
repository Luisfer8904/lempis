"""
Sub-app privada para Inversiones Guevara Herrera.
Acceso separado usando una credencial especial desde el mismo login de Lempis.
"""
from functools import wraps

from flask import Blueprint, flash, redirect, render_template, session, url_for


igh_bp = Blueprint("igh", __name__, url_prefix="/igh")


def igh_login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("igh_user"):
            return redirect(url_for("auth.login"))
        return fn(*args, **kwargs)
    return wrapper


@igh_bp.route("/")
@igh_login_required
def dashboard():
    return render_template(
        "igh/dashboard.html",
        igh_user=session.get("igh_user"),
        company_name="Inversiones Guevara Herrera",
    )


@igh_bp.route("/dashboard")
@igh_login_required
def dashboard_alias():
    return redirect(url_for("igh.dashboard"))


@igh_bp.route("/logout")
def logout():
    session.pop("igh_user", None)
    session.pop("tenant_id", None)
    flash("Sesión de Inversiones Guevara Herrera cerrada.", "info")
    return redirect(url_for("auth.login"))
