"""
Facturas y sus líneas. Aisladas por tenant.
"""
from datetime import datetime
from decimal import Decimal
from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, Enum, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from models import db
from models.base import TimestampMixin, tenant_fk


class Invoice(db.Model, TimestampMixin):
    """Factura emitida por el tenant a un Customer."""
    __tablename__ = "lempis_facturas"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number", name="uq_lempis_facturas_tenant_number"),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()

    number = Column(String(40), nullable=False, index=True)
    issue_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    due_date = Column(DateTime)

    customer_id = Column(Integer, ForeignKey("lempis_clientes.id", ondelete="SET NULL"))
    issued_by_user_id = Column(Integer, ForeignKey("lempis_usuarios.id", ondelete="SET NULL"))

    # Totales
    currency = Column(String(3), default="HNL", nullable=False)
    subtotal = Column(Numeric(12, 2), default=0, nullable=False)
    tax_total = Column(Numeric(12, 2), default=0, nullable=False)
    discount_total = Column(Numeric(12, 2), default=0, nullable=False)
    total = Column(Numeric(12, 2), default=0, nullable=False)

    status = Column(
        Enum("draft", "issued", "paid", "partially_paid", "void", "overdue",
             name="invoice_status"),
        default="draft",
        nullable=False,
    )

    payment_method = Column(String(40))   # cash, transfer, card, ...
    notes = Column(Text)

    # Relaciones
    tenant = relationship("Tenant", back_populates="invoices")
    customer = relationship("Customer", back_populates="invoices")
    items = relationship(
        "InvoiceItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceItem.id",
    )

    def recalc_totals(self) -> None:
        """Recalcula subtotal, impuestos y total a partir de las líneas."""
        sub = Decimal("0")
        tax = Decimal("0")
        disc = Decimal("0")
        for it in self.items:
            sub += Decimal(it.subtotal or 0)
            tax += Decimal(it.tax_amount or 0)
            disc += Decimal(it.discount_amount or 0)
        self.subtotal = sub
        self.tax_total = tax
        self.discount_total = disc
        self.total = sub + tax - disc

    def __repr__(self):
        return f"<Invoice {self.number} tenant={self.tenant_id}>"


class InvoiceItem(db.Model, TimestampMixin):
    """Línea de factura."""
    __tablename__ = "lempis_detalle_facturas"

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()
    invoice_id = Column(Integer, ForeignKey("lempis_facturas.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("lempis_productos.id", ondelete="SET NULL"))

    description = Column(String(255), nullable=False)
    quantity = Column(Numeric(12, 2), default=1, nullable=False)
    unit_price = Column(Numeric(12, 2), default=0, nullable=False)

    tax_rate = Column(Numeric(5, 2), default=0)            # %
    tax_amount = Column(Numeric(12, 2), default=0)
    discount_amount = Column(Numeric(12, 2), default=0)

    subtotal = Column(Numeric(12, 2), default=0, nullable=False)

    invoice = relationship("Invoice", back_populates="items")
    product = relationship("Product", back_populates="invoice_items")

    def recalc(self) -> None:
        """Recalcula el subtotal y los impuestos de la línea."""
        qty = Decimal(self.quantity or 0)
        price = Decimal(self.unit_price or 0)
        rate = Decimal(self.tax_rate or 0)
        self.subtotal = qty * price
        self.tax_amount = (self.subtotal * rate / Decimal("100")).quantize(Decimal("0.01"))
