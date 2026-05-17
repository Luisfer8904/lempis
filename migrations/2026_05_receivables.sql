-- ============================================================
-- Migración: Cuentas por Cobrar
-- ============================================================

-- 1) amount_paid en facturas
ALTER TABLE lempis_facturas
  ADD COLUMN IF NOT EXISTS amount_paid DECIMAL(12,2) NOT NULL DEFAULT 0;

-- 2) Tabla de pagos / abonos a facturas
CREATE TABLE IF NOT EXISTS lempis_pagos_facturas (
  id INT NOT NULL AUTO_INCREMENT,
  tenant_id INT NOT NULL,
  invoice_id INT NOT NULL,
  received_by_user_id INT NULL,
  amount DECIMAL(12,2) NOT NULL,
  payment_method ENUM('efectivo','transferencia','tarjeta','credito','otro')
    NOT NULL DEFAULT 'efectivo',
  reference VARCHAR(80) NULL,
  notes TEXT NULL,
  paid_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  INDEX idx_lempis_pagos_tenant (tenant_id),
  INDEX idx_lempis_pagos_invoice (invoice_id),
  INDEX idx_lempis_pagos_paid_at (paid_at),
  CONSTRAINT fk_lempis_pagos_tenant
    FOREIGN KEY (tenant_id) REFERENCES lempis_empresas(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_pagos_invoice
    FOREIGN KEY (invoice_id) REFERENCES lempis_facturas(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_pagos_user
    FOREIGN KEY (received_by_user_id) REFERENCES lempis_usuarios(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3) Marcar como pagadas todas las facturas que ya tenían status='paid' (sincronizar amount_paid)
UPDATE lempis_facturas
   SET amount_paid = total
 WHERE status = 'paid' AND amount_paid = 0;

-- Verificación:
-- SHOW COLUMNS FROM lempis_facturas LIKE 'amount_paid';
-- SHOW TABLES LIKE 'lempis_pagos_facturas';
