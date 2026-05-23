from sqlalchemy import Boolean, Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from models import db
from models.base import TimestampMixin


class TenantPrintSettings(db.Model, TimestampMixin):
    __tablename__ = "lempis_config_impresion"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(
        Integer,
        ForeignKey("lempis_empresas.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    quick_sale_format = Column(String(30), nullable=False, default="thermal_receipt")
    detailed_sale_format = Column(String(30), nullable=False, default="letter")
    payment_receipt_format = Column(String(30), nullable=False, default="thermal_receipt")
    receipt_paper_width = Column(String(10), nullable=False, default="80mm")
    document_page_format = Column(String(20), nullable=False, default="letter")
    thermal_printer_enabled = Column(Boolean, nullable=False, default=True)

    tenant = relationship("Tenant")

