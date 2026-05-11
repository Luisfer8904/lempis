"""
Decoradores de autorización: roles + verificación de tenant.
"""
from functools import wraps
from flask import abort, redirect, url_for, flash
from flask_login import current_user

from services.tenant_context import current_tenant


def role_required(*role_codes):
    """Solo permite el acceso si el usuario tiene alguno de los roles indicados."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.is_owner or any(current_user.has_role(c) for c in role_codes):
                return fn(*args, **kwargs)
            flash("No tienes permisos para acceder a esta sección.", "danger")
            return abort(403)
        return wrapper
    return decorator


def admin_required(fn):
    """Atajo para owner/admin."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not current_user.is_admin():
            flash("Solo los administradores pueden acceder.", "danger")
            return abort(403)
        return fn(*args, **kwargs)
    return wrapper


def tenant_required(fn):
    """Garantiza que haya un tenant resuelto en el request."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if current_tenant() is None:
            flash("Empresa no encontrada o sesión inválida.", "warning")
            return redirect(url_for("auth.login"))
        return fn(*args, **kwargs)
    return wrapper
