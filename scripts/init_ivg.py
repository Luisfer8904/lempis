"""
Inicializa tablas del workspace IVG y crea usuarios base.

Uso:
  ./venv/bin/python scripts/init_ivg.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from models import db
from models.ivg import IVGClient, IVGProduct, IVGUser


DEFAULT_USERS = [
    {
        "username": "Luis",
        "email": "luis@ivg.local",
        "full_name": "Luis",
        "role": "superadmin",
        "password": "8904",
    },
    {
        "username": "Jorge",
        "email": "jorge@ivg.local",
        "full_name": "Jorge",
        "role": "admin",
        "password": "1234",
    },
    {
        "username": "Stefany",
        "email": "stefany@ivg.local",
        "full_name": "Stefany",
        "role": "cajero",
        "password": "1234",
    },
]


def ensure_tables():
    IVGUser.__table__.create(bind=db.engine, checkfirst=True)
    IVGClient.__table__.create(bind=db.engine, checkfirst=True)
    IVGProduct.__table__.create(bind=db.engine, checkfirst=True)


def seed_users():
    for payload in DEFAULT_USERS:
        existing = IVGUser.query.filter_by(username=payload["username"]).first()
        if existing:
            existing.email = payload["email"]
            existing.full_name = payload["full_name"]
            existing.role = payload["role"]
            existing.is_active = True
            existing.set_password(payload["password"])
            continue

        user = IVGUser(
            username=payload["username"],
            email=payload["email"],
            full_name=payload["full_name"],
            role=payload["role"],
            is_active=True,
        )
        user.set_password(payload["password"])
        db.session.add(user)


def main():
    app = create_app()
    with app.app_context():
        print("→ Creando tablas IVG...")
        ensure_tables()
        print("→ Seedeando usuarios IVG...")
        seed_users()
        db.session.commit()
        print("✅ IVG listo.")


if __name__ == "__main__":
    main()
