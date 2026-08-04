"""
Aplica retiros parciales de caja en instalaciones sin Alembic.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from models import CashClosure, CashWithdrawal, db  # noqa: E402,F401
from sqlalchemy import inspect, text  # noqa: E402


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        inspector = inspect(db.engine)
        columns = {col["name"] for col in inspector.get_columns("lempis_cierres_diarios")}
        if "withdrawals_amount" not in columns:
            db.session.execute(text(
                "ALTER TABLE lempis_cierres_diarios "
                "ADD COLUMN withdrawals_amount DECIMAL(12,2) NOT NULL DEFAULT 0 "
                "AFTER expenses_amount"
            ))
            db.session.commit()

        db.session.execute(text(
            """
            UPDATE lempis_retiros_caja r
              JOIN lempis_cierres_diarios c
                ON c.tenant_id = r.tenant_id
               AND c.closure_date = DATE(r.withdrawal_date)
               SET r.closure_id = c.id
             WHERE r.closure_id IS NULL
            """
        ))
        db.session.execute(text(
            """
            UPDATE lempis_cierres_diarios c
            LEFT JOIN (
              SELECT
                r.tenant_id,
                DATE(r.withdrawal_date) AS d,
                SUM(r.amount) AS total
              FROM lempis_retiros_caja r
              GROUP BY r.tenant_id, DATE(r.withdrawal_date)
            ) rt
              ON rt.tenant_id = c.tenant_id
             AND rt.d = c.closure_date
            SET
              c.withdrawals_amount = COALESCE(rt.total, 0),
              c.expected_cash_amount = c.opening_amount
                                     + c.cash_sales_amount
                                     + c.receivable_cash_amount
                                     - c.expenses_amount
                                     - COALESCE(rt.total, 0),
              c.variance_amount = c.delivered_cash_amount
                                - (c.opening_amount
                                   + c.cash_sales_amount
                                   + c.receivable_cash_amount
                                   - c.expenses_amount
                                   - COALESCE(rt.total, 0))
            """
        ))
        db.session.commit()
    print("migracion retiros de caja ok")


if __name__ == "__main__":
    main()
