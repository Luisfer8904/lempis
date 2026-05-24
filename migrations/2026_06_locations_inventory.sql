-- Sedes, bodegas e inventario por ubicación.
-- Preferido en servidor: python scripts/apply_locations_migration.py

CREATE TABLE IF NOT EXISTS lempis_sedes (
  id INT NOT NULL AUTO_INCREMENT,
  tenant_id INT NOT NULL,
  name VARCHAR(120) NOT NULL,
  code VARCHAR(30) NULL,
  city VARCHAR(80) NULL,
  address TEXT NULL,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  INDEX idx_lempis_sedes_tenant (tenant_id),
  UNIQUE KEY uq_lempis_sedes_tenant_name (tenant_id, name),
  CONSTRAINT fk_lempis_sedes_tenant FOREIGN KEY (tenant_id) REFERENCES lempis_empresas(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS lempis_bodegas (
  id INT NOT NULL AUTO_INCREMENT,
  tenant_id INT NOT NULL,
  branch_id INT NOT NULL,
  name VARCHAR(120) NOT NULL,
  code VARCHAR(30) NULL,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  INDEX idx_lempis_bodegas_tenant (tenant_id),
  INDEX idx_lempis_bodegas_branch (branch_id),
  UNIQUE KEY uq_lempis_bodegas_tenant_branch_name (tenant_id, branch_id, name),
  CONSTRAINT fk_lempis_bodegas_tenant FOREIGN KEY (tenant_id) REFERENCES lempis_empresas(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_bodegas_branch FOREIGN KEY (branch_id) REFERENCES lempis_sedes(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS lempis_inventario_bodegas (
  id INT NOT NULL AUTO_INCREMENT,
  tenant_id INT NOT NULL,
  warehouse_id INT NOT NULL,
  product_id INT NOT NULL,
  batch_id INT NULL,
  quantity DECIMAL(12,2) NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  INDEX idx_lempis_inv_bodega_tenant (tenant_id),
  INDEX idx_lempis_inv_bodega_warehouse (warehouse_id),
  INDEX idx_lempis_inv_bodega_product (product_id),
  INDEX idx_lempis_inv_bodega_batch (batch_id),
  UNIQUE KEY uq_lempis_inv_bodega_producto_lote (tenant_id, warehouse_id, product_id, batch_id),
  CONSTRAINT fk_lempis_inv_bodega_tenant FOREIGN KEY (tenant_id) REFERENCES lempis_empresas(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_inv_bodega_warehouse FOREIGN KEY (warehouse_id) REFERENCES lempis_bodegas(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_inv_bodega_product FOREIGN KEY (product_id) REFERENCES lempis_productos(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_inv_bodega_batch FOREIGN KEY (batch_id) REFERENCES lempis_lotes(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS lempis_movimientos_inventario (
  id INT NOT NULL AUTO_INCREMENT,
  tenant_id INT NOT NULL,
  product_id INT NULL,
  batch_id INT NULL,
  source_warehouse_id INT NULL,
  target_warehouse_id INT NULL,
  quantity DECIMAL(12,2) NOT NULL,
  movement_type VARCHAR(30) NOT NULL,
  reference VARCHAR(80) NULL,
  notes TEXT NULL,
  moved_at DATETIME NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  INDEX idx_lempis_mov_inv_tenant (tenant_id),
  INDEX idx_lempis_mov_inv_product (product_id),
  CONSTRAINT fk_lempis_mov_inv_tenant FOREIGN KEY (tenant_id) REFERENCES lempis_empresas(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_mov_inv_product FOREIGN KEY (product_id) REFERENCES lempis_productos(id) ON DELETE SET NULL,
  CONSTRAINT fk_lempis_mov_inv_batch FOREIGN KEY (batch_id) REFERENCES lempis_lotes(id) ON DELETE SET NULL,
  CONSTRAINT fk_lempis_mov_inv_source FOREIGN KEY (source_warehouse_id) REFERENCES lempis_bodegas(id) ON DELETE SET NULL,
  CONSTRAINT fk_lempis_mov_inv_target FOREIGN KEY (target_warehouse_id) REFERENCES lempis_bodegas(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
