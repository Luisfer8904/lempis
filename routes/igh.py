"""
Sub-app privada para Inversiones Guevara Herrera.
Acceso separado usando usuarios IVG desde el mismo login de Lempis.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func, inspect

from models import db
from models.ivg import IVGAgendaItem, IVGCashSummary, IVGClient, IVGPayment, IVGSale, IVGUser


igh_bp = Blueprint("igh", __name__, url_prefix="/igh")


def _table_exists(model) -> bool:
    return inspect(db.engine).has_table(model.__tablename__)


def _current_igh_user():
    user_id = session.get("igh_user_id")
    if not user_id or not _table_exists(IVGUser):
        return None
    user = db.session.get(IVGUser, user_id)
    if user is None or not user.is_active:
        return None
    return user


def igh_login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _current_igh_user()
        if user is None:
            session.pop("igh_user_id", None)
            session.pop("igh_user", None)
            session.pop("igh_role", None)
            return redirect(url_for("auth.login"))
        return fn(*args, **kwargs)
    return wrapper


def igh_superadmin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _current_igh_user()
        if user is None:
            return redirect(url_for("auth.login"))
        if not user.is_superadmin():
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


def _safe_count(model) -> int:
    if not _table_exists(model):
        return 0
    return model.query.count()


def _safe_sum(model, column, *filters):
    if not _table_exists(model):
        return 0
    query = db.session.query(func.coalesce(func.sum(column), 0))
    if filters:
        query = query.filter(*filters)
    return float(query.scalar() or 0)


def _parse_decimal(raw_value: str, label: str) -> Decimal:
    value = (raw_value or "").strip().replace(",", "")
    if not value:
        raise ValueError(f"{label} es obligatorio.")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} no tiene un formato válido.") from exc
    if amount < 0:
        raise ValueError(f"{label} no puede ser negativo.")
    return amount


def _to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _parse_datetime(raw_value: str, label: str, required: bool = False):
    value = (raw_value or "").strip()
    if not value:
        if required:
            raise ValueError(f"{label} es obligatorio.")
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"{label} no tiene un formato válido.")


def _recalculate_sale_balance(sale: IVGSale) -> None:
    total_paid = sum((payment.amount or Decimal("0.00")) for payment in sale.payments)
    gross_amount = _to_decimal(sale.gross_amount)
    balance = max(gross_amount - _to_decimal(total_paid), Decimal("0.00"))
    sale.balance_due = balance
    if balance == 0:
        sale.status = "pagada"
    elif balance < gross_amount:
        sale.status = "parcial"
    else:
        sale.status = "registrada"


def _month_start():
    now = datetime.utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _latest_cash_summary():
    if not _table_exists(IVGCashSummary):
        return None
    return (
        IVGCashSummary.query.order_by(IVGCashSummary.summary_date.desc(), IVGCashSummary.id.desc()).first()
    )


def _base_context():
    current_igh_user = _current_igh_user()
    month_start = _month_start()
    pending_invoices = []
    recent_collections = []
    recent_cash_summaries = []
    top_clients = []
    if _table_exists(IVGSale):
        pending_invoices = (
            IVGSale.query.filter(
                IVGSale.balance_due > 0,
            )
            .order_by(IVGSale.due_date.asc(), IVGSale.sale_date.desc())
            .limit(8)
            .all()
        )
        top_clients = (
            db.session.query(
                IVGClient,
                func.coalesce(func.sum(IVGSale.balance_due), 0).label("pending_balance"),
            )
            .join(IVGSale, IVGSale.client_id == IVGClient.id)
            .group_by(IVGClient.id)
            .order_by(func.coalesce(func.sum(IVGSale.balance_due), 0).desc())
            .limit(6)
            .all()
        )
    if _table_exists(IVGPayment):
        recent_collections = (
            IVGPayment.query.order_by(IVGPayment.payment_date.desc(), IVGPayment.id.desc())
            .limit(8)
            .all()
        )
    if _table_exists(IVGCashSummary):
        recent_cash_summaries = (
            IVGCashSummary.query.order_by(IVGCashSummary.summary_date.desc(), IVGCashSummary.id.desc())
            .limit(6)
            .all()
        )
    latest_cash_summary = recent_cash_summaries[0] if recent_cash_summaries else None
    latest_variance = _to_decimal(latest_cash_summary.variance_amount) if latest_cash_summary else Decimal("0.00")
    return {
        "igh_user": current_igh_user.username if current_igh_user else session.get("igh_user"),
        "igh_current_user": current_igh_user,
        "igh_can_manage_users": bool(current_igh_user and current_igh_user.is_superadmin()),
        "company_name": "Inversiones Guevara Herrera",
        "ivg_counts": {
            "users": _safe_count(IVGUser),
            "clients": _safe_count(IVGClient),
            "sales": _safe_count(IVGSale),
            "payments": _safe_count(IVGPayment),
            "cash_summaries": _safe_count(IVGCashSummary),
            "agenda": _safe_count(IVGAgendaItem),
            "credit_sales": _safe_count(IVGSale),
            "pending_invoices": _safe_count(IVGSale) if not _table_exists(IVGSale) else IVGSale.query.filter(IVGSale.balance_due > 0).count(),
        },
        "ivg_metrics": {
            "receivable_total": _safe_sum(IVGSale, IVGSale.balance_due) if _table_exists(IVGSale) else 0,
            "cash_total": _safe_sum(IVGCashSummary, IVGCashSummary.total_amount) if _table_exists(IVGCashSummary) else 0,
            "payment_total": _safe_sum(IVGPayment, IVGPayment.amount) if _table_exists(IVGPayment) else 0,
            "sales_month_total": (
                (_safe_sum(IVGSale, IVGSale.gross_amount, IVGSale.sale_date >= month_start) if _table_exists(IVGSale) else 0) +
                (_safe_sum(IVGCashSummary, IVGCashSummary.total_amount, IVGCashSummary.summary_date >= month_start) if _table_exists(IVGCashSummary) else 0)
            ),
            "collections_month_total": _safe_sum(IVGPayment, IVGPayment.amount, IVGPayment.payment_date >= month_start) if _table_exists(IVGPayment) else 0,
            "transfer_total": _safe_sum(IVGCashSummary, IVGCashSummary.transfer_amount) if _table_exists(IVGCashSummary) else 0,
            "efectivo_total": _safe_sum(IVGCashSummary, IVGCashSummary.cash_amount) if _table_exists(IVGCashSummary) else 0,
            "herbicidas_credito": _safe_sum(IVGSale, IVGSale.gross_amount, IVGSale.category == "herbicidas") if _table_exists(IVGSale) else 0,
            "concentrados_credito": _safe_sum(IVGSale, IVGSale.gross_amount, IVGSale.category == "concentrados") if _table_exists(IVGSale) else 0,
            "latest_opening_amount": float(_to_decimal(latest_cash_summary.opening_amount)) if latest_cash_summary else 0,
            "latest_expected_close_amount": float(_to_decimal(latest_cash_summary.expected_close_amount)) if latest_cash_summary else 0,
            "latest_actual_close_amount": float(_to_decimal(latest_cash_summary.actual_close_amount)) if latest_cash_summary else 0,
            "latest_variance_amount": float(abs(latest_variance)) if latest_cash_summary else 0,
            "latest_variance_signed": float(latest_variance) if latest_cash_summary else 0,
        },
        "pending_invoices": pending_invoices,
        "recent_collections": recent_collections,
        "recent_cash_summaries": recent_cash_summaries,
        "latest_cash_summary": latest_cash_summary,
        "top_clients": top_clients,
        "ivg_tables": [
            "ivg_usuarios",
            "ivg_clientes",
            "ivg_ventas",
            "ivg_pagos",
            "ivg_contado",
            "ivg_agenda",
        ],
    }


def _render_section(title: str, eyebrow: str, description: str, cta: str):
    context = _base_context()
    context.update({
        "section_title": title,
        "section_eyebrow": eyebrow,
        "section_description": description,
        "section_cta": cta,
    })
    return render_template("igh/section.html", **context)


def _get_ivg_user_or_404(user_id: int) -> IVGUser:
    if not _table_exists(IVGUser):
        abort(404)
    user = db.session.get(IVGUser, user_id)
    if user is None:
        abort(404)
    return user


def _get_ivg_client_or_404(client_id: int) -> IVGClient:
    if not _table_exists(IVGClient):
        abort(404)
    client = db.session.get(IVGClient, client_id)
    if client is None:
        abort(404)
    return client


def _get_ivg_sale_or_404(sale_id: int) -> IVGSale:
    if not _table_exists(IVGSale):
        abort(404)
    sale = db.session.get(IVGSale, sale_id)
    if sale is None:
        abort(404)
    return sale


def _get_ivg_cash_or_404(summary_id: int) -> IVGCashSummary:
    if not _table_exists(IVGCashSummary):
        abort(404)
    summary = db.session.get(IVGCashSummary, summary_id)
    if summary is None:
        abort(404)
    return summary


def _get_ivg_payment_or_404(payment_id: int) -> IVGPayment:
    if not _table_exists(IVGPayment):
        abort(404)
    payment = db.session.get(IVGPayment, payment_id)
    if payment is None:
        abort(404)
    return payment


def _get_ivg_agenda_or_404(item_id: int) -> IVGAgendaItem:
    if not _table_exists(IVGAgendaItem):
        abort(404)
    item = db.session.get(IVGAgendaItem, item_id)
    if item is None:
        abort(404)
    return item


@igh_bp.route("/")
@igh_login_required
def dashboard():
    context = _base_context()
    context["page_heading"] = "Dashboard"
    return render_template("igh/dashboard.html", **context)


@igh_bp.route("/dashboard")
@igh_login_required
def dashboard_alias():
    return redirect(url_for("igh.dashboard"))


@igh_bp.route("/usuarios")
@igh_login_required
def usuarios():
    users = IVGUser.query.order_by(IVGUser.created_at.asc()).all() if _table_exists(IVGUser) else []
    context = _base_context()
    context["users"] = users
    return render_template("igh/users_list.html", **context)


@igh_bp.route("/usuarios/new", methods=["GET", "POST"])
@igh_login_required
@igh_superadmin_required
def usuarios_new():
    context = _base_context()
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip().lower() or None
        password = request.form.get("password") or ""
        role = request.form.get("role") or "cajero"

        if not username or not password:
            flash("Usuario y contraseña son obligatorios.", "danger")
            return redirect(url_for("igh.usuarios_new"))

        if len(password) < 4:
            flash("La contraseña debe tener al menos 4 caracteres.", "danger")
            return redirect(url_for("igh.usuarios_new"))

        if _table_exists(IVGUser):
            exists = IVGUser.query.filter_by(username=username).first()
            if exists:
                flash("Ya existe un usuario IVG con ese nombre.", "danger")
                return redirect(url_for("igh.usuarios_new"))

        user = IVGUser(
            username=username,
            full_name=full_name or None,
            email=email,
            role=role,
            is_active=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash(f"Usuario IVG {username} creado.", "success")
        return redirect(url_for("igh.usuarios"))

    context["user"] = None
    return render_template("igh/users_form.html", **context)


@igh_bp.route("/usuarios/<int:user_id>/edit", methods=["GET", "POST"])
@igh_login_required
@igh_superadmin_required
def usuarios_edit(user_id: int):
    user = _get_ivg_user_or_404(user_id)
    context = _base_context()

    if request.method == "POST":
        user.full_name = (request.form.get("full_name") or "").strip() or None
        user.email = (request.form.get("email") or "").strip().lower() or None
        user.role = request.form.get("role") or user.role
        user.is_active = bool(request.form.get("is_active"))

        new_password = request.form.get("password") or ""
        if new_password:
            if len(new_password) < 4:
                flash("La contraseña debe tener al menos 4 caracteres.", "danger")
                return redirect(url_for("igh.usuarios_edit", user_id=user.id))
            user.set_password(new_password)

        db.session.commit()
        flash(f"Usuario IVG {user.username} actualizado.", "success")
        return redirect(url_for("igh.usuarios"))

    context["user"] = user
    return render_template("igh/users_form.html", **context)


@igh_bp.route("/clientes")
@igh_login_required
def clientes():
    clients = IVGClient.query.order_by(IVGClient.created_at.desc()).all() if _table_exists(IVGClient) else []
    context = _base_context()
    context["clients"] = clients
    return render_template("igh/clients_list.html", **context)


@igh_bp.route("/clientes/<int:client_id>")
@igh_login_required
def clientes_detail(client_id: int):
    client = _get_ivg_client_or_404(client_id)
    sales = (
        IVGSale.query.filter_by(client_id=client.id)
        .order_by(IVGSale.sale_date.desc(), IVGSale.id.desc())
        .all()
        if _table_exists(IVGSale)
        else []
    )
    payments = (
        IVGPayment.query.join(IVGSale, IVGPayment.sale_id == IVGSale.id)
        .filter(IVGSale.client_id == client.id)
        .order_by(IVGPayment.payment_date.desc(), IVGPayment.id.desc())
        .all()
        if _table_exists(IVGPayment) and _table_exists(IVGSale)
        else []
    )
    pending_sales = [sale for sale in sales if Decimal(sale.balance_due or 0) > 0]
    total_sales = sum(Decimal(sale.gross_amount or 0) for sale in sales)
    total_collections = sum(Decimal(payment.amount or 0) for payment in payments)
    pending_balance = sum(Decimal(sale.balance_due or 0) for sale in pending_sales)

    context = _base_context()
    context.update({
        "client": client,
        "client_sales": sales,
        "client_payments": payments,
        "client_pending_sales": pending_sales,
        "client_metrics": {
            "sales_total": total_sales,
            "collections_total": total_collections,
            "pending_balance": pending_balance,
        },
    })
    return render_template("igh/client_detail.html", **context)


@igh_bp.route("/clientes/new", methods=["GET", "POST"])
@igh_login_required
def clientes_new():
    context = _base_context()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        legal_name = (request.form.get("legal_name") or "").strip() or None
        tax_id = (request.form.get("tax_id") or "").strip() or None
        email = (request.form.get("email") or "").strip().lower() or None
        phone = (request.form.get("phone") or "").strip() or None
        city = (request.form.get("city") or "").strip() or None
        status = (request.form.get("status") or "prospecto").strip()
        is_active = bool(request.form.get("is_active"))

        if not name:
            flash("El nombre del cliente es obligatorio.", "danger")
            return redirect(url_for("igh.clientes_new"))

        client = IVGClient(
            name=name,
            legal_name=legal_name,
            tax_id=tax_id,
            email=email,
            phone=phone,
            city=city,
            status=status,
            is_active=is_active,
        )
        db.session.add(client)
        db.session.commit()
        flash(f"Cliente IVG {name} creado.", "success")
        return redirect(url_for("igh.clientes"))

    context["client"] = None
    return render_template("igh/clients_form.html", **context)


@igh_bp.route("/clientes/<int:client_id>/edit", methods=["GET", "POST"])
@igh_login_required
def clientes_edit(client_id: int):
    client = _get_ivg_client_or_404(client_id)
    context = _base_context()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("El nombre del cliente es obligatorio.", "danger")
            return redirect(url_for("igh.clientes_edit", client_id=client.id))

        client.name = name
        client.legal_name = (request.form.get("legal_name") or "").strip() or None
        client.tax_id = (request.form.get("tax_id") or "").strip() or None
        client.email = (request.form.get("email") or "").strip().lower() or None
        client.phone = (request.form.get("phone") or "").strip() or None
        client.city = (request.form.get("city") or "").strip() or None
        client.status = (request.form.get("status") or "prospecto").strip()
        client.is_active = bool(request.form.get("is_active"))

        db.session.commit()
        flash(f"Cliente IVG {client.name} actualizado.", "success")
        return redirect(url_for("igh.clientes"))

    context["client"] = client
    return render_template("igh/clients_form.html", **context)


@igh_bp.route("/ventas")
@igh_login_required
def ventas():
    sales = IVGSale.query.order_by(IVGSale.sale_date.desc(), IVGSale.id.desc()).all() if _table_exists(IVGSale) else []
    context = _base_context()
    context["sales"] = sales
    return render_template("igh/sales_list.html", **context)


@igh_bp.route("/ventas/new", methods=["GET", "POST"])
@igh_login_required
def ventas_new():
    context = _base_context()
    context["clients"] = IVGClient.query.filter_by(is_active=True).order_by(IVGClient.name.asc()).all() if _table_exists(IVGClient) else []

    if request.method == "POST":
        try:
            client_id = int(request.form.get("client_id") or "0")
        except ValueError:
            client_id = 0
        category = (request.form.get("category") or "herbicidas").strip()
        reference_number = (request.form.get("reference_number") or "").strip() or None
        notes = (request.form.get("notes") or "").strip() or None

        try:
            gross_amount = _parse_decimal(request.form.get("gross_amount"), "El monto bruto")
            sale_date = _parse_datetime(request.form.get("sale_date"), "La fecha de venta", required=True)
            due_date = _parse_datetime(request.form.get("due_date"), "La fecha de vencimiento", required=True)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("igh.ventas_new"))

        client = db.session.get(IVGClient, client_id) if client_id else None
        if client is None:
            flash("Debes seleccionar un cliente válido.", "danger")
            return redirect(url_for("igh.ventas_new"))

        sale = IVGSale(
            client_id=client.id,
            sale_type="credito",
            category=category,
            gross_amount=gross_amount,
            balance_due=gross_amount,
            sale_date=sale_date,
            due_date=due_date,
            reference_number=reference_number,
            notes=notes,
            status="registrada",
        )
        _recalculate_sale_balance(sale)
        db.session.add(sale)
        db.session.commit()
        flash("Factura crédito IVG registrada.", "success")
        return redirect(url_for("igh.ventas"))

    context["sale"] = None
    return render_template("igh/sales_form.html", **context)


@igh_bp.route("/ventas/<int:sale_id>/edit", methods=["GET", "POST"])
@igh_login_required
def ventas_edit(sale_id: int):
    sale = _get_ivg_sale_or_404(sale_id)
    context = _base_context()
    context["clients"] = IVGClient.query.filter_by(is_active=True).order_by(IVGClient.name.asc()).all() if _table_exists(IVGClient) else []

    if request.method == "POST":
        try:
            client_id = int(request.form.get("client_id") or "0")
        except ValueError:
            client_id = 0
        category = (request.form.get("category") or sale.category).strip()
        reference_number = (request.form.get("reference_number") or "").strip() or None
        notes = (request.form.get("notes") or "").strip() or None

        try:
            gross_amount = _parse_decimal(request.form.get("gross_amount"), "El monto bruto")
            sale_date = _parse_datetime(request.form.get("sale_date"), "La fecha de venta", required=True)
            due_date = _parse_datetime(request.form.get("due_date"), "La fecha de vencimiento", required=True)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("igh.ventas_edit", sale_id=sale.id))

        client = db.session.get(IVGClient, client_id) if client_id else None
        if client is None:
            flash("Debes seleccionar un cliente válido.", "danger")
            return redirect(url_for("igh.ventas_edit", sale_id=sale.id))

        sale.client_id = client.id
        sale.sale_type = "credito"
        sale.category = category
        sale.gross_amount = gross_amount
        sale.sale_date = sale_date
        sale.due_date = due_date
        sale.reference_number = reference_number
        sale.notes = notes
        _recalculate_sale_balance(sale)
        db.session.commit()
        flash("Factura crédito IVG actualizada.", "success")
        return redirect(url_for("igh.ventas"))

    context["sale"] = sale
    return render_template("igh/sales_form.html", **context)


@igh_bp.route("/contado")
@igh_login_required
def contado():
    summaries = IVGCashSummary.query.order_by(IVGCashSummary.summary_date.desc(), IVGCashSummary.id.desc()).all() if _table_exists(IVGCashSummary) else []
    context = _base_context()
    context["summaries"] = summaries
    return render_template("igh/cash_list.html", **context)


@igh_bp.route("/contado/new", methods=["GET", "POST"])
@igh_login_required
def contado_new():
    context = _base_context()
    previous_summary = _latest_cash_summary()
    context["default_opening_amount"] = previous_summary.actual_close_amount if previous_summary else Decimal("0.00")
    context["previous_summary"] = previous_summary

    if request.method == "POST":
        notes = (request.form.get("notes") or "").strip() or None
        try:
            summary_date = _parse_datetime(request.form.get("summary_date"), "La fecha del contado", required=True)
            opening_amount = _parse_decimal(request.form.get("opening_amount"), "La apertura")
            cash_amount = _parse_decimal(request.form.get("cash_amount"), "El monto en efectivo")
            transfer_amount = _parse_decimal(request.form.get("transfer_amount"), "El monto en transferencia")
            actual_close_amount = _parse_decimal(request.form.get("actual_close_amount"), "El cierre real")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("igh.contado_new"))

        total_amount = cash_amount + transfer_amount
        if total_amount <= 0:
            flash("Debes registrar al menos un monto mayor que cero en contado.", "danger")
            return redirect(url_for("igh.contado_new"))
        expected_close_amount = opening_amount + cash_amount
        variance_amount = actual_close_amount - expected_close_amount

        summary = IVGCashSummary(
            summary_date=summary_date,
            opening_amount=opening_amount,
            cash_amount=cash_amount,
            transfer_amount=transfer_amount,
            total_amount=total_amount,
            expected_close_amount=expected_close_amount,
            actual_close_amount=actual_close_amount,
            variance_amount=variance_amount,
            notes=notes,
        )
        db.session.add(summary)
        db.session.commit()
        flash("Resumen de contado registrado.", "success")
        return redirect(url_for("igh.contado"))

    context["summary"] = None
    return render_template("igh/cash_form.html", **context)


@igh_bp.route("/contado/<int:summary_id>/edit", methods=["GET", "POST"])
@igh_login_required
def contado_edit(summary_id: int):
    summary = _get_ivg_cash_or_404(summary_id)
    context = _base_context()

    if request.method == "POST":
        notes = (request.form.get("notes") or "").strip() or None
        try:
            summary_date = _parse_datetime(request.form.get("summary_date"), "La fecha del contado", required=True)
            opening_amount = _parse_decimal(request.form.get("opening_amount"), "La apertura")
            cash_amount = _parse_decimal(request.form.get("cash_amount"), "El monto en efectivo")
            transfer_amount = _parse_decimal(request.form.get("transfer_amount"), "El monto en transferencia")
            actual_close_amount = _parse_decimal(request.form.get("actual_close_amount"), "El cierre real")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("igh.contado_edit", summary_id=summary.id))

        total_amount = cash_amount + transfer_amount
        if total_amount <= 0:
            flash("Debes registrar al menos un monto mayor que cero en contado.", "danger")
            return redirect(url_for("igh.contado_edit", summary_id=summary.id))
        expected_close_amount = opening_amount + cash_amount
        variance_amount = actual_close_amount - expected_close_amount

        summary.summary_date = summary_date
        summary.opening_amount = opening_amount
        summary.cash_amount = cash_amount
        summary.transfer_amount = transfer_amount
        summary.total_amount = total_amount
        summary.expected_close_amount = expected_close_amount
        summary.actual_close_amount = actual_close_amount
        summary.variance_amount = variance_amount
        summary.notes = notes
        db.session.commit()
        flash("Resumen de contado actualizado.", "success")
        return redirect(url_for("igh.contado"))

    context["summary"] = summary
    return render_template("igh/cash_form.html", **context)


@igh_bp.route("/pagos")
@igh_login_required
def pagos():
    payments = IVGPayment.query.order_by(IVGPayment.payment_date.desc(), IVGPayment.id.desc()).all() if _table_exists(IVGPayment) else []
    context = _base_context()
    context["payments"] = payments
    return render_template("igh/payments_list.html", **context)


@igh_bp.route("/pagos/new", methods=["GET", "POST"])
@igh_login_required
def pagos_new():
    context = _base_context()
    context["credit_sales"] = IVGSale.query.filter(IVGSale.balance_due > 0).order_by(IVGSale.sale_date.desc()).all() if _table_exists(IVGSale) else []

    if request.method == "POST":
        try:
            sale_id = int(request.form.get("sale_id") or "0")
        except ValueError:
            sale_id = 0
        payment_kind = (request.form.get("payment_kind") or "abono").strip()
        payment_method = (request.form.get("payment_method") or "").strip()
        category = (request.form.get("category") or "herbicidas").strip()
        notes = (request.form.get("notes") or "").strip() or None

        try:
            amount = _parse_decimal(request.form.get("amount"), "El monto")
            payment_date = _parse_datetime(request.form.get("payment_date"), "La fecha de pago", required=True)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("igh.pagos_new"))

        sale = db.session.get(IVGSale, sale_id) if sale_id else None
        if sale is None:
            flash("Debes seleccionar una factura de crédito válida.", "danger")
            return redirect(url_for("igh.pagos_new"))
        if amount <= 0:
            flash("El monto debe ser mayor que cero.", "danger")
            return redirect(url_for("igh.pagos_new"))
        if Decimal(sale.balance_due or 0) <= 0:
            flash("La factura seleccionada ya no tiene saldo pendiente.", "warning")
            return redirect(url_for("igh.pagos_new"))
        if amount > Decimal(sale.balance_due or 0):
            flash("El monto supera el saldo pendiente de la factura.", "danger")
            return redirect(url_for("igh.pagos_new"))

        payment = IVGPayment(
            sale_id=sale.id,
            payment_kind=payment_kind,
            payment_method=payment_method,
            category=category or sale.category,
            amount=amount,
            payment_date=payment_date,
            notes=notes,
        )
        db.session.add(payment)
        db.session.flush()
        _recalculate_sale_balance(sale)
        db.session.commit()
        flash("Pago / abono registrado.", "success")
        return redirect(url_for("igh.pagos"))

    context["payment"] = None
    return render_template("igh/payments_form.html", **context)


@igh_bp.route("/pagos/<int:payment_id>/edit", methods=["GET", "POST"])
@igh_login_required
def pagos_edit(payment_id: int):
    payment = _get_ivg_payment_or_404(payment_id)
    old_sale = payment.sale
    context = _base_context()
    context["credit_sales"] = IVGSale.query.order_by(IVGSale.sale_date.desc()).all() if _table_exists(IVGSale) else []

    if request.method == "POST":
        try:
            sale_id = int(request.form.get("sale_id") or "0")
        except ValueError:
            sale_id = 0
        payment_kind = (request.form.get("payment_kind") or payment.payment_kind).strip()
        payment_method = (request.form.get("payment_method") or payment.payment_method).strip()
        category = (request.form.get("category") or payment.category).strip()
        notes = (request.form.get("notes") or "").strip() or None

        try:
            amount = _parse_decimal(request.form.get("amount"), "El monto")
            payment_date = _parse_datetime(request.form.get("payment_date"), "La fecha de pago", required=True)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("igh.pagos_edit", payment_id=payment.id))

        sale = db.session.get(IVGSale, sale_id) if sale_id else None
        if sale is None:
            flash("Debes seleccionar una factura de crédito válida.", "danger")
            return redirect(url_for("igh.pagos_edit", payment_id=payment.id))
        if amount <= 0:
            flash("El monto debe ser mayor que cero.", "danger")
            return redirect(url_for("igh.pagos_edit", payment_id=payment.id))

        payment.sale_id = sale.id
        payment.payment_kind = payment_kind
        payment.payment_method = payment_method
        payment.category = category or sale.category
        payment.amount = amount
        payment.payment_date = payment_date
        payment.notes = notes
        db.session.flush()
        _recalculate_sale_balance(old_sale)
        if sale.id != old_sale.id:
            _recalculate_sale_balance(sale)
        else:
            _recalculate_sale_balance(payment.sale)
        if Decimal(payment.sale.balance_due or 0) < 0:
            flash("El monto editado deja saldo negativo en la factura.", "danger")
            db.session.rollback()
            return redirect(url_for("igh.pagos_edit", payment_id=payment.id))
        db.session.commit()
        flash("Pago / abono actualizado.", "success")
        return redirect(url_for("igh.pagos"))

    context["payment"] = payment
    return render_template("igh/payments_form.html", **context)


@igh_bp.route("/agenda")
@igh_login_required
def agenda():
    items = IVGAgendaItem.query.order_by(IVGAgendaItem.scheduled_for.asc(), IVGAgendaItem.id.desc()).all() if _table_exists(IVGAgendaItem) else []
    context = _base_context()
    context["agenda_items"] = items
    return render_template("igh/agenda_list.html", **context)


@igh_bp.route("/agenda/new", methods=["GET", "POST"])
@igh_login_required
def agenda_new():
    context = _base_context()
    context["clients"] = IVGClient.query.filter_by(is_active=True).order_by(IVGClient.name.asc()).all() if _table_exists(IVGClient) else []

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        activity_type = (request.form.get("activity_type") or "seguimiento").strip()
        status = (request.form.get("status") or "pendiente").strip()
        priority = (request.form.get("priority") or "media").strip()
        notes = (request.form.get("notes") or "").strip() or None
        try:
            client_id = int(request.form.get("client_id") or "0")
        except ValueError:
            client_id = 0

        try:
            scheduled_for = _parse_datetime(request.form.get("scheduled_for"), "La fecha programada", required=True)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("igh.agenda_new"))

        if not title:
            flash("El título de la actividad es obligatorio.", "danger")
            return redirect(url_for("igh.agenda_new"))

        item = IVGAgendaItem(
            client_id=client_id or None,
            title=title,
            activity_type=activity_type,
            status=status,
            priority=priority,
            scheduled_for=scheduled_for,
            notes=notes,
        )
        db.session.add(item)
        db.session.commit()
        flash("Actividad de agenda creada.", "success")
        return redirect(url_for("igh.agenda"))

    context["item"] = None
    return render_template("igh/agenda_form.html", **context)


@igh_bp.route("/agenda/<int:item_id>/edit", methods=["GET", "POST"])
@igh_login_required
def agenda_edit(item_id: int):
    item = _get_ivg_agenda_or_404(item_id)
    context = _base_context()
    context["clients"] = IVGClient.query.filter_by(is_active=True).order_by(IVGClient.name.asc()).all() if _table_exists(IVGClient) else []

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        activity_type = (request.form.get("activity_type") or item.activity_type).strip()
        status = (request.form.get("status") or item.status).strip()
        priority = (request.form.get("priority") or item.priority).strip()
        notes = (request.form.get("notes") or "").strip() or None
        try:
            client_id = int(request.form.get("client_id") or "0")
        except ValueError:
            client_id = 0

        try:
            scheduled_for = _parse_datetime(request.form.get("scheduled_for"), "La fecha programada", required=True)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("igh.agenda_edit", item_id=item.id))

        if not title:
            flash("El título de la actividad es obligatorio.", "danger")
            return redirect(url_for("igh.agenda_edit", item_id=item.id))

        item.client_id = client_id or None
        item.title = title
        item.activity_type = activity_type
        item.status = status
        item.priority = priority
        item.scheduled_for = scheduled_for
        item.notes = notes
        db.session.commit()
        flash("Actividad de agenda actualizada.", "success")
        return redirect(url_for("igh.agenda"))

    context["item"] = item
    return render_template("igh/agenda_form.html", **context)


@igh_bp.route("/reportes")
@igh_login_required
def reportes():
    context = _base_context()
    context["recent_sales"] = IVGSale.query.order_by(IVGSale.sale_date.desc(), IVGSale.id.desc()).limit(8).all() if _table_exists(IVGSale) else []
    context["recent_payments"] = IVGPayment.query.order_by(IVGPayment.payment_date.desc(), IVGPayment.id.desc()).limit(8).all() if _table_exists(IVGPayment) else []
    context["pending_agenda"] = IVGAgendaItem.query.filter(IVGAgendaItem.status != "completada").order_by(IVGAgendaItem.scheduled_for.asc()).limit(6).all() if _table_exists(IVGAgendaItem) else []
    return render_template("igh/reports.html", **context)


@igh_bp.route("/productos")
@igh_login_required
def productos_alias():
    return redirect(url_for("igh.ventas"))


@igh_bp.route("/cobros")
@igh_login_required
def cobros_alias():
    return redirect(url_for("igh.pagos"))


@igh_bp.route("/logout")
def logout():
    session.pop("igh_user_id", None)
    session.pop("igh_user", None)
    session.pop("igh_role", None)
    session.pop("tenant_id", None)
    flash("Sesión de Inversiones Guevara Herrera cerrada.", "info")
    return redirect(url_for("auth.login"))
