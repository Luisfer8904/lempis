"""
Aplica columnas nuevas de planes y sincroniza el catálogo comercial.

Uso:
  python scripts/apply_plan_migration.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect

from app import create_app
from models import db
from scripts.init_db import seed_plans


PLAN_COLUMNS = {
    "max_suppliers": "INT DEFAULT 2",
    "max_branches": "INT DEFAULT 1",
    "max_warehouses": "INT DEFAULT 1",
    "can_use_advanced_reports": "BOOLEAN DEFAULT FALSE",
    "can_use_multi_branch": "BOOLEAN DEFAULT FALSE",
    "can_customize_roles": "BOOLEAN DEFAULT FALSE",
}


def _has_column(table: str, column: str) -> bool:
    inspector = inspect(db.engine)
    return column in {col["name"] for col in inspector.get_columns(table)}


def _apply_columns() -> None:
    with db.engine.begin() as conn:
        for column, definition in PLAN_COLUMNS.items():
            if not _has_column("lempis_planes", column):
                conn.exec_driver_sql(
                    f"ALTER TABLE lempis_planes ADD COLUMN {column} {definition}"
                )


def main() -> None:
    os.environ.pop("DATABASE_URL", None)
    app = create_app()
    with app.app_context():
        db.create_all()
        _apply_columns()
        seed_plans()
        db.session.commit()
    print("migracion de planes ok")


if __name__ == "__main__":
    main()
