"""
Modo 'simple': factura sin requisitos fiscales.
- Numeración: {prefix}{correlativo:06d}  →  "FAC-000001"
- Sin CAI, sin rangos, sin leyenda SAR
- Usable internacionalmente o para recibos internos
"""
from __future__ import annotations

from io import BytesIO

from services.invoice_mode.base import InvoiceProvider, InvoiceProviderError
from services.plan_limits import check_can_emit_invoice, PlanLimitError


class SimpleProvider(InvoiceProvider):
    code = "simple"
    label = "Factura simple"
    description = (
        "Numeración correlativa básica (ej. FAC-000001). "
        "Sin requisitos fiscales. Ideal para recibos, uso interno, o países sin facturación electrónica."
    )
    requires_tax_id = False
    country_code = None

    def validate_can_emit(self, tenant) -> None:
        # Solo valida límite de plan
        try:
            check_can_emit_invoice(tenant)
        except PlanLimitError as e:
            raise InvoiceProviderError(str(e))

    def next_number(self, tenant) -> tuple[int, str]:
        correlativo = tenant.next_invoice_number or 1
        return correlativo, self.format_number(tenant, correlativo)

    def format_number(self, tenant, correlativo: int) -> str:
        prefix = (tenant.invoice_prefix or "").strip()
        return f"{prefix}{correlativo:06d}"

    def freeze_invoice(self, tenant, invoice) -> None:
        # Solo congelamos datos del emisor para que el PDF se mantenga
        # consistente aunque la empresa cambie su nombre después.
        invoice.emisor_name = tenant.legal_name or tenant.name
        invoice.emisor_tax_id = tenant.tax_id
        invoice.emisor_address = tenant.address
        # Limpiar campos SAR para que el PDF no los muestre
        invoice.cai_code = None
        invoice.cai_range_start = None
        invoice.cai_range_end = None
        invoice.cai_valid_until = None

    def generate_pdf(self, invoice, tenant) -> BytesIO:
        from services.pdf_generator import generate_simple_invoice_pdf
        return generate_simple_invoice_pdf(invoice, tenant)
