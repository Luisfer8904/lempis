"""
Catálogo del tenant: clientes, productos, categorías, lotes.
Todos los modelos están aislados por tenant_id.
"""
from datetime import date
from sqlalchemy import Column, Integer, String, Boolean, Numeric, Text, ForeignKey, UniqueConstraint, Enum, Date
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
    price_wholesale = Column(Numeric(12, 2), nullable=False, default=0)
    price_special = Column(Numeric(12, 2), nullable=False, default=0)
    cost = Column(Numeric(12, 2), default=0)
    stock = Column(Integer, default=0)
    track_stock = Column(Boolean, default=True)

    tax_config_id = Column(Integer, ForeignKey("lempis_impuestos.id"), nullable=True)
    image_url = Column(String(255))
    is_active = Column(Boolean, default=True)

    # Si está activo, el producto maneja lotes con vencimiento.
    # Default True para productos físicos; los servicios se crean con False.
    track_batches = Column(Boolean, default=True, nullable=False)

    tenant = relationship("Tenant", back_populates="products")
    category = relationship("Category", back_populates="products")
    invoice_items = relationship("InvoiceItem", back_populates="product")
    batches = relationship(
        "ProductBatch",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductBatch.expiration_date.asc()",
    )

    # ---------- helpers de lotes ----------

    def total_stock_from_batches(self) -> int:
        """Stock total disponible sumando todos los lotes con cantidad > 0."""
        return sum(int(b.remaining_quantity or 0) for b in self.batches)

    def active_batches(self):
        """Lotes con stock disponible, ordenados FIFO (más próximo a vencer primero)."""
        return [b for b in self.batches if (b.remaining_quantity or 0) > 0]

    def next_batch_to_consume(self):
        """Devuelve el lote a consumir por FIFO (más próximo a vencer con stock)."""
        actives = self.active_batches()
        return actives[0] if actives else None

    def price_for_tier(self, tier: str | None):
        """Devuelve el precio correspondiente a la categoría comercial elegida."""
        if tier == "mayorista":
            return self.price_wholesale if self.price_wholesale is not None else self.price
        if tier == "especial":
            return self.price_special if self.price_special is not None else self.price
        return self.price

    def __repr__(self):
        return f"<Product {self.sku} {self.name}>"


class ProductBatch(db.Model, TimestampMixin):
    """
    Lote de un producto con fechas de fabricación y vencimiento.
    Aislado por tenant. Mantiene cantidad inicial y remanente
    para trazabilidad histórica (no se borra al llegar a 0).
    """
    __tablename__ = "lempis_lotes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "product_id", "batch_number",
            name="uq_lempis_lotes_tenant_product_batch",
        ),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()
    product_id = Column(
        Integer,
        ForeignKey("lempis_productos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    batch_number = Column(String(60), nullable=False)
    manufacturing_date = Column(Date)
    expiration_date = Column(Date, index=True)

    initial_quantity = Column(Numeric(12, 2), default=0, nullable=False)
    remaining_quantity = Column(Numeric(12, 2), default=0, nullable=False)

    cost = Column(Numeric(12, 2), default=0)
    supplier = Column(String(160))
    notes = Column(Text)

    product = relationship("Product", back_populates="batches")
    invoice_items = relationship("InvoiceItem", back_populates="batch")

    # ---------- propiedades de estado ----------

    @property
    def is_expired(self) -> bool:
        return self.expiration_date is not None and self.expiration_date < date.today()

    @property
    def days_until_expiry(self):
        if self.expiration_date is None:
            return None
        return (self.expiration_date - date.today()).days

    @property
    def is_depleted(self) -> bool:
        return (self.remaining_quantity or 0) <= 0

    def status_label(self) -> str:
        """Devuelve un identificador corto del estado: vencido / por vencer / vigente / agotado."""
        if self.is_depleted:
            return "agotado"
        if self.is_expired:
            return "vencido"
        d = self.days_until_expiry
        if d is not None and d <= 30:
            return "por_vencer"
        return "vigente"

    def __repr__(self):
        return f"<ProductBatch {self.batch_number} prod={self.product_id}>"


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
    preferred_price_tier = Column(
        Enum("general", "mayorista", "especial", name="customer_price_tier"),
        nullable=False,
        default="general",
    )
    country_code = Column(String(2), ForeignKey("lempis_paises.code"))

    notes = Column(Text)
    is_active = Column(Boolean, default=True)

    tenant = relationship("Tenant", back_populates="customers")
    invoices = relationship("Invoice", back_populates="customer")

    def __repr__(self):
        return f"<Customer {self.name}>"
