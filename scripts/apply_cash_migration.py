"""
Aplica las tablas de caja diaria en instalaciones sin Alembic.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from models import db  # noqa: E402


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
    print("migracion caja ok")


if __name__ == "__main__":
    main()
