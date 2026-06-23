"""
Aplica columnas nuevas de configuración de impresión en instalaciones sin Alembic.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from models import db  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        inspector = inspect(db.engine)
        columns = {col["name"] for col in inspector.get_columns("lempis_config_impresion")}
        if "open_cash_drawer_on_print" not in columns:
            db.session.execute(text(
                "ALTER TABLE lempis_config_impresion "
                "ADD COLUMN open_cash_drawer_on_print TINYINT(1) NOT NULL DEFAULT 1"
            ))
            db.session.commit()
        if "enable_load_order_print" not in columns:
            db.session.execute(text(
                "ALTER TABLE lempis_config_impresion "
                "ADD COLUMN enable_load_order_print TINYINT(1) NOT NULL DEFAULT 0"
            ))
            db.session.commit()
    print("migracion impresion ok")


if __name__ == "__main__":
    main()
