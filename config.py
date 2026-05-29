"""
Configuración de la aplicación SaaS de Facturación.
Multi-tenant, multi-país (LatAm), con planes Stripe.
"""
import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


class BaseConfig:
    """Configuración base compartida por todos los entornos."""

    # ---- Flask ----
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-CHANGE-ME")
    APP_NAME = os.environ.get("APP_NAME", "Lempis")
    APP_DOMAIN = os.environ.get("APP_DOMAIN", "lempis.com")
    SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "soporte@example.com")

    # ---- Servidor ----
    APP_HOST = os.environ.get("APP_HOST", "0.0.0.0")
    APP_PORT = int(os.environ.get("APP_PORT", 5001))  # 5000 conflicta con AirPlay en macOS

    # ---- Base de datos ----
    # Local: SQLite (sin instalación). Producción: MySQL (Lightsail).
    _default_sqlite = "sqlite:///" + os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "instance", "lempis_local.db"
    )
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", _default_sqlite)

    # Normalizar URLs:
    # - mysql://...                → mysql+pymysql://...
    # - postgres://... (heredado)  → postgresql+psycopg://...
    if SQLALCHEMY_DATABASE_URI.startswith("mysql://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
            "mysql://", "mysql+pymysql://", 1
        )
    # Asegurar utf8mb4 (soporte completo de emojis, tildes, español/portugués)
    if SQLALCHEMY_DATABASE_URI.startswith("mysql+pymysql://") and "charset=" not in SQLALCHEMY_DATABASE_URI:
        sep = "&" if "?" in SQLALCHEMY_DATABASE_URI else "?"
        SQLALCHEMY_DATABASE_URI = f"{SQLALCHEMY_DATABASE_URI}{sep}charset=utf8mb4"
    elif SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
            "postgres://", "postgresql+psycopg://", 1
        )
    elif SQLALCHEMY_DATABASE_URI.startswith("postgresql://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Pool config para servidores remotos (MySQL/Postgres). SQLite no la usa.
    SQLALCHEMY_ENGINE_OPTIONS = (
        {} if SQLALCHEMY_DATABASE_URI.startswith("sqlite") else {
            "pool_pre_ping": True,
            "pool_recycle": 280,
            "pool_size": 5,
            "max_overflow": 10,
        }
    )

    # ---- Sesión ----
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "False") == "True"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    WTF_CSRF_ENABLED = os.environ.get("WTF_CSRF_ENABLED", "True") == "True"
    PERMANENT_SESSION_LIFETIME = timedelta(
        days=int(os.environ.get("PERMANENT_SESSION_LIFETIME_DAYS", 7))
    )

    # ---- Email ----
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "True") == "True"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "no-reply@example.com")

    # ---- Stripe ----
    STRIPE_PUBLIC_KEY = os.environ.get("STRIPE_PUBLIC_KEY", "")
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PRICE_PRO = os.environ.get("STRIPE_PRICE_PRO", "")
    STRIPE_PRICE_BUSINESS = os.environ.get("STRIPE_PRICE_BUSINESS", "")

    # ---- Multi-tenant ----
    TENANT_RESOLVER = os.environ.get("TENANT_RESOLVER", "session")  # subdomain | session | header
    DEFAULT_LOCALE = os.environ.get("DEFAULT_LOCALE", "es")
    DEFAULT_COUNTRY = os.environ.get("DEFAULT_COUNTRY", "HN")
    DEFAULT_CURRENCY = os.environ.get("DEFAULT_CURRENCY", "HNL")

    # ---- Rate limit ----
    RATELIMIT_DEFAULT = os.environ.get("RATELIMIT_DEFAULT", "200 per hour")
    RATELIMIT_STORAGE_URI = "memory://"

    # ---- Uploads ----
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "static", "uploads")


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    TESTING = False
    SQLALCHEMY_ECHO = False


class TestingConfig(BaseConfig):
    DEBUG = True
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


class ProductionConfig(BaseConfig):
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"


config = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
