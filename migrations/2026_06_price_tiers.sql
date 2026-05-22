-- ============================================================
-- Migración: agrega 3 tipos de precios por producto + tier preferido por cliente
-- Compatible con MySQL 8.0.x (Oracle/RDS). Idempotente.
-- Requiere que ya exista lempis_add_column_if_missing (definido en 2026_06_full_sync.sql).
-- ============================================================

-- Crea el helper aquí también por si esta migración se corre suelta
DROP PROCEDURE IF EXISTS lempis_add_column_if_missing;
DELIMITER //
CREATE PROCEDURE lempis_add_column_if_missing(
  IN tbl VARCHAR(64),
  IN col VARCHAR(64),
  IN coldef TEXT
)
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = tbl AND COLUMN_NAME = col
  ) THEN
    SET @s = CONCAT('ALTER TABLE `', tbl, '` ADD COLUMN `', col, '` ', coldef);
    PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
  END IF;
END//
DELIMITER ;

-- ===== PRODUCTOS: 3 tipos de precio =====
CALL lempis_add_column_if_missing('lempis_productos', 'price_wholesale',
  'DECIMAL(12,2) NOT NULL DEFAULT 0');

CALL lempis_add_column_if_missing('lempis_productos', 'price_special',
  'DECIMAL(12,2) NOT NULL DEFAULT 0');

-- Backfill: si los nuevos precios están en 0, copia el precio general como punto de partida
UPDATE lempis_productos
   SET price_wholesale = price
 WHERE (price_wholesale IS NULL OR price_wholesale = 0)
   AND price IS NOT NULL AND price > 0;

UPDATE lempis_productos
   SET price_special = price
 WHERE (price_special IS NULL OR price_special = 0)
   AND price IS NOT NULL AND price > 0;

-- ===== CLIENTES: tier de precio preferido =====
CALL lempis_add_column_if_missing('lempis_clientes', 'preferred_price_tier',
  "ENUM('general','mayorista','especial') NOT NULL DEFAULT 'general'");

-- Limpieza del helper
DROP PROCEDURE IF EXISTS lempis_add_column_if_missing;

-- ===== Verificación rápida =====
SELECT
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'lempis_productos'
      AND COLUMN_NAME IN ('price_wholesale','price_special')) AS productos_ok,
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'lempis_clientes'
      AND COLUMN_NAME = 'preferred_price_tier') AS clientes_ok;
