-- ============================================================
-- Migracion: retiros parciales de caja para Lempis.
-- El efectivo esperado queda:
--   apertura + ventas efectivo + abonos efectivo - gastos efectivo - retiros
-- ============================================================

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

CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'withdrawals_amount', 'DECIMAL(12,2) NOT NULL DEFAULT 0 AFTER `expenses_amount`');

CREATE TABLE IF NOT EXISTS lempis_retiros_caja (
  id INT NOT NULL AUTO_INCREMENT,
  tenant_id INT NOT NULL,
  closure_id INT NULL,
  branch_id INT NULL,
  user_id INT NULL,
  withdrawal_date DATETIME NOT NULL,
  recipient VARCHAR(120) NOT NULL,
  description VARCHAR(180) NOT NULL,
  amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  notes TEXT NULL,
  created_at DATETIME NULL,
  updated_at DATETIME NULL,
  PRIMARY KEY (id),
  INDEX ix_lempis_retiros_caja_tenant_id (tenant_id),
  INDEX ix_lempis_retiros_caja_closure_id (closure_id),
  INDEX ix_lempis_retiros_caja_branch_id (branch_id),
  INDEX ix_lempis_retiros_caja_user_id (user_id),
  INDEX ix_lempis_retiros_caja_withdrawal_date (withdrawal_date),
  CONSTRAINT fk_lempis_retiros_caja_tenant FOREIGN KEY (tenant_id) REFERENCES lempis_tenants(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_retiros_caja_closure FOREIGN KEY (closure_id) REFERENCES lempis_cierres_diarios(id) ON DELETE SET NULL,
  CONSTRAINT fk_lempis_retiros_caja_branch FOREIGN KEY (branch_id) REFERENCES lempis_sedes(id) ON DELETE SET NULL,
  CONSTRAINT fk_lempis_retiros_caja_user FOREIGN KEY (user_id) REFERENCES lempis_usuarios(id) ON DELETE SET NULL
);

UPDATE lempis_retiros_caja r
  JOIN lempis_cierres_diarios c
    ON c.tenant_id = r.tenant_id
   AND c.closure_date = DATE(r.withdrawal_date)
   SET r.closure_id = c.id
 WHERE r.closure_id IS NULL;

UPDATE lempis_cierres_diarios c
LEFT JOIN (
  SELECT
    r.tenant_id,
    DATE(r.withdrawal_date) AS d,
    SUM(r.amount) AS total
  FROM lempis_retiros_caja r
  GROUP BY r.tenant_id, DATE(r.withdrawal_date)
) rt
  ON rt.tenant_id = c.tenant_id
 AND rt.d = c.closure_date
SET
  c.withdrawals_amount  = COALESCE(rt.total, 0),
  c.expected_cash_amount = c.opening_amount
                         + c.cash_sales_amount
                         + c.receivable_cash_amount
                         - c.expenses_amount
                         - COALESCE(rt.total, 0),
  c.variance_amount      = c.delivered_cash_amount
                         - (c.opening_amount
                            + c.cash_sales_amount
                            + c.receivable_cash_amount
                            - c.expenses_amount
                            - COALESCE(rt.total, 0));

DROP PROCEDURE IF EXISTS lempis_add_column_if_missing;
