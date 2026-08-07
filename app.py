"""
Punto de entrada de la aplicación SaaS de facturación multi-tenant.
"""
import os
from flask import Flask, g, send_from_directory

from config import config
from models import db, login_manager, migrate
from services.tenant_context import resolve_tenant
from services.datetime_utils import format_local_datetime
from services.currency import currency_symbol, format_money
from services.security import csrf_token, validate_csrf


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or os.environ.get("FLASK_ENV", "development")

    app = Flask(__name__)
    app.config.from_object(config[config_name])

    # Extensiones
    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)

    # Blueprints
    from routes.landing import landing_bp
    from routes.auth import auth_bp
    from routes.dashboard import dashboard_bp
    from routes.billing import billing_bp
    from routes.clientes import clientes_bp
    from routes.productos import productos_bp
    from routes.categorias import categorias_bp
    from routes.facturas import facturas_bp
    from routes.configuracion import configuracion_bp
    from routes.reportes import reportes_bp
    from routes.usuarios import usuarios_bp
    from routes.admin import admin_bp
    from routes.lotes import lotes_bp
    from routes.pos import pos_bp
    from routes.proveedores import proveedores_bp
    from routes.compras import compras_bp
    from routes.cobros import cobros_bp
    from routes.ubicaciones import ubicaciones_bp
    from routes.caja import caja_bp
    from routes.igh import igh_bp

    app.register_blueprint(landing_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(billing_bp)
    app.register_blueprint(clientes_bp)
    app.register_blueprint(productos_bp)
    app.register_blueprint(categorias_bp)
    app.register_blueprint(facturas_bp)
    app.register_blueprint(configuracion_bp)
    app.register_blueprint(reportes_bp)
    app.register_blueprint(usuarios_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(lotes_bp)
    app.register_blueprint(pos_bp)
    app.register_blueprint(proveedores_bp)
    app.register_blueprint(compras_bp)
    app.register_blueprint(cobros_bp)
    app.register_blueprint(ubicaciones_bp)
    app.register_blueprint(caja_bp)
    app.register_blueprint(igh_bp)

    # Middleware: resolver tenant en cada request
    @app.before_request
    def _attach_tenant():
        resolve_tenant()

    @app.before_request
    def _validate_csrf():
        validate_csrf()

    # Variables globales para los templates
    @app.context_processor
    def _inject_globals():
        tenant = getattr(g, "tenant", None)
        tenant_currency = getattr(tenant, "currency", None) or app.config["DEFAULT_CURRENCY"]
        return {
            "APP_NAME": app.config["APP_NAME"],
            "tenant": tenant,
            "currency_symbol": currency_symbol(tenant_currency),
            "csrf_token": csrf_token,
        }

    @app.template_filter("money")
    def _money_filter(value, currency_code=None):
        tenant = getattr(g, "tenant", None)
        code = currency_code or getattr(tenant, "currency", None) or app.config["DEFAULT_CURRENCY"]
        return format_money(value, code)

    @app.template_filter("local_datetime")
    def _local_datetime_filter(value, fmt="%d/%m/%Y %H:%M"):
        return format_local_datetime(value, fmt, getattr(g, "tenant", None))

    @app.template_filter("local_date")
    def _local_date_filter(value, fmt="%d/%m/%Y"):
        return format_local_datetime(value, fmt, getattr(g, "tenant", None))

    # Healthcheck
    @app.route("/health")
    def _health():
        return {"status": "ok", "app": app.config["APP_NAME"]}

    @app.route("/favicon.ico")
    def _favicon():
        return send_from_directory(
            os.path.join(app.root_path, "static", "img"),
            "favicon.png",
            mimetype="image/png",
        )

    return app


if __name__ == "__main__":
    app = create_app()
    host = app.config.get("APP_HOST", "0.0.0.0")
    port = app.config.get("APP_PORT", 5000)
    debug = app.config.get("DEBUG", True)

    print("=" * 60)
    print(f"  🚀 {app.config['APP_NAME']} — SaaS de Facturación")
    print(f"  📍 http://{host}:{port}")
    print(f"  🔧 Modo: {'Desarrollo' if debug else 'Producción'}")
    print("=" * 60)

    app.run(host=host, port=port, debug=debug)
