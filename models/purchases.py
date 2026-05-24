"""
Compras de mercadería (entrada de inventario).

Flujo:
- draft     : compra registrada, NO afecta inventario aún (editable)
- received  : compra finalizada, alimenta lotes, sube stock, actualiza precios
- void      : anulada, devuelve stock al estado previo
"""
from datetime import datetime
from decimal import Decimal
from sqlalchemy import (
    Column, Integer, String, Boolean, Text, ForeignKey, Numeric, DateTime,
    Date, Enum, UniqueConstraint
)
from sqlalchemy.orm import relationship

from models import db
from models.base import TimestampMixin, tenant_fk


class Purchase(db.Model, TimestampMixin):
    """Compra a proveedor."""
    __tablename__ = "lempis_compras"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number", name="uq_lempis_compras_tenant_number"),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()

    # Numeración
    number = Column(String(40), nullable=False, index=True)       # interno: COMP-000001
    supplier_invoice_number = Column(String(60))                  # nro de la factura del proveedor

    # Relaciones
    supplier_id = Column(Integer, ForeignKey("lempis_proveedores.id", ondelete="SET NULL"))
    received_by_user_id = Column(Integer, ForeignKey("lempis_usuarios.id", ondelete="SET NULL"))
    warehouse_id = Column(Integer, ForeignKey("lempis_bodegas.id", ondelete="SET NULL"), nullable=True)

    # Fechas
    issue_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    received_at = Column(DateTime)                                # cuándo se finalizó
    due_date = Column(DateTime)                                   # vence el pago

    # Términos de pago
    terms_days = Column(Integer, default=0)                       # 0 = contado
    currency = Column(String(3), default="HNL", nullable=False)

    # Totales (se recalculan al guardar)
    subtotal = Column(Numeric(12, 2), default=0, nullable=False)
    tax_total = Column(Numeric(12, 2), default=0, nullable=False)
    discount_total = Column(Numeric(12, 2), default=0, nullable=False)
    total = Column(Numeric(12, 2), default=0, nullable=False)
    amount_paid = Column(Numeric(12, 2), default=0, nullable=False)

    status = Column(
        Enum("draft", "received", "void", name="purchase_status"),
        default="draft",
        nullable=False,
    )

    notes = Column(Text)

    # Relaciones
    supplier = relationship("Supplier", back_populates="purchases")
    items = relationship(
        "PurchaseItem",
        back_populates="purchase",
        cascade="all, delete-orphan",
        order_by="PurchaseItem.id",
    )

    @property
    def amount_due(self):
        """Saldo pendiente de pago."""
        return Decimal(self.total or 0) - Decimal(self.amount_paid or 0)

    @property
    def is_credit(self) -> bool:
        return (self.terms_days or 0) > 0

    def recalc_totals(self) -> None:
        sub = Decimal("0")
        tax = Decimal("0")
        for it in self.items:
            sub += Decimal(it.subtotal or 0)
            tax += Decimal(it.tax_amount or 0)
        self.subtotal = sub
        self.tax_total = tax
        self.total = sub + tax

    def __repr__(self):
        return f"<Purchase {self.number} {self.status}>"


class PurchaseItem(db.Model, TimestampMixin):
    """
    Línea de una compra.
    Captura simultáneamente:
    - Producto y cantidad/costo
    - Datos del LOTE (si aplica): número, fab, vencimiento
    - Opcional: NUEVO precio de venta a aplicar al producto al recibir
    """
    __tablename__ = "lempis_detalle_compras"

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()
    purchase_id = Column(
        Integer,
        ForeignKey("lempis_compras.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    product_id = Column(
        Integer,
        ForeignKey("lempis_productos.id", ondelete="SET NULL"),
    )
    # Se llena cuando se finaliza la compra y se crea el lote
    batch_id = Column(
        Integer,
        ForeignKey("lempis_lotes.id", ondelete="SET NULL"),
    )

    description = Column(String(255), nullable=False)
    quantity = Column(Numeric(12, 2), default=1, nullable=False)
    unit_cost = Column(Numeric(12, 2), default=0, nullable=False)

    tax_rate = Column(Numeric(5, 2), default=0)          # %
    tax_amount = Column(Numeric(12, 2), default=0)
    subtotal = Column(Numeric(12, 2), default=0, nullable=False)

    # Datos del lote (si el producto maneja lotes)
    batch_number = Column(String(60))
    manufacturing_date = Column(Date)
    expiration_date = Column(Date)

    # Nuevo precio de venta a aplicar al producto al recibir (opcional)
    new_sale_price = Column(Numeric(12, 2))

    purchase = relationship("Purchase", back_populates="items")
    product = relationship("Product")
    batch = relationship("ProductBatch")

    def recalc(self) -> None:
        qty = Decimal(self.quantity or 0)
        cost = Decimal(self.unit_cost or 0)
        rate = Decimal(self.tax_rate or 0)
        self.subtotal = qty * cost
        self.tax_amount = (self.subtotal * rate / Decimal("100")).quantize(Decimal("0.01"))
