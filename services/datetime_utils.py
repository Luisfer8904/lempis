"""
Utilidades para mostrar fechas en la zona horaria del tenant.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "America/Tegucigalpa"


def tenant_timezone(tenant=None):
    name = getattr(tenant, "timezone", None) or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def to_local_datetime(value, tenant=None):
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        return aware.astimezone(tenant_timezone(tenant))
    return value


def to_utc_datetime(value, tenant=None):
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time.min)
    if value.tzinfo is None:
        aware = value.replace(tzinfo=tenant_timezone(tenant))
    else:
        aware = value
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def local_now(tenant=None):
    return datetime.now(timezone.utc).astimezone(tenant_timezone(tenant))


def tenant_today(tenant=None):
    return local_now(tenant).date()


def local_date_range_to_utc(start_date, end_date, tenant=None):
    start_local = datetime.combine(start_date, time.min)
    end_local = datetime.combine(end_date + timedelta(days=1), time.min)
    start_utc = to_utc_datetime(start_local, tenant)
    end_utc = to_utc_datetime(end_local, tenant)
    return start_utc, end_utc


def format_local_datetime(value, fmt="%d/%m/%Y %H:%M", tenant=None, empty="—"):
    if value is None:
        return empty
    local_value = to_local_datetime(value, tenant)
    if isinstance(local_value, (datetime, date)):
        return local_value.strftime(fmt)
    return str(local_value)
