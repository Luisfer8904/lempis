-- Límites comerciales por plan.
-- Idempotente: agrega columnas solo si faltan.

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

CALL lempis_add_column_if_missing('lempis_planes', 'max_suppliers', 'INT DEFAULT 2');
CALL lempis_add_column_if_missing('lempis_planes', 'max_branches', 'INT DEFAULT 1');
CALL lempis_add_column_if_missing('lempis_planes', 'max_warehouses', 'INT DEFAULT 1');
CALL lempis_add_column_if_missing('lempis_planes', 'can_use_advanced_reports', 'BOOLEAN DEFAULT FALSE');
CALL lempis_add_column_if_missing('lempis_planes', 'can_use_multi_branch', 'BOOLEAN DEFAULT FALSE');
CALL lempis_add_column_if_missing('lempis_planes', 'can_customize_roles', 'BOOLEAN DEFAULT FALSE');
