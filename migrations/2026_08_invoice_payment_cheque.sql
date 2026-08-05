ALTER TABLE lempis_pagos_facturas
  MODIFY payment_method ENUM('efectivo','transferencia','tarjeta','credito','cheque','otro') NOT NULL DEFAULT 'efectivo';
