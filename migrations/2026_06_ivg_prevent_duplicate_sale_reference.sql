-- IVG: prevent new duplicate invoice references at database level.
-- Existing historical duplicates are not modified by this migration.

DROP TRIGGER IF EXISTS bi_ivg_ventas_reference_unique;
DROP TRIGGER IF EXISTS bu_ivg_ventas_reference_unique;

DELIMITER //

CREATE TRIGGER bi_ivg_ventas_reference_unique
BEFORE INSERT ON ivg_ventas
FOR EACH ROW
BEGIN
  SET NEW.reference_number = NULLIF(TRIM(NEW.reference_number), '');
  IF NEW.reference_number IS NOT NULL
     AND EXISTS (
       SELECT 1
       FROM ivg_ventas
       WHERE reference_number IS NOT NULL
         AND LOWER(reference_number) = LOWER(NEW.reference_number)
       LIMIT 1
     )
  THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Referencia de factura IVG duplicada';
  END IF;
END//

CREATE TRIGGER bu_ivg_ventas_reference_unique
BEFORE UPDATE ON ivg_ventas
FOR EACH ROW
BEGIN
  SET NEW.reference_number = NULLIF(TRIM(NEW.reference_number), '');
  IF NEW.reference_number IS NOT NULL
     AND EXISTS (
       SELECT 1
       FROM ivg_ventas
       WHERE id <> OLD.id
         AND reference_number IS NOT NULL
         AND LOWER(reference_number) = LOWER(NEW.reference_number)
       LIMIT 1
     )
  THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Referencia de factura IVG duplicada';
  END IF;
END//

DELIMITER ;
