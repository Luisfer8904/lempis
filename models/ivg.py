"""
Modelos base del sub-app IVG dentro de la misma base de datos.
Usan prefijo `ivg_` para mantener aislamiento lógico sin otra BD.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, UniqueConstraint
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


class IVGProduct(db.Model, TimestampMixin):
    __tablename__ = "ivg_productos"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, index=True)
    name = Column(String(140), nullable=False, index=True)
    category = Column(String(80))
    unit_price = Column(Numeric(10, 2), default=0)
    stock = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
