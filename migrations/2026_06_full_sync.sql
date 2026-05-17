-- ============================================================
-- Migración consolidada: trae cualquier DB a la versión actual
-- Compatible con MySQL 8.0.x (Oracle/RDS). 100% idempotente.
-- Es seguro correrla muchas veces — solo agrega lo que falta.
-- ============================================================

-- Helper: agrega columna solo si no existe
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

-- ===== USUARIOS: is_superadmin =====
CALL lempis_add_column_if_missing('lempis_usuarios', 'is_superadmin',
  'BOOLEAN NOT NULL DEFAULT FALSE');

-- ===== EMPRESAS (Tenant): SAR + modo facturación =====
CALL lempis_add_column_if_missing('lempis_empresas', 'cai_code', 'VARCHAR(40) NULL');
CALL lempis_add_column_if_missing('lempis_empresas', 'cai_valid_from', 'DATETIME NULL');
CALL lempis_add_column_if_missing('lempis_empresas', 'cai_valid_until', 'DATETIME NULL');
CALL lempis_add_column_if_missing('lempis_empresas', 'cai_range_start', 'INT NULL DEFAULT 1');
CALL lempis_add_column_if_missing('lempis_empresas', 'cai_range_end',   'INT NULL DEFAULT 99999999');
CALL lempis_add_column_if_missing('lempis_empresas', 'establecimiento', 'VARCHAR(3) NULL DEFAULT ''001''');
CALL lempis_add_column_if_missing('lempis_empresas', 'punto_emision',   'VARCHAR(3) NULL DEFAULT ''001''');
CALL lempis_add_column_if_missing('lempis_empresas', 'tipo_documento',  'VARCHAR(2) NULL DEFAULT ''01''');
CALL lempis_add_column_if_missing('lempis_empresas', 'next_invoice_number', 'INT NULL DEFAULT 1');
CALL lempis_add_column_if_missing('lempis_empresas', 'invoice_mode',
  'VARCHAR(30) NOT NULL DEFAULT ''simple''');
CALL lempis_add_column_if_missing('lempis_empresas', 'invoice_prefix', 'VARCHAR(20) DEFAULT ''''');

-- Migrar tenants con CAI a modo SAR
UPDATE lempis_empresas SET invoice_mode = 'sar_hn'
 WHERE cai_code IS NOT NULL AND cai_code != '' AND invoice_mode = 'simple';

-- ===== FACTURAS: SAR + pagos + crédito =====
CALL lempis_add_column_if_missing('lempis_facturas', 'payment_method',
  'ENUM(''efectivo'',''transferencia'',''tarjeta'',''credito'') NOT NULL DEFAULT ''efectivo''');
CALL lempis_add_column_if_missing('lempis_facturas', 'payment_terms_days', 'INT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_facturas', 'amount_paid',
  'DECIMAL(12,2) NOT NULL DEFAULT 0');
CALL lempis_add_column_if_missing('lempis_facturas', 'cai_code', 'VARCHAR(40) NULL');
CALL lempis_add_column_if_missing('lempis_facturas', 'cai_range_start', 'INT NULL');
CALL lempis_add_column_if_missing('lempis_facturas', 'cai_range_end',   'INT NULL');
CALL lempis_add_column_if_missing('lempis_facturas', 'cai_valid_until', 'DATETIME NULL');
CALL lempis_add_column_if_missing('lempis_facturas', 'emisor_name',    'VARCHAR(160) NULL');
CALL lempis_add_column_if_missing('lempis_facturas', 'emisor_tax_id',  'VARCHAR(40) NULL');
CALL lempis_add_column_if_missing('lempis_facturas', 'emisor_address', 'TEXT NULL');
CALL lempis_add_column_if_missing('lempis_facturas', 'receptor_name',  'VARCHAR(160) NULL');
CALL lempis_add_column_if_missing('lempis_facturas', 'receptor_tax_id','VARCHAR(40) NULL');

-- Sincronizar facturas ya pagadas
UPDATE lempis_facturas SET amount_paid = total
 WHERE status = 'paid' AND amount_paid = 0;

-- ===== PRODUCTOS: lotes =====
CALL lempis_add_column_if_missing('lempis_productos', 'track_batches',
  'BOOLEAN NOT NULL DEFAULT TRUE');

-- ===== DETALLE FACTURAS: lote =====
CALL lempis_add_column_if_missing('lempis_detalle_facturas', 'batch_id', 'INT NULL');

-- ===== Crear tablas nuevas (idempotente con CREATE TABLE IF NOT EXISTS) =====

CREATE TABLE IF NOT EXISTS lempis_lotes (
  id INT NOT NULL AUTO_INCREMENT,
  tenant_id INT NOT NULL,
  product_id INT NOT NULL,
  batch_number VARCHAR(60) NOT NULL,
  manufacturing_date DATE NULL,
  expiration_date DATE NULL,
  initial_quantity DECIMAL(12,2) NOT NULL DEFAULT 0,
  remaining_quantity DECIMAL(12,2) NOT NULL DEFAULT 0,
  cost DECIMAL(12,2) DEFAULT 0,
  supplier VARCHAR(160) NULL,
  notes TEXT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  INDEX idx_lempis_lotes_tenant (tenant_id),
  INDEX idx_lempis_lotes_product (product_id),
  INDEX idx_lempis_lotes_expiration (expiration_date),
  UNIQUE KEY uq_lempis_lotes_tenant_product_batch (tenant_id, product_id, batch_number),
  CONSTRAINT fk_lempis_lotes_tenant FOREIGN KEY (tenant_id) REFERENCES lempis_empresas(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_lotes_product FOREIGN KEY (product_id) REFERENCES lempis_productos(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS lempis_proveedores (
  id INT NOT NULL AUTO_INCREMENT,
  tenant_id INT NOT NULL,
  code VARCHAR(20) NULL,
  name VARCHAR(160) NOT NULL,
  legal_name VARCHAR(160) NULL,
  tax_id VARCHAR(40) NULL,
  contact_name VARCHAR(120) NULL,
  email VARCHAR(160) NULL,
  phone VARCHAR(40) NULL,
  address TEXT NULL,
  city VARCHAR(80) NULL,
  country_code VARCHAR(2) NULL,
  payment_terms_days INT DEFAULT 0,
  credit_limit DECIMAL(12,2) DEFAULT 0,
  price_includes_tax BOOLEAN DEFAULT FALSE,
  notes TEXT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  INDEX idx_lempis_proveedores_tenant (tenant_id),
  UNIQUE KEY uq_lempis_proveedores_tenant_code (tenant_id, code),
  UNIQUE KEY uq_lempis_proveedores_tenant_taxid (tenant_id, tax_id),
  CONSTRAINT fk_lempis_proveedores_tenant FOREIGN KEY (tenant_id) REFERENCES lempis_empresas(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_proveedores_country FOREIGN KEY (country_code) REFERENCES lempis_paises(code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS lempis_compras (
  id INT NOT NULL AUTO_INCREMENT,
  tenant_id INT NOT NULL,
  number VARCHAR(40) NOT NULL,
  supplier_invoice_number VARCHAR(60) NULL,
  supplier_id INT NULL,
  received_by_user_id INT NULL,
  issue_date DATETIME NOT NULL,
  received_at DATETIME NULL,
  due_date DATETIME NULL,
  terms_days INT DEFAULT 0,
  currency VARCHAR(3) NOT NULL DEFAULT 'HNL',
  subtotal DECIMAL(12,2) NOT NULL DEFAULT 0,
  tax_total DECIMAL(12,2) NOT NULL DEFAULT 0,
  discount_total DECIMAL(12,2) NOT NULL DEFAULT 0,
  total DECIMAL(12,2) NOT NULL DEFAULT 0,
  amount_paid DECIMAL(12,2) NOT NULL DEFAULT 0,
  status ENUM('draft','received','void') NOT NULL DEFAULT 'draft',
  notes TEXT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  INDEX idx_lempis_compras_tenant (tenant_id),
  INDEX idx_lempis_compras_supplier (supplier_id),
  UNIQUE KEY uq_lempis_compras_tenant_number (tenant_id, number),
  CONSTRAINT fk_lempis_compras_tenant FOREIGN KEY (tenant_id) REFERENCES lempis_empresas(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_compras_supplier FOREIGN KEY (supplier_id) REFERENCES lempis_proveedores(id) ON DELETE SET NULL,
  CONSTRAINT fk_lempis_compras_user FOREIGN KEY (received_by_user_id) REFERENCES lempis_usuarios(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS lempis_detalle_compras (
  id INT NOT NULL AUTO_INCREMENT,
  tenant_id INT NOT NULL,
  purchase_id INT NOT NULL,
  product_id INT NULL,
  batch_id INT NULL,
  description VARCHAR(255) NOT NULL,
  quantity DECIMAL(12,2) NOT NULL DEFAULT 1,
  unit_cost DECIMAL(12,2) NOT NULL DEFAULT 0,
  tax_rate DECIMAL(5,2) DEFAULT 0,
  tax_amount DECIMAL(12,2) DEFAULT 0,
  subtotal DECIMAL(12,2) NOT NULL DEFAULT 0,
  batch_number VARCHAR(60) NULL,
  manufacturing_date DATE NULL,
  expiration_date DATE NULL,
  new_sale_price DECIMAL(12,2) NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  INDEX idx_lempis_detalle_compras_purchase (purchase_id),
  CONSTRAINT fk_lempis_detalle_compras_purchase FOREIGN KEY (purchase_id) REFERENCES lempis_compras(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_detalle_compras_product FOREIGN KEY (product_id) REFERENCES lempis_productos(id) ON DELETE SET NULL,
  CONSTRAINT fk_lempis_detalle_compras_batch FOREIGN KEY (batch_id) REFERENCES lempis_lotes(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS lempis_pagos_facturas (
  id INT NOT NULL AUTO_INCREMENT,
  tenant_id INT NOT NULL,
  invoice_id INT NOT NULL,
  received_by_user_id INT NULL,
  amount DECIMAL(12,2) NOT NULL,
  payment_method ENUM('efectivo','transferencia','tarjeta','credito','otro') NOT NULL DEFAULT 'efectivo',
  reference VARCHAR(80) NULL,
  notes TEXT NULL,
  paid_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  INDEX idx_lempis_pagos_tenant (tenant_id),
  INDEX idx_lempis_pagos_invoice (invoice_id),
  CONSTRAINT fk_lempis_pagos_tenant FOREIGN KEY (tenant_id) REFERENCES lempis_empresas(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_pagos_invoice FOREIGN KEY (invoice_id) REFERENCES lempis_facturas(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_pagos_user FOREIGN KEY (received_by_user_id) REFERENCES lempis_usuarios(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Limpiar helper
DROP PROCEDURE IF EXISTS lempis_add_column_if_missing;

-- ============================================================
-- Listo. Verifica con:
-- SHOW COLUMNS FROM lempis_facturas LIKE 'amount_paid';
-- SHOW COLUMNS FROM lempis_empresas LIKE 'invoice_mode';
-- SHOW TABLES;
-- ============================================================
