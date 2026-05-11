"""
Punto de entrada de la aplicación SaaS de facturación multi-tenant.
"""
import os
from flask import Flask, g

from config import config
from models import db, login_manager, migrate
from services.tenant_context import resolve_tenant


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

    app.register_blueprint(landing_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(billing_bp)
    app.register_blueprint(clientes_bp)
    app.register_blueprint(productos_bp)
    app.register_blueprint(categorias_bp)

    # Middleware: resolver tenant en cada request
    @app.before_request
    def _attach_tenant():
        resolve_tenant()

    # Variables globales para los templates
    @app.context_processor
    def _inject_globals():
        return {
            "APP_NAME": app.config["APP_NAME"],
            "tenant": getattr(g, "tenant", None),
        }

    # Healthcheck
    @app.route("/health")
    def _health():
        return {"status": "ok", "app": app.config["APP_NAME"]}

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
