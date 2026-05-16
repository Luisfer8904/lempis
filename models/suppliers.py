"""
Proveedores del tenant. Cada empresa lleva su propia lista.
Multi-tenant, aislado por tenant_id.
"""
from sqlalchemy import Column, Integer, String, Boolean, Text, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import relationship

from models import db
from models.base import TimestampMixin, tenant_fk


class Supplier(db.Model, TimestampMixin):
    """Proveedor de mercadería o servicios."""
    __tablename__ = "lempis_proveedores"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_lempis_proveedores_tenant_code"),
        UniqueConstraint("tenant_id", "tax_id", name="uq_lempis_proveedores_tenant_taxid"),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()

    # Identificación
    code = Column(String(20))                  # Código interno (auto-asignado)
    name = Column(String(160), nullable=False) # Nombre comercial
    legal_name = Column(String(160))           # Razón social
    tax_id = Column(String(40))                # RTN/RUC/RFC/NIT

    # Contacto
    contact_name = Column(String(120))         # Persona de contacto
    email = Column(String(160))
    phone = Column(String(40))
    address = Column(Text)
    city = Column(String(80))
    country_code = Column(String(2), ForeignKey("lempis_paises.code"))

    # Condiciones comerciales
    # 0 = contado. 15, 30, 45, 60, 90 días de crédito
    payment_terms_days = Column(Integer, default=0)
    credit_limit = Column(Numeric(12, 2), default=0)
    # Si los precios en las facturas del proveedor ya incluyen el impuesto
    price_includes_tax = Column(Boolean, default=False)

    notes = Column(Text)
    is_active = Column(Boolean, default=True, nullable=False)

    # Relaciones
    purchases = relationship("Purchase", back_populates="supplier", lazy="dynamic")

    def __repr__(self):
        return f"<Supplier {self.code or self.id} {self.name}>"
