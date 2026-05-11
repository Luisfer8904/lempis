"""
Auditoría: registra acciones importantes por tenant y usuario.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship

from models import db
from models.base import tenant_fk


class AuditLog(db.Model):
    """Registro de auditoría — útil para compliance y soporte."""
    __tablename__ = "lempis_auditoria"

    id = Column(Integer, primary_key=True)
    tenant_id = tenant_fk()
    user_id = Column(Integer, ForeignKey("lempis_usuarios.id", ondelete="SET NULL"))

    action = Column(String(80), nullable=False)         # invoice.create, user.invite, ...
    entity_type = Column(String(60))                    # Invoice, User, ...
    entity_id = Column(Integer)
    ip_address = Column(String(45))
    user_agent = Column(String(255))
    metadata_json = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = relationship("User")

    def __repr__(self):
        return f"<AuditLog {self.action} t={self.tenant_id}>"
