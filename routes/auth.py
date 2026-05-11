"""
Auth + onboarding del tenant.
- Signup: crea Tenant + User owner + Subscription Free trial.
- Login / Logout
- Recuperar contraseña (placeholder, listo para conectar email)
"""
import re
from datetime import datetime, timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, session, current_app
)
from flask_login import login_user, logout_user, login_required, current_user

from models import db
from models.tenant import Tenant, Plan, Subscription
from models.user import User, Role, UserRole

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


# -------- Helpers --------

def _slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:60] or "empresa"


def _unique_slug(base: str) -> str:
    slug = _slugify(base)
    candidate = slug
    i = 2
    while Tenant.query.filter_by(slug=candidate).first() is not None:
        candidate = f"{slug}-{i}"
        i += 1
    return candidate


# -------- Rutas --------

@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        company = request.form.get("company", "").strip()
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        country_code = request.form.get("country_code", current_app.config["DEFAULT_COUNTRY"])

        if not (company and full_name and email and password):
            flash("Por favor completa todos los campos.", "warning")
            return redirect(url_for("auth.signup"))

        if User.query.filter_by(email=email).first():
            flash("Ya existe una cuenta con ese correo.", "danger")
            return redirect(url_for("auth.signup"))

        # 1) Crear Tenant
        tenant = Tenant(
            name=company,
            slug=_unique_slug(company),
            country_code=country_code,
            currency=current_app.config["DEFAULT_CURRENCY"],
            email=email,
            trial_ends_at=datetime.utcnow() + timedelta(days=14),
        )
        db.session.add(tenant)
        db.session.flush()

        # 2) Crear User owner
        user = User(
            tenant_id=tenant.id,
            email=email,
            full_name=full_name,
            is_owner=True,
            email_verified=False,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        # 3) Asignar rol owner
        owner_role = Role.query.filter_by(code="owner").first()
        if owner_role:
            db.session.add(UserRole(user_id=user.id, role_id=owner_role.id, tenant_id=tenant.id))

        # 4) Suscripción Free trial
        free_plan = Plan.query.filter_by(code="free").first()
        if free_plan:
            db.session.add(Subscription(
                tenant_id=tenant.id,
                plan_id=free_plan.id,
                status="trialing",
                current_period_start=datetime.utcnow(),
                current_period_end=tenant.trial_ends_at,
            ))

        db.session.commit()

        login_user(user, remember=True)
        session["tenant_id"] = tenant.id
        flash(f"¡Bienvenido a {current_app.config['APP_NAME']}! Tu prueba gratis de 14 días ha comenzado.", "success")
        return redirect(url_for("dashboard.home"))

    return render_template("auth/signup.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        user = User.query.filter_by(email=email, is_active=True).first()
        if not user or not user.check_password(password):
            flash("Correo o contraseña inválidos.", "danger")
            return redirect(url_for("auth.login"))

        if not user.tenant.is_active:
            flash("Tu empresa está suspendida. Contacta soporte.", "warning")
            return redirect(url_for("auth.login"))

        user.touch_login()
        db.session.commit()

        login_user(user, remember=remember)
        session["tenant_id"] = user.tenant_id
        return redirect(url_for("dashboard.home"))

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    session.pop("tenant_id", None)
    flash("Sesión cerrada correctamente.", "info")
    return redirect(url_for("landing.index"))


@auth_bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    # Placeholder — implementar envío real con Flask-Mail
    if request.method == "POST":
        flash("Si el correo existe, te enviaremos instrucciones.", "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot.html")
