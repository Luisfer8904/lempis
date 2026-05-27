CREATE TABLE IF NOT EXISTS lempis_config_impresion (
  id INT NOT NULL AUTO_INCREMENT,
  tenant_id INT NOT NULL,
  quick_sale_format VARCHAR(30) NOT NULL DEFAULT 'thermal_receipt',
  detailed_sale_format VARCHAR(30) NOT NULL DEFAULT 'letter',
  payment_receipt_format VARCHAR(30) NOT NULL DEFAULT 'thermal_receipt',
  receipt_paper_width VARCHAR(10) NOT NULL DEFAULT '80mm',
  document_page_format VARCHAR(20) NOT NULL DEFAULT 'letter',
  thermal_printer_enabled TINYINT(1) NOT NULL DEFAULT 1,
  open_cash_drawer_on_print TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_lempis_config_impresion_tenant (tenant_id),
  CONSTRAINT fk_lempis_config_impresion_tenant
    FOREIGN KEY (tenant_id) REFERENCES lempis_empresas(id)
    ON DELETE CASCADE
);
