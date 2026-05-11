# SaaS de Facturación Multi-Tenant

> Plataforma SaaS de facturación profesional para PYMEs de LatAm.
> Cada empresa tiene su propio espacio aislado: clientes, productos, facturas y usuarios.

**Nombre del producto:** *(pendiente — lo decidiremos)*

---

## ✨ Características

- **Multi-tenant**: una sola instalación sirve a muchas empresas; cada una solo ve sus datos.
- **Multi-país LatAm**: Honduras, Guatemala, El Salvador, Nicaragua, Costa Rica, Panamá, México, Colombia, Perú, Ecuador, Rep. Dominicana, Chile, Argentina — con sus impuestos pre-cargados (ISV, IVA, IGV, ITBMS, ITBIS).
- **Roles y permisos por empresa**: owner, admin, vendedor, contador, viewer.
- **Planes y suscripciones (Stripe)**: Free, Pro, Business — con límites por plan.
- **Reportes y dashboard**: KPIs de ingresos, facturas, clientes, productos.

## 🧱 Stack

- **Backend**: Flask 3 + SQLAlchemy 2 + Flask-Login + Flask-Migrate
- **Base de datos**: PostgreSQL (recomendado) — soporta multi-tenant con `tenant_id`
- **Frontend**: Templates Jinja2 + Tailwind CSS
- **Pagos**: Stripe (Checkout + Webhooks)
- **Email**: Flask-Mail (SMTP)

## 📁 Estructura

```
Saas Honduras/
├── app.py                  # App factory
├── wsgi.py                 # Entry para Gunicorn
├── config.py               # Configuración multi-entorno
├── requirements.txt
├── .env.example
│
├── models/                 # Modelos SQLAlchemy
│   ├── __init__.py
│   ├── base.py             # Mixins (TenantScoped, Timestamps)
│   ├── tenant.py           # Tenant, Plan, Subscription
│   ├── user.py             # User, Role, UserRole
│   ├── country.py          # Country, TaxConfig
│   ├── catalog.py          # Customer, Product, Category
│   ├── invoice.py          # Invoice, InvoiceItem
│   └── audit.py            # AuditLog
│
├── routes/                 # Blueprints
│   ├── landing.py          # Páginas públicas
│   ├── auth.py             # Login / Signup / Logout
│   ├── dashboard.py        # Dashboard del tenant
│   └── billing.py          # Suscripción + Stripe
│
├── services/               # Lógica de negocio reutilizable
│   ├── tenant_context.py   # Resolución del tenant activo
│   └── permissions.py      # Decoradores de roles
│
├── templates/
│   ├── base.html
│   ├── landing/            # index.html, pricing.html
│   ├── auth/               # login, signup, forgot
│   ├── dashboard/          # home.html
│   └── billing/            # index.html
│
├── static/
│   ├── css/styles.css
│   └── js/main.js
│
├── scripts/
│   ├── init_db.py          # Crea tablas + seed (países, roles, planes)
│   └── create_demo_tenant.py
│
├── migrations/             # Flask-Migrate (alembic)
└── docs/
    ├── ARCHITECTURE.md
    └── ROADMAP.md
```

## 🚀 Setup local (rápido — usa SQLite, sin instalar PostgreSQL)

```bash
cd "Saas Honduras"

# 1) Crear y activar venv
python3 -m venv venv
source venv/bin/activate

# 2) Instalar dependencias
pip install --upgrade pip
pip install -r requirements.txt

# 3) Copiar archivo de entorno (sin comentarios al final por zsh)
cp .env.example .env

# 4) Inicializar DB (SQLite por defecto) + seed (países, roles, planes)
python scripts/init_db.py

# 5) (opcional) Crear tenant de demo
python scripts/create_demo_tenant.py

# 6) Levantar el servidor
python app.py
# → http://localhost:5000
```

**Login demo:** `demo@example.com` / `demo1234`

## 🐘 Cambiar a PostgreSQL (cuando quieras producción)

1. Instalar PostgreSQL: `brew install postgresql@16 && brew services start postgresql@16`
2. Crear DB: `createdb saas_facturacion`
3. En `.env`, descomentar y editar:
   ```
   DATABASE_URL=postgresql://tu_usuario@localhost:5432/saas_facturacion
   ```
4. Volver a correr `python scripts/init_db.py`

## 🧪 Tests

```bash
pytest
```

## 🌐 Deployment (resumen)

1. Servidor con PostgreSQL.
2. `gunicorn wsgi:application` detrás de Nginx.
3. Configurar SSL (Let's Encrypt).
4. Variables de entorno en producción (`FLASK_ENV=production`).
5. Configurar webhooks de Stripe → `/app/billing/webhook`.

## 📚 Más documentación

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — decisiones técnicas
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — plan de desarrollo por fases

## 🪪 Licencia

Propietario — todos los derechos reservados.
