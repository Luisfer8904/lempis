"""
Crea un tenant de demo con datos de ejemplo (útil para desarrollo).

Uso:
  python scripts/create_demo_tenant.py
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from models import db
from models.tenant import Tenant, Plan, Subscription
from models.user import User, Role, UserRole
from models.catalog import Customer, Product, Category


def main():
    app = create_app()
    with app.app_context():
        if Tenant.query.filter_by(slug="demo").first():
            print("⚠️  El tenant 'demo' ya existe.")
            return

        tenant = Tenant(
            name="Demo S.A.",
            slug="demo",
            country_code="HN",
            currency="HNL",
            email="demo@example.com",
            trial_ends_at=datetime.utcnow() + timedelta(days=14),
        )
        db.session.add(tenant)
        db.session.flush()

        owner = User(
            tenant_id=tenant.id,
            email="demo@example.com",
            full_name="Usuario Demo",
            is_owner=True,
            email_verified=True,
        )
        owner.set_password("demo1234")
        db.session.add(owner)
        db.session.flush()

        owner_role = Role.query.filter_by(code="owner").first()
        if owner_role:
            db.session.add(UserRole(user_id=owner.id, role_id=owner_role.id, tenant_id=tenant.id))

        free = Plan.query.filter_by(code="free").first()
        if free:
            db.session.add(Subscription(
                tenant_id=tenant.id, plan_id=free.id,
                status="trialing",
                current_period_start=datetime.utcnow(),
                current_period_end=tenant.trial_ends_at,
            ))

        cat = Category(tenant_id=tenant.id, name="General")
        db.session.add(cat)
        db.session.flush()

        for sku, name, price in [
            ("P-001", "Servicio de consultoría", 1500),
            ("P-002", "Producto A", 250),
            ("P-003", "Producto B", 99.90),
        ]:
            db.session.add(Product(
                tenant_id=tenant.id, category_id=cat.id,
                sku=sku, name=name, price=price, stock=100,
            ))

        db.session.add(Customer(
            tenant_id=tenant.id, name="Cliente de prueba",
            tax_id="08011990123456", email="cliente@example.com",
            country_code="HN",
        ))

        db.session.commit()
        print("✅ Tenant demo creado.")
        print("   → Email: demo@example.com")
        print("   → Pass:  demo1234")


if __name__ == "__main__":
    main()
