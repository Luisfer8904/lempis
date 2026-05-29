"""
Reinicia Lempis cuando los datos actuales son de prueba.

Borra empresas, usuarios y datos operativos, conserva catálogos base
(países, impuestos, roles y planes) y crea una empresa nueva con owner.

Uso local/desarrollo:
  LEMPIS_RESET_CONFIRM=RESET_ALL_DATA python scripts/reset_demo_data.py EMAIL PASSWORD "Empresa" "Nombre Owner"

En producción este script está bloqueado por defecto. Si alguna vez se usa
para una limpieza controlada, debe ejecutarse después de un backup verificado
y con LEMPIS_PRODUCTION_RESET_CONFIRM=DELETE_PRODUCTION_DATA.

El módulo IGH no se borra por defecto. Para incluir IGH también se requiere:
  LEMPIS_RESET_INCLUDE_IGH=DELETE_IGH_DATA
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from models import db
from models.tenant import Tenant, Plan, Subscription
from models.user import User, Role, UserRole, RolePermission
from models.audit import AuditLog
from models.ivg import IVGAgendaItem, IVGCashSummary, IVGClient, IVGPayment, IVGProduct, IVGSale, IVGUser


CONFIRM_VALUE = "RESET_ALL_DATA"
PRODUCTION_CONFIRM_VALUE = "DELETE_PRODUCTION_DATA"
INCLUDE_IGH_VALUE = "DELETE_IGH_DATA"


def _slugify(value: str) -> str:
    import re

    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:60] or "empresa"


def _clear_data(include_igh: bool = False):
    """Borra datos operativos. El orden evita conflictos de llaves foráneas."""
    # Módulo IGH separado.
    if include_igh:
        for model in [IVGPayment, IVGSale, IVGAgendaItem, IVGCashSummary, IVGClient, IVGProduct, IVGUser]:
            db.session.query(model).delete()

    # Auditoría y permisos por tenant.
    db.session.query(AuditLog).delete()
    db.session.query(RolePermission).delete()

    # Tenant tiene cascada hacia usuarios, suscripción, clientes, productos y facturas.
    db.session.query(Tenant).delete()
    db.session.flush()


def _create_owner(email: str, password: str, company: str, full_name: str):
    tenant = Tenant(
        name=company,
        slug=_slugify(company),
        country_code="HN",
        currency="HNL",
        email=email,
        trial_ends_at=datetime.utcnow() + timedelta(days=14),
        onboarding_completed=True,
    )
    db.session.add(tenant)
    db.session.flush()

    user = User(
        tenant_id=tenant.id,
        email=email,
        full_name=full_name,
        is_owner=True,
        is_active=True,
        email_verified=True,
    )
    user.set_password(password)
    db.session.add(user)
    db.session.flush()

    owner_role = Role.query.filter_by(code="owner").first()
    if owner_role:
        db.session.add(UserRole(user_id=user.id, role_id=owner_role.id, tenant_id=tenant.id))

    free_plan = Plan.query.filter_by(code="free").first()
    if free_plan:
        db.session.add(Subscription(
            tenant_id=tenant.id,
            plan_id=free_plan.id,
            status="trialing",
            current_period_start=datetime.utcnow(),
            current_period_end=tenant.trial_ends_at,
        ))

    return tenant, user


def main():
    if os.environ.get("LEMPIS_RESET_CONFIRM") != CONFIRM_VALUE:
        print(f"ERROR: define LEMPIS_RESET_CONFIRM={CONFIRM_VALUE} para confirmar el borrado.")
        sys.exit(2)
    if len(sys.argv) < 5:
        print('Uso: LEMPIS_RESET_CONFIRM=RESET_ALL_DATA python scripts/reset_demo_data.py EMAIL PASSWORD "Empresa" "Nombre Owner"')
        sys.exit(1)

    email = sys.argv[1].strip().lower()
    password = sys.argv[2]
    company = sys.argv[3].strip()
    full_name = sys.argv[4].strip()

    app = create_app()
    with app.app_context():
        is_production = app.config.get("ENV") == "production" or not app.config.get("DEBUG", False)
        if is_production and os.environ.get("LEMPIS_PRODUCTION_RESET_CONFIRM") != PRODUCTION_CONFIRM_VALUE:
            print(
                "ERROR: reset_demo_data.py está bloqueado en producción. "
                f"Define LEMPIS_PRODUCTION_RESET_CONFIRM={PRODUCTION_CONFIRM_VALUE} solo después de un backup verificado."
            )
            sys.exit(3)

        include_igh = os.environ.get("LEMPIS_RESET_INCLUDE_IGH") == INCLUDE_IGH_VALUE
        if include_igh:
            print("⚠️  ADVERTENCIA: también se borrarán los datos IGH.")

        _clear_data(include_igh=include_igh)
        tenant, user = _create_owner(email, password, company, full_name)
        db.session.commit()
        print("✅ Datos de prueba borrados.")
        if not include_igh:
            print("✅ Datos IGH conservados.")
        print(f"✅ Empresa creada: {tenant.name} ({tenant.slug})")
        print(f"✅ Usuario owner: {user.email}")


if __name__ == "__main__":
    main()
