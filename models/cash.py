"""
Cierres diarios, aperturas y gastos de caja.
"""
from datetime import date, datetime
from sqlalchemy import Column, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship

from models import db
from models.base import TimestampMixin, tenant_fk


class CashClosure(db.Model, TimestampMixin):
    """Resumen de caja de un dia operativo."""
    __tablename__ = "lempis_cierres_diarios"

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()
    branch_id = Column(Integer, ForeignKey("lempis_sedes.id", ondelete="SET NULL"), nullable=True, index=True)
    warehouse_id = Column(Integer, ForeignKey("lempis_bodegas.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("lempis_usuarios.id", ondelete="SET NULL"), nullable=True)

    closure_date = Column(Date, default=date.today, nullable=False, index=True)
    status = Column(String(20), default="closed", nullable=False)
    opening_amount = Column(Numeric(12, 2), default=0, nullable=False)
    cash_sales_amount = Column(Numeric(12, 2), default=0, nullable=False)
    transfer_sales_amount = Column(Numeric(12, 2), default=0, nullable=False)
    card_sales_amount = Column(Numeric(12, 2), default=0, nullable=False)
    credit_sales_amount = Column(Numeric(12, 2), default=0, nullable=False)
    receivable_cash_amount = Column(Numeric(12, 2), default=0, nullable=False)
    receivable_transfer_amount = Column(Numeric(12, 2), default=0, nullable=False)
    receivable_card_amount = Column(Numeric(12, 2), default=0, nullable=False)
    expenses_amount = Column(Numeric(12, 2), default=0, nullable=False)
    withdrawals_amount = Column(Numeric(12, 2), default=0, nullable=False)
    expected_cash_amount = Column(Numeric(12, 2), default=0, nullable=False)
    actual_cash_amount = Column(Numeric(12, 2), default=0, nullable=False)
    delivered_cash_amount = Column(Numeric(12, 2), default=0, nullable=False)
    variance_amount = Column(Numeric(12, 2), default=0, nullable=False)
    notes = Column(Text)

    branch = relationship("Branch")
    warehouse = relationship("Warehouse")
    user = relationship("User")
    expenses = relationship("CashExpense", back_populates="closure")
    withdrawals = relationship("CashWithdrawal", back_populates="closure")

    @property
    def payment_total(self):
        return (
            (self.cash_sales_amount or 0)
            + (self.transfer_sales_amount or 0)
            + (self.card_sales_amount or 0)
            + (self.credit_sales_amount or 0)
        )

    @property
    def contado_sales_amount(self):
        """Ventas de contado = todo lo que no es a crédito (efectivo + transferencia + tarjeta)."""
        return (
            (self.cash_sales_amount or 0)
            + (self.transfer_sales_amount or 0)
            + (self.card_sales_amount or 0)
        )

    @property
    def closure_status(self):
        """
        Estado del cierre basado en la diferencia entre lo entregado y lo esperado.
        - 'cuadrado'  → diferencia == 0
        - 'sobrante'  → diferencia > 0 (se entregó más de lo esperado)
        - 'faltante'  → diferencia < 0 (se entregó menos de lo esperado)
        """
        diff = self.variance_amount or 0
        if diff == 0:
            return "cuadrado"
        return "sobrante" if diff > 0 else "faltante"

    @property
    def closure_status_label(self):
        return {
            "cuadrado": "Cuadrado",
            "sobrante": "Sobrante",
            "faltante": "Faltante",
        }.get(self.closure_status, "—")

    @property
    def is_closed(self):
        return self.status == "closed"


class CashExpense(db.Model, TimestampMixin):
    """Gasto registrado durante el dia de caja."""
    __tablename__ = "lempis_gastos_caja"

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()
    closure_id = Column(Integer, ForeignKey("lempis_cierres_diarios.id", ondelete="SET NULL"), nullable=True, index=True)
    branch_id = Column(Integer, ForeignKey("lempis_sedes.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("lempis_usuarios.id", ondelete="SET NULL"), nullable=True)

    expense_date = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    category = Column(String(80), nullable=False, default="General")
    description = Column(String(180), nullable=False)
    payment_method = Column(
        Enum("efectivo", "transferencia", "tarjeta", "otro", name="cash_expense_payment_method"),
        default="efectivo",
        nullable=False,
    )
    amount = Column(Numeric(12, 2), default=0, nullable=False)
    notes = Column(Text)

    closure = relationship("CashClosure", back_populates="expenses")
    branch = relationship("Branch")
    user = relationship("User")


class CashWithdrawal(db.Model, TimestampMixin):
    """Retiro parcial de efectivo durante el dia de caja."""
    __tablename__ = "lempis_retiros_caja"

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()
    closure_id = Column(Integer, ForeignKey("lempis_cierres_diarios.id", ondelete="SET NULL"), nullable=True, index=True)
    branch_id = Column(Integer, ForeignKey("lempis_sedes.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("lempis_usuarios.id", ondelete="SET NULL"), nullable=True)

    withdrawal_date = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    recipient = Column(String(120), nullable=False)
    description = Column(String(180), nullable=False)
    amount = Column(Numeric(12, 2), default=0, nullable=False)
    notes = Column(Text)

    closure = relationship("CashClosure", back_populates="withdrawals")
    branch = relationship("Branch")
    user = relationship("User")
