"""
Validación de límites del plan del tenant.
Cada plan tiene topes: max_users, max_invoices_per_month, max_products, max_customers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from models import db
from models.tenant import Tenant, Plan
from models.invoice import Invoice
from models.catalog import Customer, Product
from models.user import User


class PlanLimitError(Exception):
    """El tenant alcanzó el límite de su plan."""


def current_plan(tenant: Tenant) -> Optional[Plan]:
    """Plan activo del tenant (o None si no tiene suscripción)."""
    sub = tenant.subscription
    return sub.plan if sub else None


def usage(tenant: Tenant) -> dict:
    """Snapshot de uso actual del tenant."""
    today = datetime.utcnow()
    month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    return {
        "invoices_this_month": Invoice.query.filter(
            Invoice.tenant_id == tenant.id,
            Invoice.issue_date >= month_start,
            Invoice.status != "draft",
        ).count(),
        "customers_total": Customer.query.filter_by(tenant_id=tenant.id).count(),
        "products_total": Product.query.filter_by(tenant_id=tenant.id).count(),
        "users_total": User.query.filter_by(tenant_id=tenant.id, is_active=True).count(),
    }


def limits_with_usage(tenant: Tenant) -> dict:
    """
    Devuelve dict con el uso vs el límite para cada métrica.
    Útil para pintar barras de progreso en el dashboard.
    """
    plan = current_plan(tenant)
    u = usage(tenant)
    if plan is None:
        return {
            "plan_name": "Sin plan",
            "invoices": {"used": u["invoices_this_month"], "max": 0, "pct": 0},
            "customers": {"used": u["customers_total"], "max": 0, "pct": 0},
            "products": {"used": u["products_total"], "max": 0, "pct": 0},
            "users": {"used": u["users_total"], "max": 0, "pct": 0},
        }
    return {
        "plan_name": plan.name,
        "plan_code": plan.code,
        "invoices": _line(u["invoices_this_month"], plan.max_invoices_per_month),
        "customers": _line(u["customers_total"], plan.max_customers),
        "products": _line(u["products_total"], plan.max_products),
        "users": _line(u["users_total"], plan.max_users),
    }


def _line(used: int, maximum: int) -> dict:
    pct = min(round((used / maximum) * 100), 100) if maximum else 0
    return {
        "used": used,
        "max": maximum,
        "pct": pct,
        "remaining": max(maximum - used, 0),
        "exceeded": used >= maximum,
        "near_limit": pct >= 80,
    }


# ---------- Checks individuales ----------

def check_can_emit_invoice(tenant: Tenant) -> None:
    plan = current_plan(tenant)
    if plan is None:
        return
    used = usage(tenant)["invoices_this_month"]
    if used >= plan.max_invoices_per_month:
        raise PlanLimitError(
            f"Alcanzaste el límite de {plan.max_invoices_per_month} facturas/mes "
            f"de tu plan {plan.name}. Actualiza tu plan para emitir más."
        )


def check_can_create_customer(tenant: Tenant) -> None:
    plan = current_plan(tenant)
    if plan is None:
        return
    used = usage(tenant)["customers_total"]
    if used >= plan.max_customers:
        raise PlanLimitError(
            f"Alcanzaste el límite de {plan.max_customers} clientes "
            f"de tu plan {plan.name}. Actualiza tu plan para agregar más."
        )


def check_can_create_product(tenant: Tenant) -> None:
    plan = current_plan(tenant)
    if plan is None:
        return
    used = usage(tenant)["products_total"]
    if used >= plan.max_products:
        raise PlanLimitError(
            f"Alcanzaste el límite de {plan.max_products} productos "
            f"de tu plan {plan.name}. Actualiza tu plan para agregar más."
        )


def check_can_add_user(tenant: Tenant) -> None:
    plan = current_plan(tenant)
    if plan is None:
        return
    used = usage(tenant)["users_total"]
    if used >= plan.max_users:
        raise PlanLimitError(
            f"Alcanzaste el límite de {plan.max_users} usuario(s) "
            f"de tu plan {plan.name}. Actualiza tu plan para invitar más."
        )
