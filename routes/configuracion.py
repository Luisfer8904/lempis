"""
Página de configuración del tenant:
- Datos de la empresa (nombre, RTN, dirección, teléfono, logo)
- Configuración SAR Honduras (CAI, rangos, EST/PV/TD, correlativo)
"""
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from flask_login import login_required

from models import db
from models.printing import TenantPrintSettings
from services.tenant_context import current_tenant
from services.permissions import permission_required, tenant_required
from services.data_excel import build_export_workbook, build_template_workbook, import_workbook
from services.uploads import is_allowed_image, save_tenant_logo

configuracion_bp = Blueprint("configuracion", __name__, url_prefix="/app/configuracion")


@configuracion_bp.route("/")
@login_required
@tenant_required
@permission_required("settings.manage")
def index():
    return redirect(url_for("configuracion.empresa"))


def _get_print_settings(tenant):
    settings = TenantPrintSettings.query.filter_by(tenant_id=tenant.id).first()
    if settings is None:
        settings = TenantPrintSettings(tenant_id=tenant.id)
        db.session.add(settings)
        db.session.flush()
    return settings


@configuracion_bp.route("/empresa", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("settings.manage")
def empresa():
    tenant = current_tenant()

    if request.method == "POST":
        tenant.name = (request.form.get("name") or "").strip() or tenant.name
        tenant.legal_name = (request.form.get("legal_name") or "").strip() or None
        tenant.tax_id = (request.form.get("tax_id") or "").strip() or None
        tenant.email = (request.form.get("email") or "").strip() or None
        tenant.phone = (request.form.get("phone") or "").strip() or None
        tenant.address = (request.form.get("address") or "").strip() or None
        tenant.logo_url = (request.form.get("logo_url") or "").strip() or None
        logo_file = request.files.get("logo_file")
        if logo_file and logo_file.filename:
            if not is_allowed_image(logo_file.filename):
                flash("El logo debe ser una imagen PNG, JPG, JPEG, GIF, WEBP o BMP.", "danger")
                return redirect(url_for("configuracion.empresa"))
            saved_logo_url = save_tenant_logo(logo_file, tenant.id)
            if saved_logo_url:
                tenant.logo_url = saved_logo_url
        tenant.country_code = (request.form.get("country_code") or tenant.country_code).strip()
        tenant.currency = (request.form.get("currency") or tenant.currency).strip()
        db.session.commit()
        flash("Datos de empresa actualizados.", "success")
        return redirect(url_for("configuracion.empresa"))

    return render_template("configuracion/empresa.html", tenant=tenant)


@configuracion_bp.route("/facturacion", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("settings.manage")
def facturacion():
    """Configuración del modo de facturación (Simple / SAR / etc.)."""
    from services.invoice_mode import available_modes
    tenant = current_tenant()

    if request.method == "POST":
        new_mode = (request.form.get("invoice_mode") or "simple").strip()
        # Solo permitimos cambiar de modo si no hay facturas emitidas todavía
        if new_mode != (tenant.invoice_mode or "simple") and tenant.invoices:
            flash(
                "No puedes cambiar el modo de facturación porque ya hay facturas emitidas. "
                "Para cambiarlo, anula primero las facturas o contacta soporte.",
                "warning",
            )
            return redirect(url_for("configuracion.facturacion"))

        tenant.invoice_mode = new_mode

        # Campos modo SIMPLE
        if new_mode == "simple":
            tenant.invoice_prefix = (request.form.get("invoice_prefix") or "").strip()[:20]
            next_num = request.form.get("next_invoice_number_simple")
            if next_num and not tenant.invoices:
                tenant.next_invoice_number = max(int(next_num), 1)

        db.session.commit()
        flash("Modo de facturación actualizado.", "success")
        return redirect(url_for("configuracion.facturacion"))

    return render_template(
        "configuracion/facturacion.html",
        tenant=tenant,
        modes=available_modes(),
    )


@configuracion_bp.route("/sar", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("settings.manage")
def sar():
    """Configuración específica SAR Honduras (solo aplica si modo=sar_hn)."""
    tenant = current_tenant()

    if (tenant.invoice_mode or "simple") != "sar_hn":
        flash("Esta sección solo aplica al modo SAR Honduras. Cambia el modo primero.", "warning")
        return redirect(url_for("configuracion.facturacion"))

    if request.method == "POST":
        tenant.cai_code = (request.form.get("cai_code") or "").strip().upper() or None

        valid_from = request.form.get("cai_valid_from")
        valid_until = request.form.get("cai_valid_until")
        tenant.cai_valid_from = datetime.strptime(valid_from, "%Y-%m-%d") if valid_from else None
        tenant.cai_valid_until = datetime.strptime(valid_until, "%Y-%m-%d") if valid_until else None

        tenant.cai_range_start = int(request.form.get("cai_range_start") or 1)
        tenant.cai_range_end = int(request.form.get("cai_range_end") or 99999999)

        tenant.establecimiento = (request.form.get("establecimiento") or "001").strip().zfill(3)[:3]
        tenant.punto_emision = (request.form.get("punto_emision") or "001").strip().zfill(3)[:3]
        tenant.tipo_documento = (request.form.get("tipo_documento") or "01").strip().zfill(2)[:2]

        # Solo permitir cambiar el correlativo si no hay facturas aún
        next_num = request.form.get("next_invoice_number")
        if next_num and not tenant.invoices:
            tenant.next_invoice_number = max(int(next_num), tenant.cai_range_start or 1)
        elif not tenant.invoices:
            tenant.next_invoice_number = tenant.cai_range_start or 1

        db.session.commit()
        flash("Configuración SAR actualizada correctamente.", "success")
        return redirect(url_for("configuracion.sar"))

    return render_template("configuracion/sar.html", tenant=tenant)


@configuracion_bp.route("/impresion", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("settings.manage")
def impresion():
    tenant = current_tenant()
    settings = _get_print_settings(tenant)

    if request.method == "POST":
        settings.quick_sale_format = (request.form.get("quick_sale_format") or "thermal_receipt").strip()
        settings.detailed_sale_format = (request.form.get("detailed_sale_format") or "letter").strip()
        settings.payment_receipt_format = (request.form.get("payment_receipt_format") or "thermal_receipt").strip()
        settings.receipt_paper_width = (request.form.get("receipt_paper_width") or "80mm").strip()
        settings.document_page_format = (request.form.get("document_page_format") or "letter").strip()
        settings.thermal_printer_enabled = bool(request.form.get("thermal_printer_enabled"))
        settings.open_cash_drawer_on_print = bool(request.form.get("open_cash_drawer_on_print"))
        db.session.commit()
        flash("Configuración de impresión actualizada.", "success")
        return redirect(url_for("configuracion.impresion"))

    return render_template("configuracion/impresion.html", tenant=tenant, settings=settings)


@configuracion_bp.route("/datos")
@login_required
@tenant_required
@permission_required("settings.manage")
def datos():
    return render_template("configuracion/datos.html", tenant=current_tenant())


@configuracion_bp.route("/ubicaciones", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("settings.manage")
def ubicaciones():
    return redirect(url_for("ubicaciones.index"))


@configuracion_bp.route("/datos/plantilla")
@login_required
@tenant_required
@permission_required("settings.manage")
def datos_plantilla():
    return send_file(
        build_template_workbook(),
        as_attachment=True,
        download_name="plantilla-lempis.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@configuracion_bp.route("/datos/exportar")
@login_required
@tenant_required
@permission_required("settings.manage")
def datos_exportar():
    tenant = current_tenant()
    return send_file(
        build_export_workbook(tenant),
        as_attachment=True,
        download_name=f"lempis-datos-{tenant.id}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@configuracion_bp.route("/datos/importar", methods=["POST"])
@login_required
@tenant_required
@permission_required("settings.manage")
def datos_importar():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("Selecciona un archivo Excel para importar.", "warning")
        return redirect(url_for("configuracion.datos"))
    if not upload.filename.lower().endswith(".xlsx"):
        flash("El archivo debe ser .xlsx.", "danger")
        return redirect(url_for("configuracion.datos"))

    try:
        stats = import_workbook(current_tenant(), upload)
    except Exception as exc:
        flash(f"No se pudo importar el archivo: {exc}", "danger")
        return redirect(url_for("configuracion.datos"))

    total_created = (
        stats["categories_created"] + stats["products_created"]
        + stats["customers_created"] + stats["suppliers_created"]
    )
    total_updated = (
        stats["categories_updated"] + stats["products_updated"]
        + stats["customers_updated"] + stats["suppliers_updated"]
    )
    flash(f"Importación completada: {total_created} creado(s), {total_updated} actualizado(s).", "success")
    for error in stats["errors"]:
        flash(error, "warning")
    return redirect(url_for("configuracion.datos"))
