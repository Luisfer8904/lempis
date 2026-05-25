"""
Generador de PDF de facturas — formato SAR Honduras.

Incluye:
- Cabecera con datos del emisor
- Número de factura, fecha, CAI, rango autorizado
- Datos del cliente (receptor)
- Detalle de líneas
- Totales: subtotal, descuento, ISV, total
- Leyenda fiscal SAR Honduras
"""
from io import BytesIO
from datetime import datetime
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
)

from services.datetime_utils import format_local_datetime


# Paleta
INDIGO = colors.HexColor("#4f46e5")
SLATE_700 = colors.HexColor("#334155")
SLATE_500 = colors.HexColor("#64748b")
SLATE_200 = colors.HexColor("#e2e8f0")
SLATE_50 = colors.HexColor("#f8fafc")


def generate_invoice_pdf(invoice, tenant) -> BytesIO:
    """
    Genera el PDF de la factura. Devuelve BytesIO listo para send_file.
    """
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"Factura {invoice.number}",
        author=tenant.name,
    )

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=9, leading=11)
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=9, textColor=SLATE_500)
    label = ParagraphStyle("label", parent=body, fontSize=7, textColor=SLATE_500, leading=8)
    h1 = ParagraphStyle("h1", parent=body, fontSize=18, leading=22, textColor=INDIGO, fontName="Helvetica-Bold")
    h2 = ParagraphStyle("h2", parent=body, fontSize=11, leading=14, textColor=SLATE_700, fontName="Helvetica-Bold")
    mono = ParagraphStyle("mono", parent=body, fontName="Courier", fontSize=9)

    story = []

    # ====== CABECERA ======
    emisor_name = invoice.emisor_name or tenant.legal_name or tenant.name
    emisor_rtn = invoice.emisor_tax_id or tenant.tax_id or ""
    emisor_addr = invoice.emisor_address or tenant.address or ""
    emisor_phone = tenant.phone or ""
    emisor_email = tenant.email or ""

    emisor_block = Paragraph(
        f"<b>{emisor_name}</b><br/>"
        f"<font size='8' color='#64748b'>RTN: {emisor_rtn}</font><br/>"
        f"<font size='8'>{emisor_addr.replace(chr(10), '<br/>')}</font><br/>"
        f"<font size='8'>{emisor_phone}{(' • ' + emisor_email) if emisor_email else ''}</font>",
        body,
    )

    # Documento info
    issue_date = format_local_datetime(invoice.issue_date, "%d/%m/%Y", tenant, empty="")
    doc_block = Paragraph(
        f"<b><font size='14' color='#4f46e5'>FACTURA</font></b><br/>"
        f"<font size='12' name='Courier'><b>{invoice.number}</b></font><br/>"
        f"<font size='8' color='#64748b'>Fecha de emisión:</font><br/>"
        f"<font size='9'>{issue_date}</font>",
        body,
    )

    header_table = Table(
        [[emisor_block, doc_block]],
        colWidths=[100 * mm, 80 * mm],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 6 * mm))

    # ====== BANDA SAR ======
    cai_text = invoice.cai_code or "—"
    rango = f"{invoice.cai_range_start or '—'} — {invoice.cai_range_end or '—'}"
    vence = invoice.cai_valid_until.strftime("%d/%m/%Y") if invoice.cai_valid_until else "—"

    sar_table = Table([
        [Paragraph("<b>CAI:</b>", small), Paragraph(cai_text, mono)],
        [Paragraph("<b>Rango autorizado:</b>", small), Paragraph(rango, mono)],
        [Paragraph("<b>Fecha límite de emisión:</b>", small), Paragraph(vence, mono)],
    ], colWidths=[45 * mm, 135 * mm])
    sar_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SLATE_50),
        ("BOX", (0, 0), (-1, -1), 0.5, SLATE_200),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, SLATE_200),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(sar_table)
    story.append(Spacer(1, 6 * mm))

    # ====== CLIENTE + INFO PAGO ======
    cliente_name = invoice.receptor_name or (invoice.customer.name if invoice.customer else "Consumidor final")
    cliente_rtn = invoice.receptor_tax_id or (invoice.customer.tax_id if invoice.customer else "") or "—"
    cliente_addr = invoice.customer.address if invoice.customer else ""

    cliente_block = Paragraph(
        f"<font size='7' color='#64748b'><b>FACTURAR A:</b></font><br/>"
        f"<b>{cliente_name}</b><br/>"
        f"<font size='8' color='#64748b'>RTN: {cliente_rtn}</font><br/>"
        f"<font size='8'>{cliente_addr or ''}</font>",
        body,
    )

    metodos = {"efectivo": "Efectivo", "transferencia": "Transferencia", "tarjeta": "Tarjeta", "credito": "Crédito"}
    metodo_text = metodos.get(invoice.payment_method, invoice.payment_method)
    extra = ""
    if invoice.payment_method == "credito":
        extra = f"<br/><font size='8'>Plazo: {invoice.payment_terms_days} días"
        if invoice.due_date:
            extra += f"<br/>Vence: {invoice.due_date.strftime('%d/%m/%Y')}"
        extra += "</font>"

    pago_block = Paragraph(
        f"<font size='7' color='#64748b'><b>FORMA DE PAGO:</b></font><br/>"
        f"<b>{metodo_text}</b>{extra}",
        body,
    )

    info_table = Table([[cliente_block, pago_block]], colWidths=[110 * mm, 70 * mm])
    info_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 6 * mm))

    # ====== LÍNEAS ======
    items_data = [["#", "Descripción", "Cant.", "Precio", "ISV%", "Subtotal"]]
    for i, item in enumerate(invoice.items, start=1):
        desc = item.description or ""
        # Agregar info de lote/vencimiento si existe
        if item.batch_id and item.batch is not None:
            b = item.batch
            extra_parts = [f"Lote: {b.batch_number}"]
            if b.expiration_date:
                extra_parts.append(f"Vence: {b.expiration_date.strftime('%m/%Y')}")
            desc = f"{desc}<br/><font size='7' color='#64748b'>{' · '.join(extra_parts)}</font>"

        items_data.append([
            str(i),
            Paragraph(desc, body),
            f"{Decimal(item.quantity):.2f}",
            f"{Decimal(item.unit_price):.2f}",
            f"{Decimal(item.tax_rate):.2f}%",
            f"{Decimal(item.subtotal):.2f}",
        ])

    items_table = Table(
        items_data,
        colWidths=[10 * mm, 90 * mm, 20 * mm, 22 * mm, 15 * mm, 23 * mm],
        repeatRows=1,
    )
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INDIGO),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SLATE_50]),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, INDIGO),
        ("BOX", (0, 0), (-1, -1), 0.5, SLATE_200),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 5 * mm))

    # ====== TOTALES ======
    cur = invoice.currency
    totals_data = [
        ["Subtotal:", f"{cur} {Decimal(invoice.subtotal):.2f}"],
        ["Descuento:", f"{cur} {Decimal(invoice.discount_total):.2f}"],
        ["ISV:", f"{cur} {Decimal(invoice.tax_total):.2f}"],
        ["TOTAL:", f"{cur} {Decimal(invoice.total):.2f}"],
    ]
    totals_table = Table(totals_data, colWidths=[40 * mm, 40 * mm], hAlign="RIGHT")
    totals_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), INDIGO),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
        ("FONTSIZE", (0, -1), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 8 * mm))

    # ====== NOTAS ======
    if invoice.notes:
        story.append(Paragraph("<b>Notas:</b>", small))
        story.append(Paragraph(invoice.notes.replace("\n", "<br/>"), body))
        story.append(Spacer(1, 4 * mm))

    # ====== TOTAL EN LETRAS + LEYENDA SAR ======
    total_letras = _number_to_letters(invoice.total, cur)
    story.append(Paragraph(
        f"<b>Valor en letras:</b> {total_letras}",
        small,
    ))
    story.append(Spacer(1, 4 * mm))

    leyenda = (
        "La presente factura se encuentra autorizada por el "
        "Servicio de Administración de Rentas (SAR) mediante el CAI "
        f"indicado. Original: Cliente — Copia: Emisor. "
        f"\"{tenant.name}\" agradece su preferencia."
    )
    if invoice.status == "void":
        leyenda = "*** DOCUMENTO ANULADO *** " + leyenda

    story.append(Paragraph(leyenda, small))

    # Build
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    buffer.seek(0)
    return buffer


def _footer(canvas, doc):
    """Pie de página con marca de la app."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(SLATE_500)
    canvas.drawString(15 * mm, 10 * mm, f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    canvas.drawRightString(
        doc.pagesize[0] - 15 * mm, 10 * mm,
        f"Página {doc.page} • Hecho con Lempis"
    )
    canvas.restoreState()


# ============================================================
#  PDF MODO SIMPLE (sin CAI, sin leyenda SAR)
# ============================================================

def generate_simple_invoice_pdf(invoice, tenant) -> BytesIO:
    """
    PDF de factura modo 'simple': encabezado limpio sin elementos fiscales.
    Apto para recibos de venta, uso interno, o países sin facturación electrónica.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"Factura {invoice.number}",
        author=tenant.name,
    )

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=9, leading=11)
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=9, textColor=SLATE_500)

    story = []

    # Cabecera
    emisor_name = invoice.emisor_name or tenant.legal_name or tenant.name
    emisor_lines = [f"<b>{emisor_name}</b>"]
    if invoice.emisor_tax_id or tenant.tax_id:
        emisor_lines.append(f"<font size='8' color='#64748b'>ID: {invoice.emisor_tax_id or tenant.tax_id}</font>")
    addr = invoice.emisor_address or tenant.address
    if addr:
        emisor_lines.append(f"<font size='8'>{addr.replace(chr(10), '<br/>')}</font>")
    contact = []
    if tenant.phone: contact.append(tenant.phone)
    if tenant.email: contact.append(tenant.email)
    if contact:
        emisor_lines.append(f"<font size='8'>{' • '.join(contact)}</font>")

    emisor_block = Paragraph("<br/>".join(emisor_lines), body)

    issue_date = format_local_datetime(invoice.issue_date, "%d/%m/%Y", tenant, empty="")
    doc_block = Paragraph(
        f"<b><font size='14' color='#4f46e5'>FACTURA</font></b><br/>"
        f"<font size='12' name='Courier'><b>{invoice.number}</b></font><br/>"
        f"<font size='8' color='#64748b'>Fecha:</font> <font size='9'>{issue_date}</font>",
        body,
    )

    header_table = Table([[emisor_block, doc_block]], colWidths=[100 * mm, 80 * mm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 8 * mm))

    # Cliente + Pago
    cliente_name = invoice.receptor_name or "Consumidor final"
    cliente_block = Paragraph(
        f"<font size='7' color='#64748b'><b>CLIENTE</b></font><br/>"
        f"<b>{cliente_name}</b>" +
        (f"<br/><font size='8' color='#64748b'>ID: {invoice.receptor_tax_id}</font>" if invoice.receptor_tax_id else ""),
        body,
    )

    metodos = {"efectivo": "Efectivo", "transferencia": "Transferencia", "tarjeta": "Tarjeta", "credito": "Crédito"}
    metodo = metodos.get(invoice.payment_method, invoice.payment_method)
    extra = ""
    if invoice.payment_method == "credito":
        extra = f"<br/><font size='8'>Plazo: {invoice.payment_terms_days} días"
        if invoice.due_date:
            extra += f"<br/>Vence: {invoice.due_date.strftime('%d/%m/%Y')}"
        extra += "</font>"
    pago_block = Paragraph(
        f"<font size='7' color='#64748b'><b>FORMA DE PAGO</b></font><br/><b>{metodo}</b>{extra}",
        body,
    )

    info_table = Table([[cliente_block, pago_block]], colWidths=[110 * mm, 70 * mm])
    info_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(info_table)
    story.append(Spacer(1, 6 * mm))

    # Líneas
    items_data = [["#", "Descripción", "Cant.", "Precio", "Impto.%", "Subtotal"]]
    for i, item in enumerate(invoice.items, start=1):
        desc = item.description or ""
        if item.batch_id and item.batch is not None:
            b = item.batch
            parts = [f"Lote: {b.batch_number}"]
            if b.expiration_date:
                parts.append(f"Vence: {b.expiration_date.strftime('%m/%Y')}")
            desc = f"{desc}<br/><font size='7' color='#64748b'>{' · '.join(parts)}</font>"
        items_data.append([
            str(i),
            Paragraph(desc, body),
            f"{Decimal(item.quantity):.2f}",
            f"{Decimal(item.unit_price):.2f}",
            f"{Decimal(item.tax_rate):.2f}%",
            f"{Decimal(item.subtotal):.2f}",
        ])

    items_table = Table(
        items_data,
        colWidths=[10 * mm, 90 * mm, 20 * mm, 22 * mm, 18 * mm, 20 * mm],
        repeatRows=1,
    )
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INDIGO),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SLATE_50]),
        ("BOX", (0, 0), (-1, -1), 0.5, SLATE_200),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 5 * mm))

    # Totales
    cur = invoice.currency
    totals = [
        ["Subtotal:", f"{cur} {Decimal(invoice.subtotal):.2f}"],
        ["Descuento:", f"{cur} {Decimal(invoice.discount_total):.2f}"],
        ["Impuestos:", f"{cur} {Decimal(invoice.tax_total):.2f}"],
        ["TOTAL:", f"{cur} {Decimal(invoice.total):.2f}"],
    ]
    totals_table = Table(totals, colWidths=[40 * mm, 40 * mm], hAlign="RIGHT")
    totals_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), INDIGO),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
        ("FONTSIZE", (0, -1), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 8 * mm))

    # Notas
    if invoice.notes:
        story.append(Paragraph("<b>Notas:</b>", small))
        story.append(Paragraph(invoice.notes.replace("\n", "<br/>"), body))
        story.append(Spacer(1, 4 * mm))

    if invoice.status == "void":
        story.append(Paragraph(
            "<b>*** DOCUMENTO ANULADO ***</b>",
            ParagraphStyle("v", parent=body, fontSize=12, textColor=colors.HexColor("#dc2626"), alignment=1),
        ))
    else:
        story.append(Paragraph(
            f"Gracias por su preferencia — {tenant.name}",
            small,
        ))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    buffer.seek(0)
    return buffer


# ===== Helper: número a letras (Honduras) =====

_UNIDADES = ("", "UN", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE",
             "DIEZ", "ONCE", "DOCE", "TRECE", "CATORCE", "QUINCE", "DIECISÉIS",
             "DIECISIETE", "DIECIOCHO", "DIECINUEVE", "VEINTE")

_DECENAS = ("", "", "VEINTI", "TREINTA", "CUARENTA", "CINCUENTA",
            "SESENTA", "SETENTA", "OCHENTA", "NOVENTA")

_CENTENAS = ("", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS",
             "QUINIENTOS", "SEISCIENTOS", "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS")


def _three_digits(n: int) -> str:
    """0-999 → letras."""
    if n == 0:
        return ""
    if n == 100:
        return "CIEN"
    centena = n // 100
    resto = n % 100
    parts = []
    if centena:
        parts.append(_CENTENAS[centena])
    if resto:
        if resto <= 20:
            parts.append(_UNIDADES[resto])
        else:
            dec = resto // 10
            uni = resto % 10
            if dec == 2 and uni:
                parts.append(f"{_DECENAS[dec]}{_UNIDADES[uni].lower()}")
            elif uni:
                parts.append(f"{_DECENAS[dec]} Y {_UNIDADES[uni]}")
            else:
                parts.append(_DECENAS[dec])
    return " ".join(parts).strip()


def _number_to_letters(amount, currency="HNL") -> str:
    """Convierte 1234.56 → 'MIL DOSCIENTOS TREINTA Y CUATRO LEMPIRAS CON 56/100'."""
    try:
        amount = Decimal(str(amount))
    except Exception:
        return ""

    entero = int(amount)
    decimales = int(round((amount - entero) * 100))

    moneda = {"HNL": "LEMPIRAS", "USD": "DÓLARES", "GTQ": "QUETZALES",
              "MXN": "PESOS", "NIO": "CÓRDOBAS"}.get(currency, currency)

    if entero == 0:
        texto = "CERO"
    elif entero < 1000:
        texto = _three_digits(entero) or "CERO"
    elif entero < 1_000_000:
        miles = entero // 1000
        resto = entero % 1000
        miles_txt = "UN" if miles == 1 else _three_digits(miles)
        texto = f"{miles_txt} MIL" + (f" {_three_digits(resto)}" if resto else "")
    else:
        millones = entero // 1_000_000
        resto = entero % 1_000_000
        m_txt = "UN MILLÓN" if millones == 1 else f"{_three_digits(millones)} MILLONES"
        if resto:
            miles = resto // 1000
            r2 = resto % 1000
            if miles:
                miles_txt = "UN" if miles == 1 else _three_digits(miles)
                m_txt += f" {miles_txt} MIL"
            if r2:
                m_txt += f" {_three_digits(r2)}"
        texto = m_txt

    return f"{texto} {moneda} CON {decimales:02d}/100"
