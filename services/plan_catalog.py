"""
Definición comercial de planes.

Mantener esta información en un solo lugar evita que la landing, billing y
los seeds muestren versiones distintas del mismo paquete.
"""
from __future__ import annotations


PLAN_ORDER = ["free", "basic", "pro", "business", "enterprise"]

PLAN_ROLE_LIMITS = {
    "free": "1 administrador y 1 cajero",
    "basic": "1 administrador y hasta 3 cajeros",
    "pro": "Todos los roles y múltiples usuarios",
    "business": "Todos los roles, usuarios por equipo y operación",
    "enterprise": "Roles y usuarios configurables según contrato",
}

PLAN_ROLE_CODES = {
    "free": ["admin", "cajero"],
    "basic": ["admin", "cajero"],
    "pro": ["admin", "cajero", "vendedor", "contador", "viewer"],
    "business": ["admin", "cajero", "vendedor", "contador", "viewer"],
    "enterprise": ["admin", "cajero", "vendedor", "contador", "viewer"],
}

PLAN_FEATURES = {
    "free": [
        "20 facturas al mes",
        "10 productos",
        "2 proveedores",
        "1 sucursal y 1 bodega",
        "1 administrador y 1 cajero",
    ],
    "basic": [
        "1,000 facturas al mes",
        "200 productos",
        "50 proveedores",
        "1 sucursal y 1 bodega",
        "1 administrador y hasta 3 cajeros",
    ],
    "pro": [
        "3,000 facturas al mes",
        "500 productos",
        "100 proveedores",
        "1 sucursal y hasta 3 bodegas",
        "Todos los roles, reportes avanzados y vencimientos",
    ],
    "business": [
        "10,000 facturas al mes",
        "Productos, proveedores y clientes ampliados",
        "Varias sucursales y bodegas",
        "Usuarios por área de trabajo",
        "Inventario por sede, transferencias y exportaciones",
    ],
    "enterprise": [
        "Límites configurables a la medida",
        "Sucursales, bodegas y usuarios según operación",
        "Migración de datos e implementación asistida",
        "Reportes, permisos e integraciones según contrato",
        "Acompañamiento para procesos internos",
    ],
}


PLAN_DEFINITIONS = [
    {
        "code": "free",
        "name": "Free",
        "description": "Para probar Lempis con una operación pequeña.",
        "price_monthly": 0,
        "price_yearly": 0,
        "currency": "USD",
        "max_users": 2,
        "max_invoices_per_month": 20,
        "max_products": 10,
        "max_customers": 100,
        "max_suppliers": 2,
        "max_branches": 1,
        "max_warehouses": 1,
        "can_export_pdf": True,
        "can_export_excel": False,
        "can_use_api": False,
        "can_custom_branding": False,
        "can_use_advanced_reports": False,
        "can_use_multi_branch": False,
        "can_customize_roles": False,
    },
    {
        "code": "basic",
        "name": "Basic",
        "description": "Para mostrador o negocio local con facturación constante.",
        "price_monthly": 15,
        "price_yearly": 150,
        "currency": "USD",
        "max_users": 4,
        "max_invoices_per_month": 1000,
        "max_products": 200,
        "max_customers": 1000,
        "max_suppliers": 50,
        "max_branches": 1,
        "max_warehouses": 1,
        "can_export_pdf": True,
        "can_export_excel": True,
        "can_use_api": False,
        "can_custom_branding": False,
        "can_use_advanced_reports": False,
        "can_use_multi_branch": False,
        "can_customize_roles": False,
    },
    {
        "code": "pro",
        "name": "Pro",
        "description": "Para empresas con más usuarios, reportes y control de bodega.",
        "price_monthly": 30,
        "price_yearly": 300,
        "currency": "USD",
        "max_users": 10,
        "max_invoices_per_month": 3000,
        "max_products": 500,
        "max_customers": 3000,
        "max_suppliers": 100,
        "max_branches": 1,
        "max_warehouses": 3,
        "can_export_pdf": True,
        "can_export_excel": True,
        "can_use_api": True,
        "can_custom_branding": False,
        "can_use_advanced_reports": True,
        "can_use_multi_branch": False,
        "can_customize_roles": True,
    },
    {
        "code": "business",
        "name": "Business",
        "description": "Para operaciones con sucursales, bodegas y equipos más grandes.",
        "price_monthly": 65,
        "price_yearly": 650,
        "currency": "USD",
        "max_users": 30,
        "max_invoices_per_month": 10000,
        "max_products": 5000,
        "max_customers": 20000,
        "max_suppliers": 1000,
        "max_branches": 5,
        "max_warehouses": 15,
        "can_export_pdf": True,
        "can_export_excel": True,
        "can_use_api": True,
        "can_custom_branding": True,
        "can_use_advanced_reports": True,
        "can_use_multi_branch": True,
        "can_customize_roles": True,
    },
    {
        "code": "enterprise",
        "name": "Empresarial",
        "description": "Plan a la medida para empresas con procesos o límites especiales.",
        "price_monthly": 0,
        "price_yearly": 0,
        "currency": "USD",
        "max_users": 9999,
        "max_invoices_per_month": 999999,
        "max_products": 999999,
        "max_customers": 999999,
        "max_suppliers": 999999,
        "max_branches": 999,
        "max_warehouses": 999,
        "can_export_pdf": True,
        "can_export_excel": True,
        "can_use_api": True,
        "can_custom_branding": True,
        "can_use_advanced_reports": True,
        "can_use_multi_branch": True,
        "can_customize_roles": True,
    },
]


def ordered_plans(plans):
    order = {code: index for index, code in enumerate(PLAN_ORDER)}
    return sorted(plans, key=lambda plan: order.get(plan.code, 999))


def features_for(plan_code: str) -> list[str]:
    return PLAN_FEATURES.get(plan_code, [])


def role_summary_for(plan_code: str) -> str:
    return PLAN_ROLE_LIMITS.get(plan_code, "Roles según configuración")


def role_codes_for_plan(plan_code: str | None) -> list[str]:
    return PLAN_ROLE_CODES.get(plan_code or "free", PLAN_ROLE_CODES["free"])
