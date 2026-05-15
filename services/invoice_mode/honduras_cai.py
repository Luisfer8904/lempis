"""
Modo 'sar_hn': Factura Honduras con CAI/SAR.
- Numeración: EST-PV-TD-CORRELATIVO  (ej. 001-001-01-00000001)
- Requiere CAI vigente, RTN del emisor, rango autorizado
- PDF con leyenda SAR oficial y valor en letras (LEMPIRAS)
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO

from services.invoice_mode.base import InvoiceProvider, InvoiceProviderError
from services.plan_limits import check_can_emit_invoice, PlanLimitError


class HondurasCaiProvider(InvoiceProvider):
    code = "sar_hn"
    label = "Honduras — SAR con CAI"
    description = (
        "Factura fiscal Hondureña con Código de Autorización de Impresión (CAI), "
        "rango autorizado, RTN y formato EST-PV-TD-CORRELATIVO. "
        "Requerido para vender legalmente en Honduras."
    )
    requires_tax_id = True
    country_code = "HN"

    def validate_can_emit(self, tenant) -> None:
        if not tenant.has_cai_configured():
            raise InvoiceProviderError(
                "Debes configurar tu CAI y RTN en Configuración → Facturación → SAR antes de emitir."
            )
        now = datetime.utcnow()
        if tenant.cai_valid_until and tenant.cai_valid_until < now:
            raise InvoiceProviderError(
                f"Tu CAI venció el {tenant.cai_valid_until.strftime('%d/%m/%Y')}. "
                "Solicita uno nuevo en el SAR."
            )
        if (tenant.next_invoice_number or 1) > (tenant.cai_range_end or 0):
            raise InvoiceProviderError(
                f"Agotaste tu rango de facturas autorizado ({tenant.cai_range_end}). "
                "Solicita un nuevo CAI en el SAR."
            )
        try:
            check_can_emit_invoice(tenant)
        except PlanLimitError as e:
            raise InvoiceProviderError(str(e))

    def next_number(self, tenant) -> tuple[int, str]:
        correlativo = tenant.next_invoice_number or tenant.cai_range_start or 1
        return correlativo, self.format_number(tenant, correlativo)

    def format_number(self, tenant, correlativo: int) -> str:
        return tenant.format_invoice_number_sar(correlativo)

    def freeze_invoice(self, tenant, invoice) -> None:
        # Congelar datos SAR
        invoice.cai_code = tenant.cai_code
        invoice.cai_range_start = tenant.cai_range_start
        invoice.cai_range_end = tenant.cai_range_end
        invoice.cai_valid_until = tenant.cai_valid_until
        # Datos del emisor
        invoice.emisor_name = tenant.legal_name or tenant.name
        invoice.emisor_tax_id = tenant.tax_id
        invoice.emisor_address = tenant.address

    def generate_pdf(self, invoice, tenant) -> BytesIO:
        from services.pdf_generator import generate_invoice_pdf
        return generate_invoice_pdf(invoice, tenant)
