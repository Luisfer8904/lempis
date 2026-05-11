"""Landing page pública del SaaS."""
from flask import Blueprint, render_template, current_app

from models.tenant import Plan

landing_bp = Blueprint("landing", __name__)


@landing_bp.route("/")
def index():
    plans = Plan.query.filter_by(is_active=True).order_by(Plan.price_monthly).all()
    return render_template(
        "landing/index.html",
        app_name=current_app.config["APP_NAME"],
        plans=plans,
    )


@landing_bp.route("/precios")
def pricing():
    plans = Plan.query.filter_by(is_active=True).order_by(Plan.price_monthly).all()
    return render_template("landing/pricing.html", plans=plans)
