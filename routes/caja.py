"""
Caja diaria: aperturas, gastos y cierres.

Reglas clave:
- Los gastos de caja del día se vinculan automáticamente al cierre del día
  (closure_id) cuando éste existe. Si se registra un gasto después del
  cierre, ese cierre se recalcula para reflejar el nuevo total.
- Editar un cierre ya guardado requiere el permiso `cash.edit_closure`
  (admin/owner/contador). El cajero solo puede crear el cierre del día.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from models import db
from models.cash import CashClosure, CashExpense, CashWithdrawal
from models.invoice import Invoice, InvoicePayment
from models.locations import Branch, Warehouse
from services.datetime_utils import local_date_range_to_utc, tenant_today, to_local_datetime, to_utc_datetime
from services.permissions import permission_required, tenant_required
from services.tenant_context import current_tenant

caja_bp = Blueprint("caja", __name__, url_prefix="/app/caja")

VALID_INVOICE_STATUSES = ["issued", "paid", "partially_paid", "overdue"]


@caja_bp.route("/")
@login_required
@tenant_required
@permission_required("cash.view")
def index():
    tenant = current_tenant()
    today = tenant_today(tenant)
    # Default: solo el día de hoy. El cajero ve su caja del día limpia.
    start_date = _parse_date(request.args.get("desde")) or today
    end_date = _parse_date(request.args.get("hasta")) or today
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    period_start, period_end = _period_bounds(start_date, end_date)
    closures = (
        CashClosure.query
        .filter(
            CashClosure.tenant_id == tenant.id,
            CashClosure.closure_date >= start_date,
            CashClosure.closure_date <= end_date,
            CashClosure.status == "closed",
        )
        .order_by(CashClosure.closure_date.desc(), CashClosure.id.desc())
        .all()
    )
    # Garantizar que los gastos del día estén vinculados y los totales sean correctos
    for c in closures:
        _reconcile_closure(c)
    if closures:
        db.session.commit()

    expenses = (
        CashExpense.query
        .filter(
            CashExpense.tenant_id == tenant.id,
            CashExpense.expense_date >= period_start,
            CashExpense.expense_date < period_end,
        )
        .order_by(CashExpense.expense_date.desc(), CashExpense.id.desc())
        .limit(30)
        .all()
    )
    withdrawals = (
        CashWithdrawal.query
        .filter(
            CashWithdrawal.tenant_id == tenant.id,
            CashWithdrawal.withdrawal_date >= period_start,
            CashWithdrawal.withdrawal_date < period_end,
        )
        .order_by(CashWithdrawal.withdrawal_date.desc(), CashWithdrawal.id.desc())
        .limit(30)
        .all()
    )
    # Estado de la caja del día actual
    today_closure = (
        CashClosure.query
        .filter(CashClosure.tenant_id == tenant.id, CashClosure.closure_date == today)
        .first()
    )
    today_summary = _suggested_closure_values(tenant.id, today)
    is_single_day = (start_date == end_date)
    summary = _cash_summary(
        tenant.id,
        period_start,
        period_end,
        closures,
        open_closure=today_closure if start_date <= today <= end_date else None,
        open_closure_values=today_summary,
    )

    # Gastos en efectivo de HOY (para listarlos detalladamente en el banner del día)
    today_start, today_end = _period_bounds(today, today)
    today_expenses = (
        CashExpense.query
        .filter(
            CashExpense.tenant_id == tenant.id,
            CashExpense.payment_method == "efectivo",
            CashExpense.expense_date >= today_start,
            CashExpense.expense_date < today_end,
        )
        .order_by(CashExpense.expense_date.desc(), CashExpense.id.desc())
        .all()
    )
    today_withdrawals = (
        CashWithdrawal.query
        .filter(
            CashWithdrawal.tenant_id == tenant.id,
            CashWithdrawal.withdrawal_date >= today_start,
            CashWithdrawal.withdrawal_date < today_end,
        )
        .order_by(CashWithdrawal.withdrawal_date.desc(), CashWithdrawal.id.desc())
        .all()
    )

    # Atajos de fecha para los botones rápidos del filtro
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=today.weekday())  # lunes
    month_start = today.replace(day=1)

    return render_template(
        "caja/index.html",
        tenant=tenant,
        closures=closures,
        expenses=expenses,
        withdrawals=withdrawals,
        branches=_active_branches(tenant.id),
        summary=summary,
        desde=start_date.isoformat(),
        hasta=end_date.isoformat(),
        today=today.isoformat(),
        today_closure=today_closure,
        today_summary=today_summary,
        is_single_day=is_single_day,
        is_today_view=(start_date == today and end_date == today),
        warehouses=_active_warehouses(tenant.id),
        yesterday_iso=yesterday.isoformat(),
        week_start_iso=week_start.isoformat(),
        month_start_iso=month_start.isoformat(),
        today_expenses=today_expenses,
        today_withdrawals=today_withdrawals,
    )


@caja_bp.route("/nuevo", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("cash.manage")
def new_closure():
    tenant = current_tenant()
    closure_date = _parse_date(request.values.get("fecha")) or tenant_today(tenant)

    existing = (
        CashClosure.query
        .filter(CashClosure.tenant_id == tenant.id, CashClosure.closure_date == closure_date)
        .first()
    )
    if existing is not None and existing.status == "closed":
        if current_user.has_permission("cash.edit_closure"):
            flash(f"Ya existe un cierre para {closure_date.strftime('%d/%m/%Y')}. Lo abrimos para editarlo.", "info")
            return redirect(url_for("caja.edit_closure", closure_id=existing.id))
        flash(f"Ya existe un cierre para {closure_date.strftime('%d/%m/%Y')}. Solo un administrador puede editarlo.", "warning")
        return redirect(url_for("caja.index", desde=closure_date.isoformat(), hasta=closure_date.isoformat()))

    suggested = _suggested_closure_values(tenant.id, closure_date)
    branches = _active_branches(tenant.id)
    warehouses = _active_warehouses(tenant.id)

    if request.method == "POST":
        closure = existing or CashClosure(tenant_id=tenant.id, user_id=current_user.id)
        closure.user_id = current_user.id
        _fill_closure_from_form(closure, suggested)
        if existing is None:
            db.session.add(closure)
        db.session.flush()  # necesitamos closure.id antes de vincular gastos
        _link_day_expenses_to_closure(closure)
        _link_day_withdrawals_to_closure(closure)
        _reconcile_closure(closure)
        db.session.commit()
        flash("Cierre de caja registrado.", "success")
        return redirect(url_for("caja.index", desde=closure.closure_date.isoformat(), hasta=closure.closure_date.isoformat()))

    return render_template(
        "caja/form.html",
        tenant=tenant,
        closure=existing,
        closing_existing_open=existing is not None,
        suggested=suggested,
        branches=branches,
        warehouses=warehouses,
        fecha=closure_date.isoformat(),
    )


@caja_bp.route("/apertura", methods=["POST"])
@login_required
@tenant_required
@permission_required("cash.manage")
def open_day():
    tenant = current_tenant()
    today = tenant_today(tenant)
    closure_date = _parse_date(request.form.get("closure_date")) or today
    existing = (
        CashClosure.query
        .filter(CashClosure.tenant_id == tenant.id, CashClosure.closure_date == closure_date)
        .first()
    )
    if existing is not None and existing.status == "closed":
        flash("La caja de ese día ya está cerrada. Solo puedes editarla desde el cierre registrado.", "warning")
        return redirect(url_for("caja.index", desde=closure_date.isoformat(), hasta=closure_date.isoformat()))

    suggested = _suggested_closure_values(tenant.id, closure_date)
    closure = existing or CashClosure(
        tenant_id=tenant.id,
        user_id=current_user.id,
        closure_date=closure_date,
        status="open",
    )
    closure.user_id = current_user.id
    closure.status = "open"
    closure.branch_id = request.form.get("branch_id", type=int) or None
    closure.warehouse_id = request.form.get("warehouse_id", type=int) or None
    closure.opening_amount = _decimal(request.form.get("opening_amount"))
    _apply_suggested_amounts(closure, suggested)
    if existing is None:
        db.session.add(closure)
        db.session.flush()
    _link_day_expenses_to_closure(closure)
    _link_day_withdrawals_to_closure(closure)
    _reconcile_closure(closure)
    db.session.commit()
    flash("Apertura de caja registrada.", "success")
    return redirect(url_for("caja.index", desde=closure_date.isoformat(), hasta=closure_date.isoformat()))


@caja_bp.route("/<int:closure_id>/editar", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("cash.edit_closure")
def edit_closure(closure_id):
    tenant = current_tenant()
    closure = CashClosure.query.filter_by(id=closure_id, tenant_id=tenant.id).first_or_404()
    suggested = _suggested_closure_values(tenant.id, closure.closure_date)
    branches = _active_branches(tenant.id)
    warehouses = _active_warehouses(tenant.id)

    if request.method == "POST":
        _fill_closure_from_form(closure, suggested)
        _link_day_expenses_to_closure(closure)
        _link_day_withdrawals_to_closure(closure)
        _reconcile_closure(closure)
        db.session.commit()
        flash("Cierre de caja actualizado.", "success")
        return redirect(url_for("caja.index", desde=closure.closure_date.isoformat(), hasta=closure.closure_date.isoformat()))

    return render_template(
        "caja/form.html",
        tenant=tenant,
        closure=closure,
        suggested=suggested,
        branches=branches,
        warehouses=warehouses,
        fecha=closure.closure_date.isoformat(),
    )


@caja_bp.route("/gastos", methods=["POST"])
@login_required
@tenant_required
@permission_required("cash.manage")
def create_expense():
    tenant = current_tenant()
    amount = _decimal(request.form.get("amount"))
    description = (request.form.get("description") or "").strip()
    if amount <= 0 or not description:
        flash("Ingresa una descripción y un monto válido para el gasto.", "warning")
        return redirect(url_for("caja.index"))

    expense_date = _parse_datetime(request.form.get("expense_date")) or datetime.utcnow()
    expense_local_date = _expense_local_date(expense_date)
    expense = CashExpense(
        tenant_id=tenant.id,
        branch_id=request.form.get("branch_id", type=int) or None,
        user_id=current_user.id,
        expense_date=expense_date,
        category=(request.form.get("category") or "General").strip()[:80],
        description=description[:180],
        payment_method=request.form.get("payment_method") or "efectivo",
        amount=amount,
        notes=(request.form.get("notes") or "").strip() or None,
    )
    db.session.add(expense)
    db.session.flush()

    # Si ya existe un cierre para ese día, vincular el gasto y recalcular el cierre
    existing_closure = (
        CashClosure.query
        .filter(
            CashClosure.tenant_id == tenant.id,
            CashClosure.closure_date == expense_local_date,
        )
        .first()
    )
    if existing_closure is not None:
        expense.closure_id = existing_closure.id
        _reconcile_closure(existing_closure)

    db.session.commit()
    flash("Gasto registrado.", "success")
    return redirect(url_for("caja.index", desde=expense_local_date.isoformat(), hasta=expense_local_date.isoformat()))


@caja_bp.route("/retiros", methods=["POST"])
@login_required
@tenant_required
@permission_required("cash.manage")
def create_withdrawal():
    tenant = current_tenant()
    amount = _decimal(request.form.get("amount"))
    recipient = (request.form.get("recipient") or "").strip()
    description = (request.form.get("description") or "").strip()
    if amount <= 0 or not recipient or not description:
        flash("Ingresa destinatario, descripción y monto válido para el retiro.", "warning")
        return redirect(url_for("caja.index"))

    withdrawal_date = _parse_datetime(request.form.get("withdrawal_date")) or datetime.utcnow()
    withdrawal_local_date = _cash_movement_local_date(withdrawal_date)
    withdrawal = CashWithdrawal(
        tenant_id=tenant.id,
        branch_id=request.form.get("branch_id", type=int) or None,
        user_id=current_user.id,
        withdrawal_date=withdrawal_date,
        recipient=recipient[:120],
        description=description[:180],
        amount=amount,
        notes=(request.form.get("notes") or "").strip() or None,
    )
    db.session.add(withdrawal)
    db.session.flush()

    existing_closure = (
        CashClosure.query
        .filter(
            CashClosure.tenant_id == tenant.id,
            CashClosure.closure_date == withdrawal_local_date,
        )
        .first()
    )
    if existing_closure is not None:
        withdrawal.closure_id = existing_closure.id
        _reconcile_closure(existing_closure)

    db.session.commit()
    flash("Retiro parcial registrado.", "success")
    return redirect(url_for("caja.index", desde=withdrawal_local_date.isoformat(), hasta=withdrawal_local_date.isoformat()))


# ============================================================
# Helpers internos
# ============================================================

def _fill_closure_from_form(closure: CashClosure, suggested: dict) -> None:
    """Aplica al cierre los datos del formulario + sugeridos del día."""
    closure.closure_date = _parse_date(request.form.get("closure_date")) or suggested["closure_date"]
    closure.status = "closed"
    closure.branch_id = request.form.get("branch_id", type=int) or None
    closure.warehouse_id = request.form.get("warehouse_id", type=int) or None
    closure.opening_amount = _decimal(request.form.get("opening_amount"))
    _apply_suggested_amounts(closure, suggested)
    closure.actual_cash_amount = _decimal(request.form.get("actual_cash_amount"))
    closure.delivered_cash_amount = _decimal(request.form.get("delivered_cash_amount"))
    closure.notes = (request.form.get("notes") or "").strip() or None


def _apply_suggested_amounts(closure: CashClosure, suggested: dict) -> None:
    """Actualiza ventas y abonos calculados para el día operativo."""
    closure.cash_sales_amount = suggested["cash_sales_amount"]
    closure.transfer_sales_amount = suggested["transfer_sales_amount"]
    closure.card_sales_amount = suggested["card_sales_amount"]
    closure.credit_sales_amount = suggested["credit_sales_amount"]
    closure.receivable_cash_amount = suggested["receivable_cash_amount"]
    closure.receivable_transfer_amount = suggested["receivable_transfer_amount"]
    closure.receivable_card_amount = suggested["receivable_card_amount"]


def _link_day_expenses_to_closure(closure: CashClosure) -> None:
    """Asocia todos los gastos en efectivo del día al cierre."""
    start, end = _period_bounds(closure.closure_date, closure.closure_date)
    (CashExpense.query
        .filter(
            CashExpense.tenant_id == closure.tenant_id,
            CashExpense.payment_method == "efectivo",
            CashExpense.expense_date >= start,
            CashExpense.expense_date < end,
        )
        .update({CashExpense.closure_id: closure.id}, synchronize_session=False)
    )


def _link_day_withdrawals_to_closure(closure: CashClosure) -> None:
    """Asocia todos los retiros parciales del día al cierre."""
    start, end = _period_bounds(closure.closure_date, closure.closure_date)
    (CashWithdrawal.query
        .filter(
            CashWithdrawal.tenant_id == closure.tenant_id,
            CashWithdrawal.withdrawal_date >= start,
            CashWithdrawal.withdrawal_date < end,
        )
        .update({CashWithdrawal.closure_id: closure.id}, synchronize_session=False)
    )


def _reconcile_closure(closure: CashClosure) -> None:
    """
    Recalcula los gastos (en tiempo real), el efectivo esperado y la diferencia
    para que un cierre siempre refleje la realidad del día, aunque se hayan
    registrado gastos después de haberlo creado.

    Fórmulas oficiales:
      efectivo_esperado = apertura + ventas_efectivo + abonos_efectivo - gastos_efectivo - retiros
      diferencia        = dinero_entregado - efectivo_esperado
        · > 0 → sobrante
        · = 0 → cuadrado
        · < 0 → faltante

    Nota: el dinero entregado NO altera las ventas. Solo se usa para comparar
    contra el efectivo esperado y determinar el estado del cierre.
    """
    start, end = _period_bounds(closure.closure_date, closure.closure_date)
    expenses_total = (
        db.session.query(func.coalesce(func.sum(CashExpense.amount), 0))
        .filter(
            CashExpense.tenant_id == closure.tenant_id,
            CashExpense.payment_method == "efectivo",
            CashExpense.expense_date >= start,
            CashExpense.expense_date < end,
        )
        .scalar()
    )
    withdrawals_total = (
        db.session.query(func.coalesce(func.sum(CashWithdrawal.amount), 0))
        .filter(
            CashWithdrawal.tenant_id == closure.tenant_id,
            CashWithdrawal.withdrawal_date >= start,
            CashWithdrawal.withdrawal_date < end,
        )
        .scalar()
    )
    closure.expenses_amount = _decimal(expenses_total)
    closure.withdrawals_amount = _decimal(withdrawals_total)
    closure.expected_cash_amount = (
        _decimal(closure.opening_amount)
        + _decimal(closure.cash_sales_amount)
        + _decimal(closure.receivable_cash_amount)
        - _decimal(closure.expenses_amount)
        - _decimal(closure.withdrawals_amount)
    )
    # Diferencia oficial: entregado - esperado
    closure.variance_amount = (
        _decimal(closure.delivered_cash_amount) - _decimal(closure.expected_cash_amount)
    )


def _suggested_closure_values(tenant_id: int, closure_date):
    start, end = _period_bounds(closure_date, closure_date)
    sales = _sum_invoices_by_method(tenant_id, start, end)
    payments = _sum_payments_by_method(tenant_id, start, end)
    credit_payments = _sum_payments_by_method(tenant_id, start, end, credit_only=True)
    expenses_amount = (
        db.session.query(func.coalesce(func.sum(CashExpense.amount), 0))
        .filter(
            CashExpense.tenant_id == tenant_id,
            CashExpense.payment_method == "efectivo",
            CashExpense.expense_date >= start,
            CashExpense.expense_date < end,
        )
        .scalar()
    )
    withdrawals_amount = (
        db.session.query(func.coalesce(func.sum(CashWithdrawal.amount), 0))
        .filter(
            CashWithdrawal.tenant_id == tenant_id,
            CashWithdrawal.withdrawal_date >= start,
            CashWithdrawal.withdrawal_date < end,
        )
        .scalar()
    )
    cash_sales = _decimal(sales.get("efectivo")) + _decimal(payments.get("efectivo")) - _decimal(credit_payments.get("efectivo"))
    transfer_sales = _decimal(sales.get("transferencia")) + _decimal(payments.get("transferencia")) - _decimal(credit_payments.get("transferencia"))
    card_sales = _decimal(sales.get("tarjeta")) + _decimal(payments.get("tarjeta")) - _decimal(credit_payments.get("tarjeta"))
    check_sales = _decimal(sales.get("cheque")) + _decimal(payments.get("cheque")) - _decimal(credit_payments.get("cheque"))
    return {
        "closure_date": closure_date,
        "cash_sales_amount": cash_sales,
        "transfer_sales_amount": transfer_sales,
        "card_sales_amount": card_sales,
        "credit_sales_amount": _decimal(sales.get("credito")),
        # "Ventas de contado" = todo lo no a crédito cobrado en el momento.
        "contado_sales_amount": cash_sales + transfer_sales + card_sales + check_sales,
        "receivable_cash_amount": _decimal(credit_payments.get("efectivo")),
        "receivable_transfer_amount": _decimal(credit_payments.get("transferencia")),
        "receivable_card_amount": _decimal(credit_payments.get("tarjeta")),
        "expenses_amount": _decimal(expenses_amount),
        "withdrawals_amount": _decimal(withdrawals_amount),
    }


def _cash_summary(
    tenant_id: int,
    period_start,
    period_end,
    closures,
    open_closure: CashClosure | None = None,
    open_closure_values: dict | None = None,
):
    sales = _sum_invoices_by_method(tenant_id, period_start, period_end)
    payments = _sum_payments_by_method(tenant_id, period_start, period_end)
    expenses = (
        db.session.query(
            CashExpense.payment_method.label("method"),
            func.coalesce(func.sum(CashExpense.amount), 0).label("total"),
        )
        .filter(
            CashExpense.tenant_id == tenant_id,
            CashExpense.expense_date >= period_start,
            CashExpense.expense_date < period_end,
        )
        .group_by(CashExpense.payment_method)
        .all()
    )
    expenses_by_method = {row.method: _decimal(row.total) for row in expenses}
    withdrawals_total = (
        db.session.query(func.coalesce(func.sum(CashWithdrawal.amount), 0))
        .filter(
            CashWithdrawal.tenant_id == tenant_id,
            CashWithdrawal.withdrawal_date >= period_start,
            CashWithdrawal.withdrawal_date < period_end,
        )
        .scalar()
    )
    cash_sales = _decimal(sales.get("efectivo"))
    transfer_sales = _decimal(sales.get("transferencia"))
    card_sales = _decimal(sales.get("tarjeta"))
    check_sales = _decimal(sales.get("cheque")) + _decimal(payments.get("cheque"))
    closure_dates = {c.closure_date for c in closures}
    include_open_closure = (
        open_closure is not None
        and open_closure.status == "open"
        and open_closure.closure_date not in closure_dates
    )
    open_values = open_closure_values or {}
    open_opening = _decimal(open_closure.opening_amount) if include_open_closure else Decimal("0.00")
    open_expected_cash = (
        open_opening
        + _decimal(open_values.get("cash_sales_amount"))
        + _decimal(open_values.get("receivable_cash_amount"))
        - _decimal(open_values.get("expenses_amount"))
        - _decimal(open_values.get("withdrawals_amount"))
        if include_open_closure
        else Decimal("0.00")
    )
    return {
        "sales": {key: _decimal(value) for key, value in sales.items()},
        "payments": {key: _decimal(value) for key, value in payments.items()},
        "expenses": expenses_by_method,
        # Total "de contado" = todo lo no a crédito del periodo
        "contado_total": cash_sales + transfer_sales + card_sales + check_sales,
        "opening_total": sum(_decimal(c.opening_amount) for c in closures) + open_opening,
        "expenses_total": sum(_decimal(c.expenses_amount) for c in closures),
        "withdrawals_total": _decimal(withdrawals_total),
        "expected_cash": sum(_decimal(c.expected_cash_amount) for c in closures) + open_expected_cash,
        "actual_cash": sum(_decimal(c.actual_cash_amount) for c in closures),
        "delivered_cash": sum(_decimal(c.delivered_cash_amount) for c in closures),
        "variance": sum(_decimal(c.variance_amount) for c in closures),
    }


def _sum_invoices_by_method(tenant_id: int, period_start, period_end):
    rows = (
        db.session.query(
            Invoice.payment_method.label("method"),
            func.coalesce(func.sum(Invoice.total), 0).label("total"),
        )
        .filter(
            Invoice.tenant_id == tenant_id,
            Invoice.status.in_(VALID_INVOICE_STATUSES),
            Invoice.issue_date >= period_start,
            Invoice.issue_date < period_end,
            or_(Invoice.payment_method == "credito", ~Invoice.payments.any()),
        )
        .group_by(Invoice.payment_method)
        .all()
    )
    return {row.method: row.total for row in rows}


def _sum_payments_by_method(tenant_id: int, period_start, period_end, credit_only: bool = False):
    query = (
        db.session.query(
            InvoicePayment.payment_method.label("method"),
            func.coalesce(func.sum(InvoicePayment.amount), 0).label("total"),
        )
        .join(Invoice, Invoice.id == InvoicePayment.invoice_id)
        .filter(
            InvoicePayment.tenant_id == tenant_id,
            InvoicePayment.paid_at >= period_start,
            InvoicePayment.paid_at < period_end,
        )
    )
    if credit_only:
        query = query.filter(Invoice.payment_method == "credito")
    rows = query.group_by(InvoicePayment.payment_method).all()
    return {row.method: row.total for row in rows}


def _active_branches(tenant_id: int):
    return Branch.query.filter_by(tenant_id=tenant_id, is_active=True).order_by(Branch.name.asc()).all()


def _active_warehouses(tenant_id: int):
    return Warehouse.query.filter_by(tenant_id=tenant_id, is_active=True).order_by(Warehouse.name.asc()).all()


def _period_bounds(start_date, end_date):
    return local_date_range_to_utc(start_date, end_date, current_tenant())


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_datetime(value):
    if not value:
        return None
    try:
        local_value = datetime.strptime(value, "%Y-%m-%dT%H:%M")
        return to_utc_datetime(local_value, current_tenant())
    except ValueError:
        return None


def _expense_local_date(expense_date):
    return to_local_datetime(expense_date, current_tenant()).date()


def _cash_movement_local_date(movement_date):
    return to_local_datetime(movement_date, current_tenant()).date()


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or "0")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")
