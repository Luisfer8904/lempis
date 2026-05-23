-- Roles y permisos personalizables por empresa.
-- Idempotente: se puede ejecutar varias veces.

CREATE TABLE IF NOT EXISTS lempis_roles_permisos (
  id INT NOT NULL AUTO_INCREMENT,
  tenant_id INT NOT NULL,
  role_id INT NOT NULL,
  permissions TEXT NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  INDEX idx_lempis_roles_permisos_tenant (tenant_id),
  INDEX idx_lempis_roles_permisos_role (role_id),
  UNIQUE KEY uq_lempis_roles_permisos_tenant_role (tenant_id, role_id),
  CONSTRAINT fk_lempis_roles_permisos_tenant
    FOREIGN KEY (tenant_id) REFERENCES lempis_empresas(id) ON DELETE CASCADE,
  CONSTRAINT fk_lempis_roles_permisos_role
    FOREIGN KEY (role_id) REFERENCES lempis_roles(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO lempis_roles (code, name, description, created_at, updated_at)
SELECT 'cajero', 'Cajero', 'Registra ventas de mostrador y consulta productos/clientes.', NOW(), NOW()
WHERE NOT EXISTS (
  SELECT 1 FROM lempis_roles WHERE code = 'cajero'
);

UPDATE lempis_roles
SET
  name = CASE code
    WHEN 'admin' THEN 'Administrador'
    WHEN 'cajero' THEN 'Cajero'
    WHEN 'vendedor' THEN 'Vendedor'
    WHEN 'contador' THEN 'Contador'
    WHEN 'viewer' THEN 'Solo lectura'
    ELSE name
  END,
  description = CASE code
    WHEN 'admin' THEN 'Administra usuarios, configuración y operación completa.'
    WHEN 'cajero' THEN 'Registra ventas de mostrador y consulta productos/clientes.'
    WHEN 'vendedor' THEN 'Gestiona ventas y clientes.'
    WHEN 'contador' THEN 'Consulta ventas, cobros, compras y reportes.'
    WHEN 'viewer' THEN 'Solo puede consultar.'
    ELSE description
  END,
  updated_at = NOW()
WHERE code IN ('admin', 'cajero', 'vendedor', 'contador', 'viewer');
