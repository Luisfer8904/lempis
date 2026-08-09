"""Alcance de sedes permitido al crear ventas.

La bodega continúa siendo la llave interna que usa el inventario, pero la
operación comercial se presenta y se autoriza por sede.
"""
from __future__ import annotations

from models.locations import Branch, Warehouse


def _is_company_admin(user) -> bool:
    if user is None:
        return False
    if bool(getattr(user, "is_superadmin", False)):
        return True
    is_admin = getattr(user, "is_admin", None)
    return bool(is_admin()) if callable(is_admin) else False


def _canonical_warehouse(warehouses: list[Warehouse]) -> Warehouse:
    """Escoge la ubicación interna representativa de una sede heredada."""
    return min(
        warehouses,
        key=lambda warehouse: (
            not bool(warehouse.is_default),
            (warehouse.name or "").strip().lower() not in {"principal", "bodega principal"},
            warehouse.id,
        ),
    )


def sale_warehouses_for_user(tenant_id: int, user=None) -> list[Warehouse]:
    """Devuelve una ubicación interna por cada sede autorizada para vender.

    Administradores y propietarios pueden operar en todas las sedes activas.
    Los demás usuarios quedan limitados estrictamente a su sede asignada. Un
    usuario operativo sin sede no recibe una ubicación por defecto.
    """
    query = (
        Warehouse.query
        .join(Branch, Branch.id == Warehouse.branch_id)
        .filter(
            Warehouse.tenant_id == tenant_id,
            Warehouse.is_active.is_(True),
            Branch.is_active.is_(True),
        )
    )
    if not _is_company_admin(user):
        branch_id = getattr(user, "branch_id", None)
        if not branch_id:
            return []
        query = query.filter(Warehouse.branch_id == branch_id)

    warehouses = query.order_by(Branch.name.asc(), Warehouse.id.asc()).all()
    by_branch: dict[int, list[Warehouse]] = {}
    for warehouse in warehouses:
        by_branch.setdefault(warehouse.branch_id, []).append(warehouse)
    return [_canonical_warehouse(group) for group in by_branch.values()]


def can_user_sell_from_warehouse(tenant_id: int, user, warehouse_id) -> bool:
    if not warehouse_id:
        return False
    try:
        requested_id = int(warehouse_id)
    except (TypeError, ValueError):
        return False
    return any(
        warehouse.id == requested_id
        for warehouse in sale_warehouses_for_user(tenant_id, user)
    )
