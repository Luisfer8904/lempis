"""
Caja diaria: aperturas, gastos y cierres.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from models import db
from models.cash import CashClosure, CashExpense
from models.invoice import Invoice, InvoicePayment
from models.locations import Branch, Warehouse
from services.permissions import permission_required, tenant_required
from services.tenant_context import current_tenant

caja_bp = Blueprint("caja", __name__, url_prefix="/app/caja")

VALID_INVOICE_STATUSES = ["issued", "paid", "partially_paid", "overdue"]


@caja_bp.route("/")
@login_required
@tenant_required
@permission_required("receivables.view")
def index():
    tenant = current_tenant()
    today = datetime.utcnow().date()
    start_date = _parse_date(request.args.get("desde")) or today.replace(day=1)
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
    summary = _cash_summary(tenant.id, period_start, period_end, closures)
    return render_template(
        "caja/index.html",
        tenant=tenant,
        closures=closures,
        expenses=expenses,
        branches=_active_branches(tenant.id),
        summary=summary,
        desde=start_date.isoformat(),
        hasta=end_date.isoformat(),
    )


@caja_bp.route("/nuevo", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("receivables.manage")
def new_closure():
    tenant = current_tenant()
    closure_date = _parse_date(request.values.get("fecha")) or datetime.utcnow().date()
    suggested = _suggested_closure_values(tenant.id, closure_date)
    branches = _active_branches(tenant.id)
    warehouses = _active_warehouses(tenant.id)

    if request.method == "POST":
        closure = CashClosure(tenant_id=tenant.id, user_id=current_user.id)
        _fill_closure_from_form(closure, suggested)
        db.session.add(closure)
        db.session.commit()
        flash("Cierre de caja registrado.", "success")
        return redirect(url_for("caja.index", desde=closure.closure_date.isoformat(), hasta=closure.closure_date.isoformat()))

    return render_template(
        "caja/form.html",
        tenant=tenant,
        closure=None,
        suggested=suggested,
        branches=branches,
        warehouses=warehouses,
        fecha=closure_date.isoformat(),
    )


@caja_bp.route("/<int:closure_id>/editar", methods=["GET", "POST"])
@login_required
@tenant_required
@permission_required("receivables.manage")
def edit_closure(closure_id):
    tenant = current_tenant()
    closure = CashClosure.query.filter_by(id=closure_id, tenant_id=tenant.id).first_or_404()
    suggested = _suggested_closure_values(tenant.id, closure.closure_date)
    branches = _active_branches(tenant.id)
    warehouses = _active_warehouses(tenant.id)

    if request.method == "POST":
        _fill_closure_from_form(closure, suggested)
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
@permission_required("receivables.manage")
def create_expense():
    tenant = current_tenant()
    amount = _decimal(request.form.get("amount"))
    description = (request.form.get("description") or "").strip()
    if amount <= 0 or not description:
        flash("Ingresa una descripción y un monto válido para el gasto.", "warning")
        return redirect(url_for("caja.index"))

    expense_date = _parse_datetime(request.form.get("expense_date")) or datetime.utcnow()
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
    db.session.commit()
    flash("Gasto registrado.", "success")
    return redirect(url_for("caja.index", desde=expense_date.date().isoformat(), hasta=expense_date.date().isoformat()))


def _fill_closure_from_form(closure: CashClosure, suggested: dict) -> None:
    closure.closure_date = _parse_date(request.form.get("closure_date")) or suggested["closure_date"]
    closure.branch_id = request.form.get("branch_id", type=int) or None
    closure.warehouse_id = request.form.get("warehouse_id", type=int) or None
    closure.opening_amount = _decimal(request.form.get("opening_amount"))
    closure.cash_sales_amount = suggested["cash_sales_amount"]
    closure.transfer_sales_amount = suggested["transfer_sales_amount"]
    closure.card_sales_amount = suggested["card_sales_amount"]
    closure.credit_sales_amount = suggested["credit_sales_amount"]
    closure.receivable_cash_amount = suggested["receivable_cash_amount"]
    closure.receivable_transfer_amount = suggested["receivable_transfer_amount"]
    closure.receivable_card_amount = suggested["receivable_card_amount"]
    closure.expenses_amount = suggested["expenses_amount"]
    closure.expected_cash_amount = (
        closure.opening_amount
        + closure.cash_sales_amount
        + closure.receivable_cash_amount
        - closure.expenses_amount
    )
    closure.actual_cash_amount = _decimal(request.form.get("actual_cash_amount"))
    closure.delivered_cash_amount = _decimal(request.form.get("delivered_cash_amount"))
    closure.variance_amount = closure.actual_cash_amount - closure.expected_cash_amount
    closure.notes = (request.form.get("notes") or "").strip() or None


def _suggested_closure_values(tenant_id: int, closure_date):
    start, end = _period_bounds(closure_date, closure_date)
    sales = _sum_invoices_by_method(tenant_id, start, end)
    payments = _sum_payments_by_method(tenant_id, start, end)
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
    return {
        "closure_date": closure_date,
        "cash_sales_amount": _decimal(sales.get("efectivo")),
        "transfer_sales_amount": _decimal(sales.get("transferencia")),
        "card_sales_amount": _decimal(sales.get("tarjeta")),
        "credit_sales_amount": _decimal(sales.get("credito")),
        "receivable_cash_amount": _decimal(payments.get("efectivo")),
        "receivable_transfer_amount": _decimal(payments.get("transferencia")),
        "receivable_card_amount": _decimal(payments.get("tarjeta")),
        "expenses_amount": _decimal(expenses_amount),
    }


def _cash_summary(tenant_id: int, period_start, period_end, closures):
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
    return {
        "sales": {key: _decimal(value) for key, value in sales.items()},
        "payments": {key: _decimal(value) for key, value in payments.items()},
        "expenses": expenses_by_method,
        "expected_cash": sum(_decimal(c.expected_cash_amount) for c in closures),
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
        )
        .group_by(Invoice.payment_method)
        .all()
    )
    return {row.method: row.total for row in rows}


def _sum_payments_by_method(tenant_id: int, period_start, period_end):
    rows = (
        db.session.query(
            InvoicePayment.payment_method.label("method"),
            func.coalesce(func.sum(InvoicePayment.amount), 0).label("total"),
        )
        .filter(
            InvoicePayment.tenant_id == tenant_id,
            InvoicePayment.paid_at >= period_start,
            InvoicePayment.paid_at < period_end,
        )
        .group_by(InvoicePayment.payment_method)
        .all()
    )
    return {row.method: row.total for row in rows}


def _active_branches(tenant_id: int):
    return Branch.query.filter_by(tenant_id=tenant_id, is_active=True).order_by(Branch.name.asc()).all()


def _active_warehouses(tenant_id: int):
    return Warehouse.query.filter_by(tenant_id=tenant_id, is_active=True).order_by(Warehouse.name.asc()).all()


def _period_bounds(start_date, end_date):
    return datetime.combine(start_date, time.min), datetime.combine(end_date + timedelta(days=1), time.min)


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
        return datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or "0")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")
