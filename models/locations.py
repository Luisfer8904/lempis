"""
Sedes, bodegas e inventario por ubicación.
"""
from decimal import Decimal
from sqlalchemy import Column, Integer, String, Boolean, Numeric, Text, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.orm import relationship

from models import db
from models.base import TimestampMixin, tenant_fk


class Branch(db.Model, TimestampMixin):
    """Sede o sucursal de una empresa."""
    __tablename__ = "lempis_sedes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_lempis_sedes_tenant_name"),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()
    name = Column(String(120), nullable=False)
    code = Column(String(30))
    city = Column(String(80))
    address = Column(Text)
    is_default = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    warehouses = relationship(
        "Warehouse",
        back_populates="branch",
        cascade="all, delete-orphan",
        order_by="Warehouse.name.asc()",
    )

    def __repr__(self):
        return f"<Branch {self.name} tenant={self.tenant_id}>"


class Warehouse(db.Model, TimestampMixin):
    """Bodega, tienda o sala de ventas dentro de una sede."""
    __tablename__ = "lempis_bodegas"
    __table_args__ = (
        UniqueConstraint("tenant_id", "branch_id", "name", name="uq_lempis_bodegas_tenant_branch_name"),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()
    branch_id = Column(Integer, ForeignKey("lempis_sedes.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    code = Column(String(30))
    is_default = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    branch = relationship("Branch", back_populates="warehouses")
    stocks = relationship("WarehouseStock", back_populates="warehouse", cascade="all, delete-orphan")

    @property
    def label(self) -> str:
        return f"{self.branch.name} / {self.name}" if self.branch else self.name

    def __repr__(self):
        return f"<Warehouse {self.name} tenant={self.tenant_id}>"


class WarehouseStock(db.Model, TimestampMixin):
    """Existencia de un producto o lote en una bodega concreta."""
    __tablename__ = "lempis_inventario_bodegas"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "warehouse_id", "product_id", "batch_id",
            name="uq_lempis_inv_bodega_producto_lote",
        ),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()
    warehouse_id = Column(Integer, ForeignKey("lempis_bodegas.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("lempis_productos.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_id = Column(Integer, ForeignKey("lempis_lotes.id", ondelete="CASCADE"), nullable=True, index=True)
    quantity = Column(Numeric(12, 2), default=0, nullable=False)

    warehouse = relationship("Warehouse", back_populates="stocks")
    product = relationship("Product")
    batch = relationship("ProductBatch")

    def add(self, qty) -> None:
        self.quantity = Decimal(self.quantity or 0) + Decimal(str(qty or 0))

    def subtract(self, qty) -> None:
        self.quantity = max(Decimal(0), Decimal(self.quantity or 0) - Decimal(str(qty or 0)))


class StockMovement(db.Model, TimestampMixin):
    """Bitácora de entradas, salidas y transferencias de inventario."""
    __tablename__ = "lempis_movimientos_inventario"

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()
    product_id = Column(Integer, ForeignKey("lempis_productos.id", ondelete="SET NULL"), index=True)
    batch_id = Column(Integer, ForeignKey("lempis_lotes.id", ondelete="SET NULL"), index=True)
    source_warehouse_id = Column(Integer, ForeignKey("lempis_bodegas.id", ondelete="SET NULL"), nullable=True)
    target_warehouse_id = Column(Integer, ForeignKey("lempis_bodegas.id", ondelete="SET NULL"), nullable=True)
    quantity = Column(Numeric(12, 2), nullable=False)
    movement_type = Column(String(30), nullable=False)
    reference = Column(String(80))
    notes = Column(Text)
    moved_at = Column(DateTime)
