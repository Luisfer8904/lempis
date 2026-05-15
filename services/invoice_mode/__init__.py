"""
Modos de facturación pluggables (multi-país).

Cada modo es un "provider" que implementa la misma interfaz:
- code: identificador corto
- label: nombre amigable
- validate_can_emit(tenant)
- next_number(tenant)
- format_number(tenant, correlativo)
- freeze_invoice(tenant, invoice)  : copia datos congelados al emitir
- generate_pdf(invoice, tenant)    : PDF específico del modo
- requires_tax_id: bool            : si el tenant necesita RTN/RFC/etc

Para agregar un país nuevo: crear `services/invoice_mode/<country>.py`,
registrarlo en `_REGISTRY` aquí abajo, y se enchufa automáticamente.
"""
from __future__ import annotations

from typing import Dict
from services.invoice_mode.base import InvoiceProvider, InvoiceProviderError
from services.invoice_mode.simple import SimpleProvider
from services.invoice_mode.honduras_cai import HondurasCaiProvider


# Registry de providers disponibles. Agregar nuevos modos aquí.
_REGISTRY: Dict[str, InvoiceProvider] = {
    SimpleProvider.code: SimpleProvider(),
    HondurasCaiProvider.code: HondurasCaiProvider(),
}


def available_modes() -> list[dict]:
    """Lista los modos disponibles para mostrar en la UI."""
    return [
        {"code": p.code, "label": p.label, "description": p.description}
        for p in _REGISTRY.values()
    ]


def get_provider(tenant) -> InvoiceProvider:
    """Devuelve el provider del tenant. Default: SimpleProvider."""
    mode = getattr(tenant, "invoice_mode", None) or SimpleProvider.code
    return _REGISTRY.get(mode, _REGISTRY[SimpleProvider.code])


__all__ = ["InvoiceProvider", "InvoiceProviderError", "get_provider", "available_modes"]
