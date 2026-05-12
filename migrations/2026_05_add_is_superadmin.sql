-- Migración: agregar columna is_superadmin a la tabla lempis_usuarios
-- Compatible con MySQL 8.x.
-- Es idempotente: si ya existe la columna, no falla.

-- 1) Agregar la columna (con valor por defecto FALSE)
ALTER TABLE lempis_usuarios
  ADD COLUMN IF NOT EXISTS is_superadmin BOOLEAN NOT NULL DEFAULT FALSE;

-- 2) (Opcional) Crear índice si vas a consultar mucho por superadmins
-- CREATE INDEX IF NOT EXISTS idx_lempis_usuarios_superadmin
--   ON lempis_usuarios(is_superadmin);

-- Verificación:
-- SELECT email, is_superadmin FROM lempis_usuarios WHERE is_superadmin = TRUE;
