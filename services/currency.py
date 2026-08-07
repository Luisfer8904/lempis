"""Formato monetario uniforme para la aplicación."""
from decimal import Decimal, InvalidOperation


CURRENCY_SYMBOLS = {
    "HNL": "L",
    "USD": "$",
    "GTQ": "Q",
    "NIO": "C$",
    "CRC": "₡",
    "PAB": "B/.",
    "MXN": "$",
    "COP": "$",
    "PEN": "S/",
    "DOP": "RD$",
    "CLP": "$",
    "ARS": "$",
}


def currency_symbol(currency_code: str | None) -> str:
    """Devuelve el símbolo configurado para un código de moneda."""
    code = (currency_code or "HNL").strip().upper()
    return CURRENCY_SYMBOLS.get(code, code)


def format_money(value, currency_code: str | None = None) -> str:
    """Formatea moneda con símbolo, miles y dos decimales."""
    try:
        amount = Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    if amount == 0:
        amount = Decimal("0")
    return f"{currency_symbol(currency_code)} {amount:,.2f}"
