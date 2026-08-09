"""
Caja diaria: aperturas, gastos y cierres.

Reglas clave:
- Los gastos y retiros se vinculan mientras la caja está abierta.
- Un cierre guardado es definitivo: no se edita ni se recalcula después.
- El precierre se calcula en el navegador y no persiste información.
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
    today_branch_id = today_closure.branch_id if today_closure else None
    today_summary = _suggested_closure_values(
        tenant.id,
        today,
        branch_id=today_branch_id,
    )
    is_single_day = (start_date == end_date)
    selected_date = start_date if is_single_day else None
    selected_closure = (
        CashClosure.query
        .filter(
            CashClosure.tenant_id == tenant.id,
            CashClosure.closure_date == selected_date,
        )
        .first()
        if selected_date is not None else None
    )
    selected_branch_id = selected_closure.branch_id if selected_closure else None
    if selected_branch_id:
        expenses = [expense for expense in expenses if expense.branch_id == selected_branch_id]
        withdrawals = [withdrawal for withdrawal in withdrawals if withdrawal.branch_id == selected_branch_id]
    selected_summary = (
        _suggested_closure_values(
            tenant.id,
            selected_date,
            branch_id=selected_branch_id,
        )
        if selected_date is not None else None
    )
    summary = _cash_summary(
        tenant.id,
        period_start,
        period_end,
        closures,
        open_closure=today_closure if start_date <= today <= end_date else None,
        branch_id=selected_branch_id if is_single_day else None,
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
    )
    if today_branch_id:
        today_expenses = today_expenses.filter(CashExpense.branch_id == today_branch_id)
    today_expenses = (
        today_expenses
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
    )
    if today_branch_id:
        today_withdrawals = today_withdrawals.filter(CashWithdrawal.branch_id == today_branch_id)
    today_withdrawals = (
        today_withdrawals
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
        selected_date=selected_date,
        selected_closure=selected_closure,
        selected_summary=selected_summary,
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
    today = tenant_today(tenant)
    closure_date = _parse_date(
        request.values.get("closure_date") or request.values.get("fecha")
    ) or today
    if closure_date > today:
        flash("No puedes cerrar una fecha futura.", "warning")
        return redirect(url_for("caja.index"))

    existing = (
        CashClosure.query
        .filter(CashClosure.tenant_id == tenant.id, CashClosure.closure_date == closure_date)
        .first()
    )
    if existing is not None and existing.status == "closed":
        flash(
            f"La caja del {closure_date.strftime('%d/%m/%Y')} ya tiene un cierre definitivo y no puede modificarse.",
            "warning",
        )
        return redirect(url_for("caja.index", desde=closure_date.isoformat(), hasta=closure_date.isoformat()))

    branches = _active_branches(tenant.id)
    requested_branch_id = request.values.get("branch_id", type=int)
    submitted_scope_changed = (
        request.method == "POST"
        and existing is not None
        and requested_branch_id != existing.branch_id
    )
    branch_id = existing.branch_id if existing else requested_branch_id
    branch_id, scope_error = _validated_branch_scope(
        tenant.id, branch_id, require_branch=bool(branches)
    )
    suggested = _suggested_closure_values(
        tenant.id, closure_date, branch_id=branch_id
    )

    if request.method == "POST":
        if submitted_scope_changed:
            flash("La sede cambió. Guarda primero la apertura antes de cerrar la caja.", "warning")
            return redirect(url_for("caja.index", desde=closure_date.isoformat(), hasta=closure_date.isoformat()))
        if scope_error:
            flash(scope_error, "warning")
            return redirect(url_for("caja.index", desde=closure_date.isoformat(), hasta=closure_date.isoformat()))
        if request.form.get("confirm_final") != "1":
            flash("Debes revisar y confirmar el cierre definitivo antes de guardarlo.", "warning")
            return redirect(url_for("caja.index", desde=closure_date.isoformat(), hasta=closure_date.isoformat()))
        actual_raw = (request.form.get("actual_cash_amount") or "").strip()
        delivered_raw = (request.form.get("delivered_cash_amount") or "").strip()
        try:
            actual_amount = Decimal(actual_raw)
            delivered_amount = Decimal(delivered_raw or "0")
        except (InvalidOperation, ValueError):
            actual_amount = Decimal("-1")
            delivered_amount = Decimal("-1")
        if not actual_raw or actual_amount < 0 or delivered_amount < 0:
            flash("Ingresa montos válidos para el efectivo contado y el dinero entregado.", "warning")
            return redirect(url_for("caja.index", desde=closure_date.isoformat(), hasta=closure_date.isoformat()))
        closure = existing or CashClosure(tenant_id=tenant.id, user_id=current_user.id)
        closure.user_id = current_user.id
        _fill_closure_from_form(closure, suggested)
        closure.branch_id = branch_id
        closure.warehouse_id = None
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
        selected_branch_id=branch_id,
        fecha=closure_date.isoformat(),
        today=today.isoformat(),
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
        flash("La caja de ese día tiene un cierre definitivo y no puede modificarse.", "warning")
        return redirect(url_for("caja.index", desde=closure_date.isoformat(), hasta=closure_date.isoformat()))

    branch_id, scope_error = _validated_branch_scope(
        tenant.id,
        request.form.get("branch_id", type=int),
        require_branch=bool(_active_branches(tenant.id)),
    )
    if scope_error:
        flash(scope_error, "warning")
        return redirect(url_for("caja.index", desde=closure_date.isoformat(), hasta=closure_date.isoformat()))
    suggested = _suggested_closure_values(
        tenant.id, closure_date, branch_id=branch_id
    )
    closure = existing or CashClosure(
        tenant_id=tenant.id,
        user_id=current_user.id,
        closure_date=closure_date,
        status="open",
    )
    closure.user_id = current_user.id
    closure.status = "open"
    closure.branch_id = branch_id
    closure.warehouse_id = None
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
    flash(
        "Este cierre es definitivo y no puede modificarse. Registra cualquier corrección como un ajuste posterior.",
        "warning",
    )
    return redirect(
        url_for(
            "caja.index",
            desde=closure.closure_date.isoformat(),
            hasta=closure.closure_date.isoformat(),
        )
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
    existing_closure = (
        CashClosure.query
        .filter(
            CashClosure.tenant_id == tenant.id,
            CashClosure.closure_date == expense_local_date,
        )
        .first()
    )
    if existing_closure is not None and existing_closure.status == "closed":
        flash("No puedes registrar gastos en una caja con cierre definitivo.", "warning")
        return redirect(url_for("caja.index", desde=expense_local_date.isoformat(), hasta=expense_local_date.isoformat()))

    branch_id, scope_error = _validated_branch_scope(
        tenant.id,
        request.form.get("branch_id", type=int),
        require_branch=bool(_active_branches(tenant.id)),
    )
    if scope_error:
        flash(scope_error, "warning")
        return redirect(url_for("caja.index", desde=expense_local_date.isoformat(), hasta=expense_local_date.isoformat()))
    if existing_closure is not None and existing_closure.branch_id != branch_id:
        flash("El gasto debe registrarse en la misma sede de la caja abierta.", "warning")
        return redirect(url_for("caja.index", desde=expense_local_date.isoformat(), hasta=expense_local_date.isoformat()))

    expense = CashExpense(
        tenant_id=tenant.id,
        branch_id=branch_id,
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
    if existing_closure is not None:
        expense.closure_id = existing_closure.id
        _reconcile_closure(existing_closure)

    db.session.commit()
    flash("Gasto registrado.", "success")
    return redirect(url_for("caja.index", desde=expense_local_date.isoformat(), hasta=expense_local_date.isoformat()))


@caja_bp.route("/gastos/<int:expense_id>/eliminar", methods=["POST"])
@login_required
@tenant_required
@permission_required("cash.delete_expense")
def delete_expense(expense_id):
    tenant = current_tenant()
    expense = CashExpense.query.filter_by(id=expense_id, tenant_id=tenant.id).first_or_404()
    expense_local_date = _expense_local_date(expense.expense_date)
    closure_id = expense.closure_id

    closed_closure = CashClosure.query.filter_by(
        tenant_id=tenant.id,
        closure_date=expense_local_date,
        status="closed",
    ).first()
    if closed_closure is not None:
        flash("No puedes eliminar gastos de una caja con cierre definitivo.", "warning")
        return redirect(url_for("caja.index", desde=expense_local_date.isoformat(), hasta=expense_local_date.isoformat()))

    db.session.delete(expense)
    db.session.flush()

    if closure_id:
        closure = CashClosure.query.filter_by(id=closure_id, tenant_id=tenant.id).first()
        if closure is not None:
            _reconcile_closure(closure)

    db.session.commit()
    flash("Gasto eliminado.", "success")
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
    existing_closure = (
        CashClosure.query
        .filter(
            CashClosure.tenant_id == tenant.id,
            CashClosure.closure_date == withdrawal_local_date,
        )
        .first()
    )
    if existing_closure is not None and existing_closure.status == "closed":
        flash("No puedes registrar retiros en una caja con cierre definitivo.", "warning")
        return redirect(url_for("caja.index", desde=withdrawal_local_date.isoformat(), hasta=withdrawal_local_date.isoformat()))

    branch_id, scope_error = _validated_branch_scope(
        tenant.id,
        request.form.get("branch_id", type=int),
        require_branch=bool(_active_branches(tenant.id)),
    )
    if scope_error:
        flash(scope_error, "warning")
        return redirect(url_for("caja.index", desde=withdrawal_local_date.isoformat(), hasta=withdrawal_local_date.isoformat()))
    if existing_closure is not None and existing_closure.branch_id != branch_id:
        flash("El retiro debe registrarse en la misma sede de la caja abierta.", "warning")
        return redirect(url_for("caja.index", desde=withdrawal_local_date.isoformat(), hasta=withdrawal_local_date.isoformat()))

    withdrawal = CashWithdrawal(
        tenant_id=tenant.id,
        branch_id=branch_id,
        user_id=current_user.id,
        withdrawal_date=withdrawal_date,
        recipient=recipient[:120],
        description=description[:180],
        amount=amount,
        notes=(request.form.get("notes") or "").strip() or None,
    )
    db.session.add(withdrawal)
    db.session.flush()

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
    closure.warehouse_id = None
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
    """Asocia los gastos en efectivo del día y sede al cierre."""
    start, end = _period_bounds(closure.closure_date, closure.closure_date)
    CashExpense.query.filter(CashExpense.closure_id == closure.id).update(
        {CashExpense.closure_id: None}, synchronize_session=False
    )
    query = (CashExpense.query
        .filter(
            CashExpense.tenant_id == closure.tenant_id,
            CashExpense.payment_method == "efectivo",
            CashExpense.expense_date >= start,
            CashExpense.expense_date < end,
        )
    )
    if closure.branch_id:
        query = query.filter(CashExpense.branch_id == closure.branch_id)
    query.update({CashExpense.closure_id: closure.id}, synchronize_session=False)


def _link_day_withdrawals_to_closure(closure: CashClosure) -> None:
    """Asocia los retiros parciales del día y sede al cierre."""
    start, end = _period_bounds(closure.closure_date, closure.closure_date)
    CashWithdrawal.query.filter(CashWithdrawal.closure_id == closure.id).update(
        {CashWithdrawal.closure_id: None}, synchronize_session=False
    )
    query = (CashWithdrawal.query
        .filter(
            CashWithdrawal.tenant_id == closure.tenant_id,
            CashWithdrawal.withdrawal_date >= start,
            CashWithdrawal.withdrawal_date < end,
        )
    )
    if closure.branch_id:
        query = query.filter(CashWithdrawal.branch_id == closure.branch_id)
    query.update({CashWithdrawal.closure_id: closure.id}, synchronize_session=False)


def _reconcile_closure(closure: CashClosure) -> None:
    """
    Calcula los importes definitivos al momento de cerrar la caja.

    Fórmulas oficiales:
      efectivo_esperado = apertura + ventas_efectivo + abonos_efectivo - gastos_efectivo - retiros
      diferencia        = efectivo_contado - efectivo_esperado
        · > 0 → sobrante
        · = 0 → cuadrado
        · < 0 → faltante

    El dinero entregado se conserva como dato informativo y permite calcular
    cuánto efectivo permanece físicamente en caja.
    """
    start, end = _period_bounds(closure.closure_date, closure.closure_date)
    expenses_query = (
        db.session.query(func.coalesce(func.sum(CashExpense.amount), 0))
        .filter(
            CashExpense.tenant_id == closure.tenant_id,
            CashExpense.payment_method == "efectivo",
            CashExpense.expense_date >= start,
            CashExpense.expense_date < end,
        )
    )
    if closure.branch_id:
        expenses_query = expenses_query.filter(CashExpense.branch_id == closure.branch_id)
    expenses_total = expenses_query.scalar()
    withdrawals_query = (
        db.session.query(func.coalesce(func.sum(CashWithdrawal.amount), 0))
        .filter(
            CashWithdrawal.tenant_id == closure.tenant_id,
            CashWithdrawal.withdrawal_date >= start,
            CashWithdrawal.withdrawal_date < end,
        )
    )
    if closure.branch_id:
        withdrawals_query = withdrawals_query.filter(CashWithdrawal.branch_id == closure.branch_id)
    withdrawals_total = withdrawals_query.scalar()
    closure.expenses_amount = _decimal(expenses_total)
    closure.withdrawals_amount = _decimal(withdrawals_total)
    closure.expected_cash_amount = (
        _decimal(closure.opening_amount)
        + _decimal(closure.cash_sales_amount)
        + _decimal(closure.receivable_cash_amount)
        - _decimal(closure.expenses_amount)
        - _decimal(closure.withdrawals_amount)
    )
    # Diferencia oficial del arqueo: contado físicamente - esperado.
    closure.variance_amount = (
        _decimal(closure.actual_cash_amount) - _decimal(closure.expected_cash_amount)
    )


def _suggested_closure_values(
    tenant_id: int,
    closure_date,
    branch_id: int | None = None,
):
    start, end = _period_bounds(closure_date, closure_date)
    sales = _sum_invoices_by_method(tenant_id, start, end, branch_id)
    payments = _sum_payments_by_method(tenant_id, start, end, False, branch_id)
    credit_payments = _sum_payments_by_method(tenant_id, start, end, True, branch_id)
    expenses_query = (
        db.session.query(func.coalesce(func.sum(CashExpense.amount), 0))
        .filter(
            CashExpense.tenant_id == tenant_id,
            CashExpense.payment_method == "efectivo",
            CashExpense.expense_date >= start,
            CashExpense.expense_date < end,
        )
    )
    if branch_id:
        expenses_query = expenses_query.filter(CashExpense.branch_id == branch_id)
    expenses_amount = expenses_query.scalar()
    withdrawals_query = (
        db.session.query(func.coalesce(func.sum(CashWithdrawal.amount), 0))
        .filter(
            CashWithdrawal.tenant_id == tenant_id,
            CashWithdrawal.withdrawal_date >= start,
            CashWithdrawal.withdrawal_date < end,
        )
    )
    if branch_id:
        withdrawals_query = withdrawals_query.filter(CashWithdrawal.branch_id == branch_id)
    withdrawals_amount = withdrawals_query.scalar()
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
        "check_sales_amount": check_sales,
        "receivable_cash_amount": _decimal(credit_payments.get("efectivo")),
        "receivable_transfer_amount": _decimal(credit_payments.get("transferencia")),
        "receivable_card_amount": _decimal(credit_payments.get("tarjeta")),
        "receivable_check_amount": _decimal(credit_payments.get("cheque")),
        "expenses_amount": _decimal(expenses_amount),
        "withdrawals_amount": _decimal(withdrawals_amount),
    }


def _cash_summary(
    tenant_id: int,
    period_start,
    period_end,
    closures,
    open_closure: CashClosure | None = None,
    branch_id: int | None = None,
):
    sales = _sum_invoices_by_method(tenant_id, period_start, period_end, branch_id)
    payments = _sum_payments_by_method(tenant_id, period_start, period_end, False, branch_id)
    credit_payments = _sum_payments_by_method(
        tenant_id,
        period_start,
        period_end,
        credit_only=True,
        branch_id=branch_id,
    )
    expenses_query = (
        db.session.query(
            CashExpense.payment_method.label("method"),
            func.coalesce(func.sum(CashExpense.amount), 0).label("total"),
        )
        .filter(
            CashExpense.tenant_id == tenant_id,
            CashExpense.expense_date >= period_start,
            CashExpense.expense_date < period_end,
        )
    )
    if branch_id:
        expenses_query = expenses_query.filter(CashExpense.branch_id == branch_id)
    expenses = expenses_query.group_by(CashExpense.payment_method).all()
    expenses_by_method = {row.method: _decimal(row.total) for row in expenses}
    withdrawals_query = (
        db.session.query(func.coalesce(func.sum(CashWithdrawal.amount), 0))
        .filter(
            CashWithdrawal.tenant_id == tenant_id,
            CashWithdrawal.withdrawal_date >= period_start,
            CashWithdrawal.withdrawal_date < period_end,
        )
    )
    if branch_id:
        withdrawals_query = withdrawals_query.filter(CashWithdrawal.branch_id == branch_id)
    withdrawals_total = withdrawals_query.scalar()
    payment_methods = ("efectivo", "transferencia", "tarjeta", "cheque")
    method_totals = {
        method: _decimal(sales.get(method)) + _decimal(payments.get(method))
        for method in payment_methods
    }
    # Las ventas actuales generan un InvoicePayment al cobrar. Restamos solo los
    # pagos de facturas a crédito para separar venta inmediata de cobro/abono.
    immediate_sales = {
        method: method_totals[method] - _decimal(credit_payments.get(method))
        for method in payment_methods
    }
    closure_dates = {c.closure_date for c in closures}
    include_open_closure = (
        open_closure is not None
        and open_closure.status == "open"
        and open_closure.closure_date not in closure_dates
    )
    open_opening = _decimal(open_closure.opening_amount) if include_open_closure else Decimal("0.00")
    opening_total = sum(_decimal(c.opening_amount) for c in closures) + open_opening
    cash_available = (
        opening_total
        + method_totals["efectivo"]
        - _decimal(expenses_by_method.get("efectivo"))
        - _decimal(withdrawals_total)
    )
    return {
        "sales": {key: _decimal(value) for key, value in sales.items()},
        "payments": {key: _decimal(value) for key, value in payments.items()},
        "credit_payments": {
            key: _decimal(value) for key, value in credit_payments.items()
        },
        "immediate_sales": immediate_sales,
        "method_totals": method_totals,
        "expenses": expenses_by_method,
        # Total recibido en el periodo, incluyendo ventas inmediatas y abonos.
        "contado_total": sum(method_totals.values(), Decimal("0.00")),
        "opening_total": opening_total,
        "expenses_total": sum(_decimal(c.expenses_amount) for c in closures),
        "withdrawals_total": _decimal(withdrawals_total),
        "expected_cash": cash_available,
        "cash_available": cash_available,
        "actual_cash": sum(_decimal(c.actual_cash_amount) for c in closures),
        "delivered_cash": sum(_decimal(c.delivered_cash_amount) for c in closures),
        "variance": sum(_decimal(c.variance_amount) for c in closures),
    }


def _sum_invoices_by_method(
    tenant_id: int,
    period_start,
    period_end,
    branch_id: int | None = None,
):
    query = (
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
    )
    query = _scope_invoice_query(query, branch_id)
    rows = query.group_by(Invoice.payment_method).all()
    return {row.method: row.total for row in rows}


def _sum_payments_by_method(
    tenant_id: int,
    period_start,
    period_end,
    credit_only: bool = False,
    branch_id: int | None = None,
):
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
    query = _scope_invoice_query(query, branch_id)
    rows = query.group_by(InvoicePayment.payment_method).all()
    return {row.method: row.total for row in rows}


def _scope_invoice_query(query, branch_id: int | None):
    """Limita facturas y cobros a la sede operativa de la caja."""
    if branch_id:
        return query.join(Warehouse, Invoice.warehouse_id == Warehouse.id).filter(
            Warehouse.branch_id == branch_id
        )
    return query


def _validated_branch_scope(
    tenant_id: int,
    branch_id: int | None,
    require_branch: bool = False,
):
    """Valida que la sede de la caja pertenezca a la empresa."""
    branch = None
    if branch_id:
        branch = Branch.query.filter_by(id=branch_id, tenant_id=tenant_id).first()
        if branch is None:
            return None, "La sede seleccionada no es válida."

    if require_branch and branch is None:
        return None, "Selecciona la sede que corresponde a esta caja."
    return branch.id if branch else None, None


def _active_branches(tenant_id: int):
    return Branch.query.filter_by(tenant_id=tenant_id, is_active=True).order_by(Branch.name.asc()).all()


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
