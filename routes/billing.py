"""
Billing del SaaS: gestión del plan, suscripción y portal Stripe.
NOTA: este módulo deja la estructura lista; la integración real con Stripe
se completa cuando configures las claves en .env.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, current_app, request

from flask_login import login_required, current_user

from models import db
from models.tenant import Plan, Subscription
from services.tenant_context import current_tenant
from services.permissions import admin_required, tenant_required
from services.plan_catalog import features_for, ordered_plans, role_summary_for

billing_bp = Blueprint("billing", __name__, url_prefix="/app/billing")


@billing_bp.route("/")
@login_required
@admin_required
@tenant_required
def index():
    tenant = current_tenant()
    plans = ordered_plans(Plan.query.filter_by(is_active=True).all())
    return render_template(
        "billing/index.html",
        tenant=tenant,
        plans=plans,
        features_for=features_for,
        role_summary_for=role_summary_for,
    )


@billing_bp.route("/checkout/<plan_code>", methods=["POST"])
@login_required
@admin_required
@tenant_required
def checkout(plan_code):
    """Inicia un checkout con Stripe para cambiar al plan indicado."""
    plan = Plan.query.filter_by(code=plan_code, is_active=True).first_or_404()

    # TODO: integrar stripe.checkout.Session.create(...)
    flash(f"Integración de Stripe pendiente — plan seleccionado: {plan.name}", "info")
    return redirect(url_for("billing.index"))


@billing_bp.route("/webhook", methods=["POST"])
def webhook():
    """Endpoint para webhooks de Stripe (subscription.updated, invoice.paid, ...)."""
    # TODO: validar firma con STRIPE_WEBHOOK_SECRET y actualizar Subscription
    return "", 200
