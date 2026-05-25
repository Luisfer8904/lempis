"""
Validación de límites del plan del tenant.
Cada plan tiene topes de uso: usuarios, facturas, productos, clientes,
proveedores, sucursales y bodegas.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from models import db
from models.tenant import Tenant, Plan
from models.invoice import Invoice
from models.catalog import Customer, Product
from models.locations import Branch, Warehouse
from models.suppliers import Supplier
from models.user import User
from services.plan_catalog import role_codes_for_plan


class PlanLimitError(Exception):
    """El tenant alcanzó el límite de su plan."""


def current_plan(tenant: Tenant) -> Optional[Plan]:
    """Plan activo del tenant (o None si no tiene suscripción)."""
    sub = tenant.subscription
    return sub.plan if sub else None


def allowed_role_codes(tenant: Tenant) -> list[str]:
    plan = current_plan(tenant)
    return role_codes_for_plan(plan.code if plan else "free")


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
        "suppliers_total": Supplier.query.filter_by(tenant_id=tenant.id).count(),
        "branches_total": Branch.query.filter_by(tenant_id=tenant.id).count(),
        "warehouses_total": Warehouse.query.filter_by(tenant_id=tenant.id).count(),
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
            "suppliers": {"used": u["suppliers_total"], "max": 0, "pct": 0},
            "branches": {"used": u["branches_total"], "max": 0, "pct": 0},
            "warehouses": {"used": u["warehouses_total"], "max": 0, "pct": 0},
        }
    return {
        "plan_name": plan.name,
        "plan_code": plan.code,
        "invoices": _line(u["invoices_this_month"], plan.max_invoices_per_month),
        "customers": _line(u["customers_total"], plan.max_customers),
        "products": _line(u["products_total"], plan.max_products),
        "users": _line(u["users_total"], plan.max_users),
        "suppliers": _line(u["suppliers_total"], plan.max_suppliers),
        "branches": _line(u["branches_total"], plan.max_branches),
        "warehouses": _line(u["warehouses_total"], plan.max_warehouses),
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


def check_can_create_supplier(tenant: Tenant) -> None:
    plan = current_plan(tenant)
    if plan is None:
        return
    used = usage(tenant)["suppliers_total"]
    if used >= plan.max_suppliers:
        raise PlanLimitError(
            f"Alcanzaste el límite de {plan.max_suppliers} proveedor(es) "
            f"de tu plan {plan.name}. Actualiza tu plan para agregar más."
        )


def check_can_create_branch(tenant: Tenant) -> None:
    plan = current_plan(tenant)
    if plan is None:
        return
    used = usage(tenant)["branches_total"]
    if used >= plan.max_branches:
        raise PlanLimitError(
            f"Alcanzaste el límite de {plan.max_branches} sucursal(es) "
            f"de tu plan {plan.name}. Actualiza tu plan para crear más."
        )


def check_can_create_warehouse(tenant: Tenant) -> None:
    plan = current_plan(tenant)
    if plan is None:
        return
    used = usage(tenant)["warehouses_total"]
    if used >= plan.max_warehouses:
        raise PlanLimitError(
            f"Alcanzaste el límite de {plan.max_warehouses} bodega(s) "
            f"de tu plan {plan.name}. Actualiza tu plan para crear más."
        )
