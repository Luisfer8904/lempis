-- IVG cash expenses: individual expense records linked to day closes.
-- Compatible with MySQL 8.0.x. Idempotent.

CREATE TABLE IF NOT EXISTS ivg_gastos_caja (
  id INT AUTO_INCREMENT PRIMARY KEY,
  closure_id INT NULL,
  user_id INT NULL,
  expense_date DATETIME NOT NULL,
  category VARCHAR(80) NOT NULL DEFAULT 'General',
  description VARCHAR(180) NOT NULL,
  amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  status VARCHAR(20) NOT NULL DEFAULT 'pendiente',
  notes TEXT NULL,
  created_at DATETIME NULL,
  updated_at DATETIME NULL,
  INDEX ix_ivg_gastos_caja_closure_id (closure_id),
  INDEX ix_ivg_gastos_caja_user_id (user_id),
  INDEX ix_ivg_gastos_caja_expense_date (expense_date),
  CONSTRAINT fk_ivg_gastos_caja_closure_id FOREIGN KEY (closure_id) REFERENCES ivg_contado(id) ON DELETE SET NULL,
  CONSTRAINT fk_ivg_gastos_caja_user_id FOREIGN KEY (user_id) REFERENCES ivg_usuarios(id) ON DELETE SET NULL
);
