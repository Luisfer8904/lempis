"""
Aplica la migración de sedes, bodegas e inventario por ubicación.

Uso:
    python scripts/apply_locations_migration.py
"""
from app import create_app
from sqlalchemy import inspect
from models import db
from models.tenant import Tenant
from models.locations import Branch, Warehouse, WarehouseStock, StockMovement  # noqa: F401
from services.locations import sync_default_warehouse_stock


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def main() -> None:
    app = create_app()
    with app.app_context():
        db.create_all()
        inspector = inspect(db.engine)

        with db.engine.begin() as conn:
            if not _has_column(inspector, "lempis_facturas", "warehouse_id"):
                conn.exec_driver_sql(
                    "ALTER TABLE lempis_facturas "
                    "ADD COLUMN warehouse_id INT NULL"
                )
            if not _has_column(inspector, "lempis_compras", "warehouse_id"):
                conn.exec_driver_sql(
                    "ALTER TABLE lempis_compras "
                    "ADD COLUMN warehouse_id INT NULL"
                )

        for tenant in Tenant.query.all():
            sync_default_warehouse_stock(tenant)
        db.session.commit()

    print("migracion sedes/bodegas ok")


if __name__ == "__main__":
    main()
