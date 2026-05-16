-- ============================================================
-- Migración: Proveedores + Compras (entrada de mercadería con lotes)
-- Compatible con MySQL 8.0.29+
-- ============================================================

-- 1) Proveedores
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
  CONSTRAINT fk_lempis_proveedores_tenant
    FOREIGN KEY (tenant_id) REFERENCES lempis_empresas(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_proveedores_country
    FOREIGN KEY (country_code) REFERENCES lempis_paises(code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2) Compras
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
  INDEX idx_lempis_compras_number (number),
  UNIQUE KEY uq_lempis_compras_tenant_number (tenant_id, number),
  CONSTRAINT fk_lempis_compras_tenant
    FOREIGN KEY (tenant_id) REFERENCES lempis_empresas(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_compras_supplier
    FOREIGN KEY (supplier_id) REFERENCES lempis_proveedores(id) ON DELETE SET NULL,
  CONSTRAINT fk_lempis_compras_user
    FOREIGN KEY (received_by_user_id) REFERENCES lempis_usuarios(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3) Detalle de compras
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
  INDEX idx_lempis_detalle_compras_tenant (tenant_id),
  INDEX idx_lempis_detalle_compras_purchase (purchase_id),
  CONSTRAINT fk_lempis_detalle_compras_purchase
    FOREIGN KEY (purchase_id) REFERENCES lempis_compras(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_detalle_compras_product
    FOREIGN KEY (product_id) REFERENCES lempis_productos(id) ON DELETE SET NULL,
  CONSTRAINT fk_lempis_detalle_compras_batch
    FOREIGN KEY (batch_id) REFERENCES lempis_lotes(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Verificación:
-- SHOW TABLES LIKE 'lempis_proveedores';
-- SHOW TABLES LIKE 'lempis_compras';
-- SHOW TABLES LIKE 'lempis_detalle_compras';
