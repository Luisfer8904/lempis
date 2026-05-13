"""
Inicialización de modelos y extensiones de DB.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()

login_manager.login_view = "auth.login"
login_manager.login_message = "Por favor inicia sesión para acceder a esta página."
login_manager.login_message_category = "warning"


# Importar modelos para que SQLAlchemy los registre
from models.tenant import Tenant, Plan, Subscription  # noqa: E402,F401
from models.user import User, Role, UserRole          # noqa: E402,F401
from models.catalog import Customer, Product, Category, ProductBatch  # noqa: E402,F401
from models.invoice import Invoice, InvoiceItem        # noqa: E402,F401
from models.country import Country, TaxConfig          # noqa: E402,F401
from models.audit import AuditLog                      # noqa: E402,F401
