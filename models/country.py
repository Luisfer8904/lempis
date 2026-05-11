"""
Países y configuración de impuestos por país.
Permite que el SaaS opere en múltiples países LatAm.
"""
from sqlalchemy import Column, String, Numeric, Boolean, Integer, ForeignKey
from sqlalchemy.orm import relationship

from models import db
from models.base import TimestampMixin


class Country(db.Model, TimestampMixin):
    """Catálogo de países soportados."""
    __tablename__ = "lempis_paises"

    code = Column(String(2), primary_key=True)  # HN, GT, SV, NI, CR, PA, MX, CO, PE, ...
    name = Column(String(80), nullable=False)
    currency = Column(String(3), nullable=False)
    currency_symbol = Column(String(8), default="$")
    locale = Column(String(10), default="es")
    phone_prefix = Column(String(8))
    tax_label = Column(String(40))  # ISV, IVA, IGV, etc.
    default_tax_rate = Column(Numeric(5, 2), default=0)
    is_active = Column(Boolean, default=True)

    tax_configs = relationship("TaxConfig", back_populates="country", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Country {self.code} {self.name}>"


class TaxConfig(db.Model, TimestampMixin):
    """
    Configuración de impuestos por país.
    Un país puede tener varias tasas (ej. tasa general, tasa exenta, tasa reducida).
    """
    __tablename__ = "lempis_impuestos"

    id = Column(Integer, primary_key=True)
    country_code = Column(String(2), ForeignKey("lempis_paises.code"), nullable=False, index=True)
    code = Column(String(40), nullable=False)        # ej. "ISV15", "EXENTO"
    name = Column(String(80), nullable=False)        # ej. "ISV General 15%"
    rate = Column(Numeric(5, 2), nullable=False, default=0)
    is_default = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    country = relationship("Country", back_populates="tax_configs")
