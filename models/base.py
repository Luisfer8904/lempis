"""
Mixins reutilizables para todos los modelos.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, DateTime, ForeignKey
from models import db


class TimestampMixin:
    """Añade created_at y updated_at automáticos."""
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class TenantScopedMixin:
    """
    Mixin para todos los modelos que pertenecen a un tenant.
    Garantiza el aislamiento de datos por empresa.
    """

    @classmethod
    def __declare_last__(cls):
        # Hook para futuras validaciones a nivel de clase
        pass

    @classmethod
    def for_tenant(cls, tenant_id):
        """Query helper: filtra automáticamente por tenant."""
        return cls.query.filter_by(tenant_id=tenant_id)


def tenant_fk():
    """FK estándar a lempis_empresas.id, indexada y obligatoria."""
    return Column(
        Integer,
        ForeignKey("lempis_empresas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
