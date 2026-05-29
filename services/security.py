"""
Request security helpers.
"""
import secrets

from flask import abort, current_app, request, session


CSRF_SESSION_KEY = "_csrf_token"
CSRF_FIELD_NAME = "_csrf_token"


CSRF_EXEMPT_ENDPOINTS = {
    "billing.webhook",
}


def csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf() -> None:
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return
    if request.endpoint in CSRF_EXEMPT_ENDPOINTS:
        return
    if current_app.config.get("WTF_CSRF_ENABLED") is False:
        return

    expected = session.get(CSRF_SESSION_KEY)
    provided = request.form.get(CSRF_FIELD_NAME) or request.headers.get("X-CSRFToken")
    if not expected or not provided or not secrets.compare_digest(expected, provided):
        abort(400, description="CSRF token inválido o ausente.")
