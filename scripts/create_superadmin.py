"""
Crea o promueve un usuario a SuperAdmin de Lempis.
SuperAdmin tiene acceso a /admin/* (cross-tenant).

Uso:
  python scripts/create_superadmin.py EMAIL PASSWORD [NOMBRE]

Si el usuario ya existe, lo promueve a SuperAdmin (no cambia password
a menos que se pase explícitamente).

Si NO existe, crea uno nuevo. Necesita un tenant — usa o crea el
tenant "lempis-admin" (la "empresa" del equipo de Lempis).
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from models import db
from models.tenant import Tenant, Plan, Subscription
from models.user import User, Role, UserRole


ADMIN_TENANT_SLUG = "lempis-admin"
ADMIN_TENANT_NAME = "Lempis (equipo)"


def get_or_create_admin_tenant() -> Tenant:
    t = Tenant.query.filter_by(slug=ADMIN_TENANT_SLUG).first()
    if t:
        return t

    t = Tenant(
        name=ADMIN_TENANT_NAME,
        slug=ADMIN_TENANT_SLUG,
        country_code="HN",
        currency="HNL",
        email="admin@lempis.com",
        trial_ends_at=datetime.utcnow() + timedelta(days=3650),  # 10 años
        onboarding_completed=True,
    )
    db.session.add(t)
    db.session.flush()

    # Suscribir al plan Business (sin límites para el equipo)
    business = Plan.query.filter_by(code="business").first()
    if business:
        db.session.add(Subscription(
            tenant_id=t.id, plan_id=business.id, status="active",
            current_period_start=datetime.utcnow(),
            current_period_end=datetime.utcnow() + timedelta(days=3650),
        ))
    return t


def main():
    if len(sys.argv) < 3:
        print("Uso: python scripts/create_superadmin.py EMAIL PASSWORD [NOMBRE]")
        sys.exit(1)

    email = sys.argv[1].strip().lower()
    password = sys.argv[2]
    full_name = sys.argv[3] if len(sys.argv) >= 4 else None

    app = create_app()
    with app.app_context():
        existing = User.query.filter_by(email=email).first()
        if existing:
            print(f"→ Usuario {email} ya existe. Promoviendo a SuperAdmin...")
            existing.is_superadmin = True
            existing.is_active = True
            existing.email_verified = True
            if password and password != "-":
                existing.set_password(password)
                print("  · Contraseña actualizada.")
            if full_name:
                existing.full_name = full_name
            db.session.commit()
            print(f"✅ {email} ahora es SuperAdmin.")
            return

        # Crear nuevo
        tenant = get_or_create_admin_tenant()
        db.session.flush()

        user = User(
            tenant_id=tenant.id,
            email=email,
            full_name=full_name,
            is_owner=False,
            is_superadmin=True,
            is_active=True,
            email_verified=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        # Asignar rol admin del tenant (para que también pueda ver el dashboard normal)
        admin_role = Role.query.filter_by(code="admin").first()
        if admin_role:
            db.session.add(UserRole(user_id=user.id, role_id=admin_role.id, tenant_id=tenant.id))

        db.session.commit()
        print(f"✅ SuperAdmin creado: {email}")
        print(f"   Tenant asignado: {tenant.slug}")
        print(f"   Acceso al panel: /admin/")


if __name__ == "__main__":
    main()
