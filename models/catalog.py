"""
Catálogo del tenant: clientes, productos, categorías.
Todos los modelos están aislados por tenant_id.
"""
from sqlalchemy import Column, Integer, String, Boolean, Numeric, Text, ForeignKey, UniqueConstraint, Enum
from sqlalchemy.orm import relationship

from models import db
from models.base import TimestampMixin, tenant_fk


class Category(db.Model, TimestampMixin):
    """Categorías de productos por tenant."""
    __tablename__ = "lempis_categorias"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_lempis_categorias_tenant_name"),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()
    name = Column(String(80), nullable=False)
    description = Column(String(255))
    color = Column(String(20))

    products = relationship("Product", back_populates="category")


class Product(db.Model, TimestampMixin):
    """Producto o servicio que vende el tenant."""
    __tablename__ = "lempis_productos"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_lempis_productos_tenant_sku"),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()
    category_id = Column(Integer, ForeignKey("lempis_categorias.id", ondelete="SET NULL"))

    sku = Column(String(60), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    description = Column(Text)

    kind = Column(
        Enum("product", "service", name="product_kind"),
        default="product",
        nullable=False,
    )

    price = Column(Numeric(12, 2), nullable=False, default=0)
    cost = Column(Numeric(12, 2), default=0)
    stock = Column(Integer, default=0)
    track_stock = Column(Boolean, default=True)

    tax_config_id = Column(Integer, ForeignKey("lempis_impuestos.id"), nullable=True)
    image_url = Column(String(255))
    is_active = Column(Boolean, default=True)

    tenant = relationship("Tenant", back_populates="products")
    category = relationship("Category", back_populates="products")
    invoice_items = relationship("InvoiceItem", back_populates="product")

    def __repr__(self):
        return f"<Product {self.sku} {self.name}>"


class Customer(db.Model, TimestampMixin):
    """Cliente final del tenant (a quien se le factura)."""
    __tablename__ = "lempis_clientes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "tax_id", name="uq_lempis_clientes_tenant_taxid"),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()

    name = Column(String(160), nullable=False)
    tax_id = Column(String(40))   # RTN/RUC/RFC/NIT
    email = Column(String(160))
    phone = Column(String(40))
    address = Column(Text)
    city = Column(String(80))
    country_code = Column(String(2), ForeignKey("lempis_paises.code"))

    notes = Column(Text)
    is_active = Column(Boolean, default=True)

    tenant = relationship("Tenant", back_populates="customers")
    invoices = relationship("Invoice", back_populates="customer")

    def __repr__(self):
        return f"<Customer {self.name}>"
