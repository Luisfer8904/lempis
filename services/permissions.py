"""
Decoradores de autorización: roles, permisos y verificación de tenant.
"""
import json
from functools import wraps
from flask import abort, redirect, url_for, flash, session, request
from flask_login import current_user, logout_user

from services.tenant_context import current_tenant


PERMISSION_GROUPS = [
    ("Ventas", [
        ("sales.view", "Ver facturas y ventas"),
        ("sales.create", "Crear ventas y facturas"),
        ("sales.manage", "Anular o eliminar facturas"),
    ]),
    ("Clientes", [
        ("customers.view", "Ver clientes"),
        ("customers.manage", "Crear y editar clientes"),
        ("customers.delete", "Eliminar clientes"),
    ]),
    ("Inventario", [
        ("products.view", "Ver productos e inventario"),
        ("products.manage", "Crear y editar productos/lotes"),
        ("products.delete", "Eliminar productos/lotes"),
    ]),
    ("Cobros", [
        ("receivables.view", "Ver cuentas por cobrar"),
        ("receivables.manage", "Registrar, revertir cobros y recibos"),
    ]),
    ("Caja diaria", [
        ("cash.view", "Ver cierres, aperturas y gastos de caja"),
        ("cash.manage", "Registrar aperturas, gastos y cierres de caja"),
        ("cash.edit_closure", "Editar o anular cierres ya realizados"),
    ]),
    ("Compras", [
        ("purchases.view", "Ver compras y proveedores"),
        ("purchases.manage", "Crear, recibir, pagar y anular compras"),
    ]),
    ("Reportes", [
        ("reports.view", "Ver reportes"),
    ]),
    ("Equipo y ajustes", [
        ("settings.manage", "Configurar empresa, facturación e impresión"),
        ("users.manage", "Gestionar usuarios y roles"),
    ]),
]

ALL_PERMISSIONS = [code for _, items in PERMISSION_GROUPS for code, _ in items]

DEFAULT_ROLE_PERMISSIONS = {
    "owner": ALL_PERMISSIONS,
    "admin": ALL_PERMISSIONS,
    "cajero": ["sales.view", "sales.create", "customers.view", "products.view", "cash.view", "cash.manage"],
    "vendedor": [
        "sales.view", "sales.create", "customers.view", "customers.manage",
        "products.view", "receivables.view",
    ],
    "contador": [
        "sales.view", "customers.view", "products.view", "receivables.view",
        "receivables.manage", "cash.view", "cash.manage", "cash.edit_closure",
        "purchases.view", "reports.view",
    ],
    "viewer": ["sales.view", "customers.view", "products.view", "receivables.view", "purchases.view", "reports.view"],
}


def role_permissions_for(tenant_id: int, role) -> set[str]:
    if role is None:
        return set()
    from models.user import RolePermission
    custom = RolePermission.query.filter_by(tenant_id=tenant_id, role_id=role.id).first()
    if custom:
        try:
            return set(json.loads(custom.permissions or "[]")) & set(ALL_PERMISSIONS)
        except (TypeError, ValueError):
            return set()
    return set(DEFAULT_ROLE_PERMISSIONS.get(role.code, []))


def user_permissions(user) -> set[str]:
    if not user or not getattr(user, "is_active", False):
        return set()
    if user.is_owner or user.has_role("owner"):
        return set(ALL_PERMISSIONS)
    perms = set()
    for role in user.roles:
        perms |= role_permissions_for(user.tenant_id, role)
    return perms


def user_has_permission(user, permission: str) -> bool:
    return permission in user_permissions(user)


def _deny(message: str):
    flash(message, "warning")
    referrer = request.referrer
    if referrer and "/app/" in referrer and referrer != request.url:
        return redirect(referrer)
    return redirect(url_for("dashboard.home"))


def role_required(*role_codes):
    """Solo permite el acceso si el usuario tiene alguno de los roles indicados."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.is_owner or any(current_user.has_role(c) for c in role_codes):
                return fn(*args, **kwargs)
            return _deny("No tienes permisos para acceder a esta sección.")
        return wrapper
    return decorator


def admin_required(fn):
    """Atajo para owner/admin del tenant."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not current_user.is_admin():
            return _deny("Solo los administradores pueden acceder.")
        return fn(*args, **kwargs)
    return wrapper


def permission_required(permission: str):
    """Permite acceso si el usuario tiene el permiso indicado."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.has_permission(permission):
                return fn(*args, **kwargs)
            return _deny("No tienes permisos para realizar esa acción.")
        return wrapper
    return decorator


def superadmin_required(fn):
    """Solo para superadmins de la plataforma Lempis (cross-tenant)."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not getattr(current_user, "is_superadmin", False):
            return _deny("Acceso restringido al equipo de Lempis.")
        return fn(*args, **kwargs)
    return wrapper


def tenant_required(fn):
    """
    Garantiza que haya un tenant resuelto en el request.
    Si hay sesión zombi (logueado pero sin tenant), la limpia para evitar loops.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if current_tenant() is None:
            # Sesión zombi: usuario "autenticado" pero sin tenant válido.
            # Limpiamos todo para no entrar en loop de redirects.
            if current_user.is_authenticated:
                logout_user()
            session.pop("tenant_id", None)
            session.pop("_flashes", None)  # evitar acumular flashes
            flash("Tu sesión expiró. Por favor inicia sesión nuevamente.", "warning")
            return redirect(url_for("auth.login"))
        return fn(*args, **kwargs)
    return wrapper
