"""
Página de configuración del tenant:
- Datos de la empresa (nombre, RTN, dirección, teléfono, logo)
- Configuración SAR Honduras (CAI, rangos, EST/PV/TD, correlativo)
"""
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required

from models import db
from services.tenant_context import current_tenant
from services.permissions import admin_required, tenant_required

configuracion_bp = Blueprint("configuracion", __name__, url_prefix="/app/configuracion")


@configuracion_bp.route("/")
@login_required
@tenant_required
def index():
    return redirect(url_for("configuracion.empresa"))


@configuracion_bp.route("/empresa", methods=["GET", "POST"])
@login_required
@admin_required
@tenant_required
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
        tenant.country_code = (request.form.get("country_code") or tenant.country_code).strip()
        tenant.currency = (request.form.get("currency") or tenant.currency).strip()
        db.session.commit()
        flash("Datos de empresa actualizados.", "success")
        return redirect(url_for("configuracion.empresa"))

    return render_template("configuracion/empresa.html", tenant=tenant)


@configuracion_bp.route("/facturacion", methods=["GET", "POST"])
@login_required
@admin_required
@tenant_required
def facturacion():
    """Configuración SAR Honduras (CAI, rangos, EST/PV/TD)."""
    tenant = current_tenant()

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

        # Solo permitir cambiar el correlativo si no hay facturas aún (evitar inconsistencias)
        next_num = request.form.get("next_invoice_number")
        if next_num and not tenant.invoices:
            tenant.next_invoice_number = max(int(next_num), tenant.cai_range_start)
        elif not tenant.invoices:
            tenant.next_invoice_number = tenant.cai_range_start

        db.session.commit()
        flash("Configuración SAR actualizada correctamente.", "success")
        return redirect(url_for("configuracion.facturacion"))

    return render_template("configuracion/facturacion.html", tenant=tenant)
