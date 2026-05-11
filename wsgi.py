"""WSGI entry point para Gunicorn / producción."""
from app import create_app

application = create_app("production")
