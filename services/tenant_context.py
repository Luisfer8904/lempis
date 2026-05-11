"""
Resolución del Tenant activo en cada request.
Soporta tres estrategias (configurable vía TENANT_RESOLVER):
- session   : usa session['tenant_id'] (login normal con dominio único)
- subdomain : extrae el slug del subdominio (empresa.tudominio.com)
- header    : usa el header X-Tenant-Slug (útil para API)
"""
from flask import current_app, g, request, session
from flask_login import current_user

from models import db
from models.tenant import Tenant


def _resolve_by_session():
    tenant_id = session.get("tenant_id")
    if tenant_id:
        tenant = Tenant.query.get(tenant_id)
        if tenant is not None:
            return tenant
    if current_user.is_authenticated:
        tenant = current_user.tenant
        if tenant is not None:
            session["tenant_id"] = tenant.id
        return tenant
    return None


def _resolve_by_subdomain():
    host = request.host.split(":")[0]

    # Ignorar IPs y localhost (no son subdominios reales)
    if host in ("localhost", "127.0.0.1", "0.0.0.0"):
        return None
    # Detectar si el host es una IP (ej. 192.168.1.10 o 34.195.216.217)
    if all(p.isdigit() for p in host.split(".")) and len(host.split(".")) == 4:
        return None

    parts = host.split(".")
    if len(parts) >= 3:
        slug = parts[0]
        # Saltar prefijos comunes que no son tenants reales
        if slug in ("www", "app", "api", "admin"):
            return None
        return Tenant.query.filter_by(slug=slug, is_active=True).first()
    return None


def _resolve_by_header():
    slug = request.headers.get("X-Tenant-Slug")
    if slug:
        return Tenant.query.filter_by(slug=slug, is_active=True).first()
    return None


def resolve_tenant():
    """Determina el tenant activo y lo expone en flask.g.tenant."""
    strategy = current_app.config.get("TENANT_RESOLVER", "session")
    resolver = {
        "session": _resolve_by_session,
        "subdomain": _resolve_by_subdomain,
        "header": _resolve_by_header,
    }.get(strategy, _resolve_by_session)

    tenant = resolver()
    g.tenant = tenant
    return tenant


def current_tenant():
    """Acceso conveniente al tenant activo dentro de un request."""
    return getattr(g, "tenant", None)


def require_same_tenant(obj) -> bool:
    """Validación de seguridad: el objeto pertenece al tenant actual."""
    t = current_tenant()
    return t is not None and getattr(obj, "tenant_id", None) == t.id
