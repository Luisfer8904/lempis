"""
Modelos base del sub-app IVG dentro de la misma base de datos.
Usan prefijo `ivg_` para mantener aislamiento lógico sin otra BD.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from werkzeug.security import check_password_hash, generate_password_hash

from models import db
from models.base import TimestampMixin


class IVGUser(db.Model, TimestampMixin):
    __tablename__ = "ivg_usuarios"
    __table_args__ = (
        UniqueConstraint("username", name="uq_ivg_usuarios_username"),
        UniqueConstraint("email", name="uq_ivg_usuarios_email"),
    )

    id = Column(Integer, primary_key=True)
    username = Column(String(60), nullable=False, index=True)
    email = Column(String(160), index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(120))
    phone = Column(String(40))
    role = Column(String(40), default="cajero", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    last_login_at = Column(DateTime, default=datetime.utcnow)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def touch_login(self) -> None:
        self.last_login_at = datetime.utcnow()

    def is_superadmin(self) -> bool:
        return self.role == "superadmin"

    def is_admin(self) -> bool:
        return self.role == "admin"


class IVGClient(db.Model, TimestampMixin):
    __tablename__ = "ivg_clientes"

    id = Column(Integer, primary_key=True)
    name = Column(String(140), nullable=False, index=True)
    legal_name = Column(String(180))
    tax_id = Column(String(40))
    email = Column(String(160))
    phone = Column(String(40))
    city = Column(String(80))
    status = Column(String(30), default="prospecto", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    sales = relationship("IVGSale", back_populates="client")
    agenda_items = relationship("IVGAgendaItem", back_populates="client")


class IVGProduct(db.Model, TimestampMixin):
    __tablename__ = "ivg_productos"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, index=True)
    name = Column(String(140), nullable=False, index=True)
    category = Column(String(80))
    unit_price = Column(Numeric(10, 2), default=0)
    stock = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class IVGSale(db.Model, TimestampMixin):
    """
    Factura individual del negocio IVG.
    En IVG las ventas individuales son siempre a crédito.
    """
    __tablename__ = "ivg_ventas"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("ivg_clientes.id", ondelete="SET NULL"), index=True)

    sale_type = Column(String(20), default="credito", nullable=False)          # legado; IVG usa credito
    category = Column(String(30), default="herbicidas", nullable=False)        # herbicidas | concentrados
    status = Column(String(20), default="registrada", nullable=False)          # registrada | parcial | pagada

    gross_amount = Column(Numeric(12, 2), default=0, nullable=False)
    balance_due = Column(Numeric(12, 2), default=0, nullable=False)

    sale_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    due_date = Column(DateTime)
    reference_number = Column(String(60), index=True)
    notes = Column(Text)

    client = relationship("IVGClient", back_populates="sales")
    payments = relationship("IVGPayment", back_populates="sale", cascade="all, delete-orphan")


class IVGCashSummary(db.Model, TimestampMixin):
    """
    Resumen de ventas de contado.
    Registra el total del día y cómo se distribuyó entre efectivo y transferencia.
    La apertura normalmente corresponde al cierre real del día anterior.
    El cierre esperado se calcula con apertura + efectivo.
    La diferencia muestra sobrante (>0) o faltante (<0).
    """
    __tablename__ = "ivg_contado"

    id = Column(Integer, primary_key=True)
    summary_date = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    opening_amount = Column(Numeric(12, 2), default=0, nullable=False)
    total_amount = Column(Numeric(12, 2), default=0, nullable=False)
    cash_amount = Column(Numeric(12, 2), default=0, nullable=False)
    transfer_amount = Column(Numeric(12, 2), default=0, nullable=False)
    expected_close_amount = Column(Numeric(12, 2), default=0, nullable=False)
    actual_close_amount = Column(Numeric(12, 2), default=0, nullable=False)
    variance_amount = Column(Numeric(12, 2), default=0, nullable=False)
    notes = Column(Text)


class IVGPayment(db.Model, TimestampMixin):
    """
    Pago o abono ligado a una venta de crédito.
    """
    __tablename__ = "ivg_pagos"

    id = Column(Integer, primary_key=True)
    sale_id = Column(Integer, ForeignKey("ivg_ventas.id", ondelete="CASCADE"), nullable=False, index=True)

    payment_kind = Column(String(20), default="abono", nullable=False)         # pago | abono
    payment_method = Column(String(20), nullable=False)                         # efectivo | transferencia
    category = Column(String(30), default="herbicidas", nullable=False)        # snapshot categoría
    amount = Column(Numeric(12, 2), default=0, nullable=False)
    payment_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    notes = Column(Text)

    sale = relationship("IVGSale", back_populates="payments")


class IVGAgendaItem(db.Model, TimestampMixin):
    __tablename__ = "ivg_agenda"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("ivg_clientes.id", ondelete="SET NULL"), index=True)
    title = Column(String(140), nullable=False)
    activity_type = Column(String(40), default="seguimiento", nullable=False)
    status = Column(String(20), default="pendiente", nullable=False)
    priority = Column(String(20), default="media", nullable=False)
    scheduled_for = Column(DateTime, default=datetime.utcnow, nullable=False)
    notes = Column(Text)

    client = relationship("IVGClient", back_populates="agenda_items")
