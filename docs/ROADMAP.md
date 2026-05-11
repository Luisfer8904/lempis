# Roadmap

## Fase 0 — Bases ✅ (completada)
- [x] Estructura del proyecto
- [x] Modelos multi-tenant (Tenant, User, Plan, Subscription, Customer, Product, Invoice, Country, TaxConfig, AuditLog)
- [x] App factory + blueprints (landing, auth, dashboard, billing)
- [x] Resolución de tenant + decoradores de permisos
- [x] Templates base (landing, login, signup, dashboard, billing)
- [x] Script de inicialización con seed de países, roles y planes
- [x] Documentación (README, ARCHITECTURE, ROADMAP)

## Fase 1 — MVP funcional 🔄
- [ ] CRUD completo de Customers
- [ ] CRUD completo de Products + Categories
- [ ] Creación, edición y emisión de Invoices
- [ ] Generación de PDF de factura (ReportLab)
- [ ] Numeración de facturas configurable por tenant
- [ ] Configuración de empresa (logo, datos fiscales)
- [ ] Validaciones de límites por plan (max_users, max_invoices, etc.)

## Fase 2 — Suscripciones reales
- [ ] Integrar Stripe Checkout para planes Pro/Business
- [ ] Webhooks de Stripe (created, updated, deleted)
- [ ] Customer Portal de Stripe (cambiar tarjeta, ver facturas)
- [ ] Manejo de trial expirado (bloqueo / downgrade)
- [ ] Emails transaccionales (bienvenida, factura emitida, suscripción)

## Fase 3 — Equipo y permisos
- [ ] Invitar usuarios al tenant por email
- [ ] Aceptar invitación + crear cuenta
- [ ] Página de gestión de usuarios y roles
- [ ] Permisos granulares por sección

## Fase 4 — Reportes y analítica
- [ ] Dashboard con gráficos (Chart.js): ventas por mes, top productos, top clientes
- [ ] Reporte de IVA/ISV mensual
- [ ] Exportar a Excel (openpyxl)
- [ ] Búsqueda y filtros avanzados

## Fase 5 — Producción
- [ ] Configurar deploy (AWS Lightsail / Render / Fly.io)
- [ ] CI/CD con GitHub Actions
- [ ] Backups automáticos de PostgreSQL
- [ ] Monitoreo (Sentry + Uptime)
- [ ] Subdominios por tenant (empresa.tudominio.com)
- [ ] Dominio + SSL

## Fase 6 — Crecimiento
- [ ] API REST pública (autenticada con JWT)
- [ ] Integración con bancos / pasarelas locales
- [ ] App móvil (React Native / Flutter)
- [ ] Módulo vertical: Agro / Veterinaria (clientes existentes Invagro)
- [ ] Marketplace de plantillas de factura
- [ ] Multi-moneda en una misma factura
