-- IVG cash close: add day expenses used in expected cash calculation.
-- Compatible with MySQL 8.0.x. Idempotent.

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

CALL lempis_add_column_if_missing('ivg_contado', 'expense_amount', 'DECIMAL(12,2) NOT NULL DEFAULT 0 AFTER `withdrawal_amount`');

UPDATE ivg_contado
SET
  expense_amount = COALESCE(expense_amount, 0),
  expected_close_amount = opening_amount + cash_amount - COALESCE(expense_amount, 0) - withdrawal_amount,
  variance_amount = actual_close_amount - (opening_amount + cash_amount - COALESCE(expense_amount, 0) - withdrawal_amount);

DROP PROCEDURE IF EXISTS lempis_add_column_if_missing;
