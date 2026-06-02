-- IVG cash close: partial withdrawals and updated expected close formula.
-- expected = opening + cash sales - transfers - expenses - withdrawals

CREATE TABLE IF NOT EXISTS ivg_retiros_caja (
  id INT AUTO_INCREMENT PRIMARY KEY,
  closure_id INT NULL,
  user_id INT NULL,
  withdrawal_date DATETIME NOT NULL,
  recipient VARCHAR(120) NOT NULL DEFAULT 'Caja',
  description VARCHAR(180) NOT NULL,
  amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  status VARCHAR(20) NOT NULL DEFAULT 'pendiente',
  notes TEXT NULL,
  created_at DATETIME NULL,
  updated_at DATETIME NULL,
  INDEX ix_ivg_retiros_caja_closure_id (closure_id),
  INDEX ix_ivg_retiros_caja_user_id (user_id),
  INDEX ix_ivg_retiros_caja_withdrawal_date (withdrawal_date),
  CONSTRAINT fk_ivg_retiros_caja_closure_id FOREIGN KEY (closure_id) REFERENCES ivg_contado(id) ON DELETE SET NULL,
  CONSTRAINT fk_ivg_retiros_caja_user_id FOREIGN KEY (user_id) REFERENCES ivg_usuarios(id) ON DELETE SET NULL
);

UPDATE ivg_contado
SET
  total_amount = cash_amount,
  expected_close_amount = opening_amount + cash_amount - transfer_amount - expense_amount - withdrawal_amount,
  variance_amount = actual_close_amount - (opening_amount + cash_amount - transfer_amount - expense_amount - withdrawal_amount);
