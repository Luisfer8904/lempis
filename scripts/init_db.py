"""
Inicializa la base de datos:
- Crea todas las tablas
- Carga catálogo de países LatAm con sus impuestos
- Crea roles por defecto
- Crea planes por defecto (Free, Basic, Pro, Business, Empresarial)

Uso:
  python scripts/init_db.py
"""
import os
import sys
from decimal import Decimal

# Permitir imports desde la raíz del proyecto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from models import db
from models.country import Country, TaxConfig
from models.user import Role
from models.tenant import Plan
from services.plan_catalog import PLAN_DEFINITIONS


COUNTRIES = [
    # (code, name, currency, symbol, prefix, tax_label, default_rate)
    ("HN", "Honduras",       "HNL", "L",  "+504", "ISV", Decimal("15.00")),
    ("GT", "Guatemala",      "GTQ", "Q",  "+502", "IVA", Decimal("12.00")),
    ("SV", "El Salvador",    "USD", "$",  "+503", "IVA", Decimal("13.00")),
    ("NI", "Nicaragua",      "NIO", "C$", "+505", "IVA", Decimal("15.00")),
    ("CR", "Costa Rica",     "CRC", "₡",  "+506", "IVA", Decimal("13.00")),
    ("PA", "Panamá",         "PAB", "B/.","+507", "ITBMS", Decimal("7.00")),
    ("MX", "México",         "MXN", "$",  "+52",  "IVA", Decimal("16.00")),
    ("CO", "Colombia",       "COP", "$",  "+57",  "IVA", Decimal("19.00")),
    ("PE", "Perú",           "PEN", "S/", "+51",  "IGV", Decimal("18.00")),
    ("EC", "Ecuador",        "USD", "$",  "+593", "IVA", Decimal("15.00")),
    ("DO", "Rep. Dominicana","DOP", "RD$","+1",   "ITBIS", Decimal("18.00")),
    ("CL", "Chile",          "CLP", "$",  "+56",  "IVA", Decimal("19.00")),
    ("AR", "Argentina",      "ARS", "$",  "+54",  "IVA", Decimal("21.00")),
]


ROLES = [
    ("owner",    "Propietario", "Creador del tenant. Acceso total."),
    ("admin",    "Administrador", "Gestiona usuarios, planes y configuración."),
    ("cajero",   "Cajero", "Registra ventas de mostrador y consulta productos/clientes."),
    ("vendedor", "Vendedor",    "Crea facturas y gestiona clientes."),
    ("contador", "Contador",    "Acceso a reportes y exportaciones."),
    ("viewer",   "Solo lectura","Solo puede consultar."),
]


PLANS = PLAN_DEFINITIONS


def seed_countries():
    print("→ Seedeando países...")
    for code, name, cur, sym, prefix, tax_label, rate in COUNTRIES:
        c = db.session.get(Country, code)
        if c:
            continue
        c = Country(
            code=code, name=name, currency=cur, currency_symbol=sym,
            phone_prefix=prefix, tax_label=tax_label, default_tax_rate=rate,
        )
        db.session.add(c)
        db.session.flush()
        # Crear tax config por defecto
        db.session.add(TaxConfig(
            country_code=code,
            code=f"{tax_label}{int(rate)}",
            name=f"{tax_label} general {rate}%",
            rate=rate,
            is_default=True,
        ))
        # Tasa exenta
        db.session.add(TaxConfig(
            country_code=code, code="EXENTO", name="Exento",
            rate=Decimal("0"), is_default=False,
        ))


def seed_roles():
    print("→ Seedeando roles...")
    for code, name, desc in ROLES:
        role = Role.query.filter_by(code=code).first()
        if role:
            role.name = name
            role.description = desc
        else:
            db.session.add(Role(code=code, name=name, description=desc))


def seed_plans():
    print("→ Seedeando planes...")
    for p in PLANS:
        existing = Plan.query.filter_by(code=p["code"]).first()
        if existing:
            for key, value in p.items():
                setattr(existing, key, value)
        else:
            db.session.add(Plan(**p))


def main():
    app = create_app()
    with app.app_context():
        print("→ Creando tablas...")
        db.create_all()

        seed_countries()
        seed_roles()
        seed_plans()

        db.session.commit()
        print("✅ Base de datos lista.")


if __name__ == "__main__":
    main()
