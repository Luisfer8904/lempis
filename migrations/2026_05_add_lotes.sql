-- ============================================================
-- Migración: agregar lotes (lempis_lotes) + columnas relacionadas
-- Compatible con MySQL 8.0.29+ (ADD COLUMN IF NOT EXISTS).
-- ============================================================

-- 1) Nueva tabla de lotes
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
  CONSTRAINT fk_lempis_lotes_tenant
    FOREIGN KEY (tenant_id) REFERENCES lempis_empresas(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_lotes_product
    FOREIGN KEY (product_id) REFERENCES lempis_productos(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2) Flag track_batches en productos
ALTER TABLE lempis_productos
  ADD COLUMN IF NOT EXISTS track_batches BOOLEAN NOT NULL DEFAULT TRUE;

-- 3) Referencia al lote desde cada línea de factura (nullable)
ALTER TABLE lempis_detalle_facturas
  ADD COLUMN IF NOT EXISTS batch_id INT NULL,
  ADD INDEX IF NOT EXISTS idx_lempis_detalle_facturas_batch (batch_id);

-- FK con SET NULL para no romper líneas si se borra el lote
ALTER TABLE lempis_detalle_facturas
  ADD CONSTRAINT fk_lempis_detalle_lote
    FOREIGN KEY (batch_id) REFERENCES lempis_lotes(id) ON DELETE SET NULL;

-- ============================================================
-- Verificación rápida
-- ============================================================
-- SHOW CREATE TABLE lempis_lotes;
-- SHOW COLUMNS FROM lempis_productos LIKE 'track_batches';
-- SHOW COLUMNS FROM lempis_detalle_facturas LIKE 'batch_id';
