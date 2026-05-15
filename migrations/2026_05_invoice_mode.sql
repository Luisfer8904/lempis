-- ============================================================
-- Migración: modos de facturación pluggables (multi-país)
-- ============================================================
-- Agrega columnas invoice_mode y invoice_prefix al tenant.
-- Migra automáticamente los tenants que ya tienen CAI configurado.

-- 1) Agregar columnas
ALTER TABLE lempis_empresas
  ADD COLUMN IF NOT EXISTS invoice_mode VARCHAR(30) NOT NULL DEFAULT 'simple',
  ADD COLUMN IF NOT EXISTS invoice_prefix VARCHAR(20) DEFAULT '';

-- 2) Auto-migrar: si la empresa ya tiene CAI, asumimos modo SAR Honduras
UPDATE lempis_empresas
   SET invoice_mode = 'sar_hn'
 WHERE cai_code IS NOT NULL AND cai_code != ''
   AND invoice_mode = 'simple';

-- 3) Verificación rápida
-- SELECT slug, invoice_mode, invoice_prefix, cai_code IS NOT NULL AS has_cai
--   FROM lempis_empresas;
