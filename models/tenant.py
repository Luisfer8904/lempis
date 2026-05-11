"""
Modelos centrales del SaaS:
- Tenant: representa una empresa cliente del SaaS (multi-tenancy).
- Plan: planes disponibles (Free, Pro, Business).
- Subscription: vínculo entre tenant y plan + estado en Stripe.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Numeric, Enum, Text
from sqlalchemy.orm import relationship

from models import db
from models.base import TimestampMixin


class Tenant(db.Model, TimestampMixin):
    """
    Una empresa que usa el SaaS.
    Cada Tenant tiene sus propios usuarios, clientes, productos y facturas.
    """
    __tablename__ = "lempis_empresas"

    id = Column(Integer, primary_key=True)

    # Identidad de la empresa
    name = Column(String(120), nullable=False)
    legal_name = Column(String(160))
    slug = Column(String(60), unique=True, nullable=False, index=True)
    tax_id = Column(String(40))  # RTN / RUC / RFC / NIT...

    # Localización
    country_code = Column(String(2), ForeignKey("lempis_paises.code"), default="HN", nullable=False)
    currency = Column(String(3), default="HNL", nullable=False)
    locale = Column(String(10), default="es")
    timezone = Column(String(50), default="America/Tegucigalpa")

    # Contacto
    email = Column(String(120))
    phone = Column(String(40))
    address = Column(Text)
    logo_url = Column(String(255))

    # Estado
    is_active = Column(Boolean, default=True, nullable=False)
    trial_ends_at = Column(DateTime)
    onboarding_completed = Column(Boolean, default=False)

    # Relaciones
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    subscription = relationship("Subscription", back_populates="tenant", uselist=False, cascade="all, delete-orphan")
    customers = relationship("Customer", back_populates="tenant", cascade="all, delete-orphan")
    products = relationship("Product", back_populates="tenant", cascade="all, delete-orphan")
    invoices = relationship("Invoice", back_populates="tenant", cascade="all, delete-orphan")
    country = relationship("Country", foreign_keys=[country_code])

    def __repr__(self):
        return f"<Tenant {self.slug}>"


class Plan(db.Model, TimestampMixin):
    """Planes del SaaS (Free, Pro, Business, ...)."""
    __tablename__ = "lempis_planes"

    id = Column(Integer, primary_key=True)
    code = Column(String(40), unique=True, nullable=False)   # free, pro, business
    name = Column(String(80), nullable=False)
    description = Column(Text)
    price_monthly = Column(Numeric(10, 2), default=0)
    price_yearly = Column(Numeric(10, 2), default=0)
    currency = Column(String(3), default="USD")

    # Límites del plan
    max_users = Column(Integer, default=1)
    max_invoices_per_month = Column(Integer, default=20)
    max_products = Column(Integer, default=50)
    max_customers = Column(Integer, default=100)

    # Features (flags simples)
    can_export_pdf = Column(Boolean, default=True)
    can_export_excel = Column(Boolean, default=False)
    can_use_api = Column(Boolean, default=False)
    can_custom_branding = Column(Boolean, default=False)

    # Stripe
    stripe_price_id_monthly = Column(String(120))
    stripe_price_id_yearly = Column(String(120))

    is_active = Column(Boolean, default=True)

    subscriptions = relationship("Subscription", back_populates="plan")

    def __repr__(self):
        return f"<Plan {self.code}>"


class Subscription(db.Model, TimestampMixin):
    """Suscripción activa de un tenant a un plan."""
    __tablename__ = "lempis_suscripciones"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("lempis_empresas.id", ondelete="CASCADE"), unique=True, nullable=False)
    plan_id = Column(Integer, ForeignKey("lempis_planes.id"), nullable=False)

    status = Column(
        Enum("trialing", "active", "past_due", "canceled", "unpaid", name="subscription_status"),
        default="trialing",
        nullable=False,
    )

    # Stripe
    stripe_customer_id = Column(String(120))
    stripe_subscription_id = Column(String(120))

    # Periodo
    current_period_start = Column(DateTime)
    current_period_end = Column(DateTime)
    cancel_at_period_end = Column(Boolean, default=False)

    tenant = relationship("Tenant", back_populates="subscription")
    plan = relationship("Plan", back_populates="subscriptions")

    def is_active(self) -> bool:
        return self.status in ("trialing", "active")

    def __repr__(self):
        return f"<Subscription tenant={self.tenant_id} plan={self.plan_id} {self.status}>"
