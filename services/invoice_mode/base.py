"""
Interfaz base para todos los modos de facturación.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from io import BytesIO


class InvoiceProviderError(Exception):
    """Error de un provider (validación, configuración, etc.)."""


class InvoiceProvider(ABC):
    """Interfaz que debe implementar cada modo de facturación."""

    # Override en cada subclase
    code: str = ""
    label: str = ""
    description: str = ""
    requires_tax_id: bool = False
    country_code: str | None = None  # 'HN', 'MX', etc. None = genérico

    @abstractmethod
    def validate_can_emit(self, tenant) -> None:
        """Lanza InvoiceProviderError si el tenant no puede emitir aún."""

    @abstractmethod
    def next_number(self, tenant) -> tuple[int, str]:
        """Devuelve (correlativo, número_formateado) para la próxima factura."""

    @abstractmethod
    def format_number(self, tenant, correlativo: int) -> str:
        """Formato del número para un correlativo dado."""

    @abstractmethod
    def freeze_invoice(self, tenant, invoice) -> None:
        """Copia datos relevantes del tenant a la factura (CAI, RTN, etc.)."""

    @abstractmethod
    def generate_pdf(self, invoice, tenant) -> BytesIO:
        """Genera el PDF de la factura específico del modo."""
