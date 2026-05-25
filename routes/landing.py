"""Landing page pública del SaaS."""
from flask import Blueprint, render_template, current_app

from models.tenant import Plan
from services.plan_catalog import features_for, ordered_plans, role_summary_for

landing_bp = Blueprint("landing", __name__)


@landing_bp.route("/")
def index():
    plans = ordered_plans(Plan.query.filter_by(is_active=True).all())
    return render_template(
        "landing/index.html",
        app_name=current_app.config["APP_NAME"],
        plans=plans,
        features_for=features_for,
        role_summary_for=role_summary_for,
    )


@landing_bp.route("/precios")
def pricing():
    plans = ordered_plans(Plan.query.filter_by(is_active=True).all())
    return render_template(
        "landing/pricing.html",
        plans=plans,
        features_for=features_for,
        role_summary_for=role_summary_for,
    )
