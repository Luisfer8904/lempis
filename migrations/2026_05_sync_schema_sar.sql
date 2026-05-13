-- ============================================================
-- Migración consolidada: sincronizar schema con modelos actuales
-- ============================================================
-- Agrega TODAS las columnas que faltan en producción después de los
-- sprints SAR/CAI + facturas + superadmin. Es idempotente: usa
-- ADD COLUMN IF NOT EXISTS (MySQL 8.0.29+).
--
-- Si tu MySQL es < 8.0.29, las líneas IF NOT EXISTS fallarán; en ese
-- caso ejecuta una por una y comenta las que ya existan.
-- ============================================================

-- ---------- lempis_empresas (Tenant) ----------
ALTER TABLE lempis_empresas
  ADD COLUMN IF NOT EXISTS cai_code VARCHAR(40) NULL,
  ADD COLUMN IF NOT EXISTS cai_valid_from DATETIME NULL,
  ADD COLUMN IF NOT EXISTS cai_valid_until DATETIME NULL,
  ADD COLUMN IF NOT EXISTS cai_range_start INT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS cai_range_end   INT NULL DEFAULT 99999999,
  ADD COLUMN IF NOT EXISTS establecimiento VARCHAR(3)  NULL DEFAULT '001',
  ADD COLUMN IF NOT EXISTS punto_emision   VARCHAR(3)  NULL DEFAULT '001',
  ADD COLUMN IF NOT EXISTS tipo_documento  VARCHAR(2)  NULL DEFAULT '01',
  ADD COLUMN IF NOT EXISTS next_invoice_number INT NULL DEFAULT 1;

-- ---------- lempis_usuarios (User) ----------
ALTER TABLE lempis_usuarios
  ADD COLUMN IF NOT EXISTS is_superadmin BOOLEAN NOT NULL DEFAULT FALSE;

-- ---------- lempis_facturas (Invoice) ----------
ALTER TABLE lempis_facturas
  ADD COLUMN IF NOT EXISTS payment_method
    ENUM('efectivo','transferencia','tarjeta','credito') NOT NULL DEFAULT 'efectivo',
  ADD COLUMN IF NOT EXISTS payment_terms_days INT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS cai_code VARCHAR(40) NULL,
  ADD COLUMN IF NOT EXISTS cai_range_start INT NULL,
  ADD COLUMN IF NOT EXISTS cai_range_end INT NULL,
  ADD COLUMN IF NOT EXISTS cai_valid_until DATETIME NULL,
  ADD COLUMN IF NOT EXISTS emisor_name VARCHAR(160) NULL,
  ADD COLUMN IF NOT EXISTS emisor_tax_id VARCHAR(40) NULL,
  ADD COLUMN IF NOT EXISTS emisor_address TEXT NULL,
  ADD COLUMN IF NOT EXISTS receptor_name VARCHAR(160) NULL,
  ADD COLUMN IF NOT EXISTS receptor_tax_id VARCHAR(40) NULL;

-- ============================================================
-- Verificación rápida
-- ============================================================
-- SHOW COLUMNS FROM lempis_empresas LIKE 'cai_%';
-- SHOW COLUMNS FROM lempis_usuarios LIKE 'is_superadmin';
-- SHOW COLUMNS FROM lempis_facturas LIKE 'cai_%';
-- SHOW COLUMNS FROM lempis_facturas LIKE 'payment_%';
-- SHOW COLUMNS FROM lempis_facturas LIKE 'emisor_%';
-- SHOW COLUMNS FROM lempis_facturas LIKE 'receptor_%';
