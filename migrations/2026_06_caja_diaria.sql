-- ============================================================
-- Migración: Caja diaria — verificación de esquema + recálculo de diferencias.
-- Compatible MySQL 8.0.x. Idempotente.
-- Garantiza que existan las columnas requeridas y recalcula
-- variance_amount con la fórmula oficial:
--   diferencia = delivered_cash_amount - expected_cash_amount
-- ============================================================

-- Helper para agregar columnas faltantes sin romper si ya existen
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

-- ============================================================
-- Tabla: lempis_cierres_diarios  — columnas requeridas
-- ============================================================
CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'status',                     'VARCHAR(20) NOT NULL DEFAULT "closed"');
CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'opening_amount',             'DECIMAL(12,2) NOT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'cash_sales_amount',          'DECIMAL(12,2) NOT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'transfer_sales_amount',      'DECIMAL(12,2) NOT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'card_sales_amount',          'DECIMAL(12,2) NOT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'credit_sales_amount',        'DECIMAL(12,2) NOT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'receivable_cash_amount',     'DECIMAL(12,2) NOT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'receivable_transfer_amount', 'DECIMAL(12,2) NOT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'receivable_card_amount',     'DECIMAL(12,2) NOT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'expenses_amount',            'DECIMAL(12,2) NOT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'expected_cash_amount',       'DECIMAL(12,2) NOT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'actual_cash_amount',         'DECIMAL(12,2) NOT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'delivered_cash_amount',      'DECIMAL(12,2) NOT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'variance_amount',            'DECIMAL(12,2) NOT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_cierres_diarios', 'notes',                      'TEXT NULL');

-- ============================================================
-- Tabla: lempis_gastos_caja  — columnas requeridas
-- ============================================================
CALL lempis_add_column_if_missing('lempis_gastos_caja', 'closure_id',     'INT NULL');
CALL lempis_add_column_if_missing('lempis_gastos_caja', 'branch_id',     'INT NULL');
CALL lempis_add_column_if_missing('lempis_gastos_caja', 'user_id',       'INT NULL');
CALL lempis_add_column_if_missing('lempis_gastos_caja', 'expense_date',  'DATETIME NOT NULL');
CALL lempis_add_column_if_missing('lempis_gastos_caja', 'category',      'VARCHAR(80) NOT NULL DEFAULT "General"');
CALL lempis_add_column_if_missing('lempis_gastos_caja', 'description',   'VARCHAR(180) NOT NULL');
CALL lempis_add_column_if_missing('lempis_gastos_caja', 'payment_method',"ENUM('efectivo','transferencia','tarjeta','otro') NOT NULL DEFAULT 'efectivo'");
CALL lempis_add_column_if_missing('lempis_gastos_caja', 'amount',        'DECIMAL(12,2) NOT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_gastos_caja', 'notes',         'TEXT NULL');

-- ============================================================
-- Vincular gastos en efectivo a su cierre del día (si quedaron huérfanos)
-- ============================================================
UPDATE lempis_gastos_caja g
  JOIN lempis_cierres_diarios c
    ON c.tenant_id = g.tenant_id
   AND c.closure_date = DATE(g.expense_date)
   SET g.closure_id = c.id
 WHERE g.payment_method = 'efectivo'
   AND g.closure_id IS NULL;

-- ============================================================
-- Recalcular expenses_amount, expected_cash_amount y variance_amount
-- con la fórmula oficial:
--   expected = opening + cash_sales + receivable_cash - expenses
--   variance = delivered - expected
-- ============================================================
UPDATE lempis_cierres_diarios c
LEFT JOIN (
  SELECT
    g.tenant_id,
    DATE(g.expense_date) AS d,
    SUM(g.amount)         AS total
  FROM lempis_gastos_caja g
  WHERE g.payment_method = 'efectivo'
  GROUP BY g.tenant_id, DATE(g.expense_date)
) ex
  ON ex.tenant_id = c.tenant_id
 AND ex.d         = c.closure_date
SET
  c.expenses_amount      = COALESCE(ex.total, 0),
  c.expected_cash_amount = c.opening_amount
                         + c.cash_sales_amount
                         + c.receivable_cash_amount
                         - COALESCE(ex.total, 0),
  c.variance_amount      = c.delivered_cash_amount
                         - (c.opening_amount
                            + c.cash_sales_amount
                            + c.receivable_cash_amount
                            - COALESCE(ex.total, 0));

-- Limpieza del helper
DROP PROCEDURE IF EXISTS lempis_add_column_if_missing;

-- ============================================================
-- Verificación rápida
-- ============================================================
SELECT
  closure_date,
  opening_amount,
  cash_sales_amount,
  expenses_amount,
  expected_cash_amount,
  delivered_cash_amount,
  variance_amount,
  CASE
    WHEN variance_amount = 0 THEN 'cuadrado'
    WHEN variance_amount > 0 THEN 'sobrante'
    ELSE 'faltante'
  END AS estado
FROM lempis_cierres_diarios
ORDER BY closure_date DESC
LIMIT 10;
