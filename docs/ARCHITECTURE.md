# Arquitectura

## Visión general

Aplicación web monolítica en Flask, multi-tenant con aislamiento por `tenant_id`,
desplegada como un único servicio + base de datos PostgreSQL.

```
                ┌────────────────┐
   Internet ──▶ │ Nginx + SSL    │
                └──────┬─────────┘
                       ▼
                ┌────────────────┐    ┌──────────────┐
                │ Gunicorn (Flask│───▶│ PostgreSQL   │
                │  app factory)  │    │ (multi-tenant│
                └──────┬─────────┘    │  shared DB)  │
                       │              └──────────────┘
                       ▼
                ┌────────────────┐
                │ Stripe Webhooks│
                └────────────────┘
```

## Multi-tenancy: Shared DB con `tenant_id`

**Decisión:** una sola base de datos, todas las tablas de negocio incluyen `tenant_id`.

**Razones:**
- Operación más simple (una DB que respaldar, migrar, monitorear).
- Más barato a escala temprana.
- Suficiente aislamiento para el caso de uso (PYMEs LatAm).

**Garantías de aislamiento:**
1. Toda tabla con datos del cliente tiene `tenant_id` con FK a `tenants.id`.
2. `services/tenant_context.py` resuelve el tenant activo en cada request.
3. Decorador `tenant_required` verifica que la sesión tenga tenant.
4. Helper `TenantScopedMixin.for_tenant(id)` y `require_same_tenant(obj)` para queries seguras.
5. (Próximo) Activar PostgreSQL Row-Level Security como capa adicional.

## Resolución de Tenant

Tres estrategias configurables vía `TENANT_RESOLVER`:

- `session` (default): se guarda `tenant_id` al hacer login. Sirve con dominio único.
- `subdomain`: extrae el slug de `empresa.tudominio.com`.
- `header`: usa `X-Tenant-Slug` (útil para futuras APIs).

## Modelos principales

```
Tenant ──┬── Subscription ── Plan
         ├── User ── UserRole ── Role
         ├── Customer
         ├── Product ── Category
         ├── Invoice ── InvoiceItem
         └── AuditLog
```

## Pagos (Stripe)

- Cada plan tiene `stripe_price_id_monthly` / `_yearly`.
- Al cambiar de plan: se crea un Stripe Checkout Session.
- Webhooks (`/app/billing/webhook`) actualizan `Subscription.status` y periodos.

## Seguridad

- Passwords hasheados con `werkzeug.security.generate_password_hash`.
- Sesión Flask con cookie `Secure + HttpOnly + SameSite=Lax`.
- CSRF con `Flask-WTF` (a habilitar en formularios).
- Rate limiting con `Flask-Limiter`.
- Auditoría: tabla `audit_logs` para acciones sensibles.

## Internacionalización

- `Country` + `TaxConfig` precargados con países LatAm.
- Cada `Tenant` tiene su `country_code`, `currency`, `locale`, `timezone`.
- Plantillas en español por defecto; arquitectura preparada para traducción futura.
