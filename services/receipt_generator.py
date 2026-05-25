"""
Generador de recibos de pago (abono a factura) en PDF.
Formato media carta, limpio, con datos del emisor + receptor + pago.
"""
from __future__ import annotations

from io import BytesIO
from datetime import datetime
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)

from services.datetime_utils import format_local_datetime

INDIGO = colors.HexColor("#4f46e5")
EMERALD = colors.HexColor("#10b981")
SLATE_500 = colors.HexColor("#64748b")
SLATE_200 = colors.HexColor("#e2e8f0")
SLATE_50 = colors.HexColor("#f8fafc")


def generate_payment_receipt_pdf(payment, tenant) -> BytesIO:
    """Genera el recibo de pago de un InvoicePayment. Devuelve BytesIO."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=20 * mm,
        title=f"Recibo de Pago - Factura {payment.invoice.number}",
        author=tenant.name,
    )

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, leading=13)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10, textColor=SLATE_500)
    label = ParagraphStyle("label", parent=body, fontSize=8, textColor=SLATE_500)
    big = ParagraphStyle("big", parent=body, fontSize=16, leading=20, textColor=INDIGO, fontName="Helvetica-Bold")
    huge = ParagraphStyle("huge", parent=body, fontSize=28, leading=32, textColor=EMERALD, fontName="Helvetica-Bold", alignment=1)

    story = []
    inv = payment.invoice

    # ====== CABECERA ======
    emisor = inv.emisor_name or tenant.legal_name or tenant.name
    emisor_block = Paragraph(
        f"<b>{emisor}</b><br/>"
        + (f"<font size='8' color='#64748b'>RTN: {inv.emisor_tax_id or tenant.tax_id or ''}</font><br/>"
           if (inv.emisor_tax_id or tenant.tax_id) else "")
        + (f"<font size='8'>{(inv.emisor_address or tenant.address or '').replace(chr(10), '<br/>')}</font>"
           if (inv.emisor_address or tenant.address) else "")
        + (f"<br/><font size='8'>{tenant.phone or ''}{(' • ' + tenant.email) if tenant.email else ''}</font>"
           if (tenant.phone or tenant.email) else ""),
        body,
    )

    titulo = Paragraph(
        "<b><font size='18' color='#10b981'>RECIBO</font></b><br/>"
        "<b><font size='12' color='#10b981'>DE PAGO</font></b><br/>"
        f"<font size='8' color='#64748b'>Recibo #{payment.id:06d}</font><br/>"
        f"<font size='9'>{format_local_datetime(payment.paid_at, '%d/%m/%Y', tenant)}</font>",
        body,
    )

    header = Table([[emisor_block, titulo]], colWidths=[110 * mm, 60 * mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header)
    story.append(Spacer(1, 8 * mm))

    # ====== Banda monto recibido ======
    monto_box = Table(
        [[Paragraph(f"MONTO RECIBIDO<br/><b>{inv.currency} {Decimal(payment.amount):,.2f}</b>", huge)]],
        colWidths=[170 * mm],
    )
    monto_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ecfdf5")),
        ("BOX", (0, 0), (-1, -1), 1, EMERALD),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(monto_box)
    story.append(Spacer(1, 6 * mm))

    # ====== RECIBÍ DE ======
    receptor_name = inv.receptor_name or (inv.customer.name if inv.customer else "Consumidor final")
    receptor_rtn = inv.receptor_tax_id or (inv.customer.tax_id if inv.customer else "") or ""
    metodos = {
        "efectivo": "💵 Efectivo", "transferencia": "🏦 Transferencia",
        "tarjeta": "💳 Tarjeta", "credito": "📋 Crédito", "otro": "Otro",
    }
    metodo = metodos.get(payment.payment_method, payment.payment_method or "Efectivo")

    info_data = [
        [Paragraph("<b>RECIBÍ DE:</b>", label),
         Paragraph(f"<b>{receptor_name}</b>" + (f"<br/><font size='8' color='#64748b'>RTN: {receptor_rtn}</font>" if receptor_rtn else ""), body)],
        [Paragraph("<b>POR CONCEPTO DE:</b>", label),
         Paragraph(f"Abono a factura <b>{inv.number}</b>", body)],
        [Paragraph("<b>FORMA DE PAGO:</b>", label),
         Paragraph(f"<b>{metodo}</b>" + (f"<br/><font size='8'>Ref: {payment.reference}</font>" if payment.reference else ""), body)],
    ]
    if payment.notes:
        info_data.append([Paragraph("<b>NOTAS:</b>", label), Paragraph(payment.notes, body)])

    info_table = Table(info_data, colWidths=[45 * mm, 125 * mm])
    info_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, SLATE_200),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 6 * mm))

    # ====== ESTADO DE CUENTA DE LA FACTURA ======
    estado_data = [
        ["Total factura:",   f"{inv.currency} {Decimal(inv.total):,.2f}"],
        ["Pagado antes:",    f"{inv.currency} {(Decimal(inv.amount_paid) - Decimal(payment.amount)):,.2f}"],
        ["Pagado ahora:",    f"{inv.currency} {Decimal(payment.amount):,.2f}"],
        ["Saldo restante:",  f"{inv.currency} {Decimal(inv.amount_due):,.2f}"],
    ]
    estado_table = Table(estado_data, colWidths=[50 * mm, 50 * mm], hAlign="RIGHT")
    estado_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.5, INDIGO),
        ("TEXTCOLOR", (0, -1), (-1, -1), INDIGO),
        ("FONTSIZE", (0, -1), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(estado_table)
    story.append(Spacer(1, 12 * mm))

    # ====== Firmas ======
    firmas = Table(
        [[
            Paragraph("__________________________<br/><font size='8'>Recibí conforme</font>", small),
            Paragraph("__________________________<br/><font size='8'>Entregué conforme</font>", small),
        ]],
        colWidths=[85 * mm, 85 * mm],
    )
    firmas.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("TOPPADDING", (0, 0), (-1, -1), 20),
    ]))
    story.append(firmas)

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    buffer.seek(0)
    return buffer


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(SLATE_500)
    canvas.drawString(20 * mm, 10 * mm, f"Generado {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    canvas.drawRightString(
        doc.pagesize[0] - 20 * mm, 10 * mm,
        "Recibo de Pago • Hecho con Lempis",
    )
    canvas.restoreState()
