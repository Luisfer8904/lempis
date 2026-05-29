# Runbook de producción

Este proyecto ya contiene datos operativos. Trata producción como irreversible:
no ejecutes scripts de reset, migraciones manuales ni despliegues sin backup.

## Antes de cualquier cambio

1. Entrar al servidor:
   ```bash
   ssh -i ~/.ssh/lightsail-intergan.pem ubuntu@34.195.216.217
   cd /home/ubuntu/lempis
   ```

2. Crear backup:
   ```bash
   source venv/bin/activate
   python scripts/backup_mysql.py
   ```

3. Confirmar que el archivo existe y no está vacío:
   ```bash
   ls -lh backups/*.sql | tail
   ```

4. Confirmar estado del servicio:
   ```bash
   sudo systemctl status lempis --no-pager
   ```

## Despliegue

```bash
cd /home/ubuntu/lempis
git pull origin codex/facturacion-session-fix
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart lempis
sudo systemctl status lempis --no-pager
```

## Verificación rápida

- `https://lempis.com/health` responde `{"status":"ok"}`.
- Login Lempis funciona.
- Login IGH funciona.
- Crear un cliente de prueba solo si se puede borrar de inmediato.
- Revisar logs:
  ```bash
  sudo journalctl -u lempis -n 80 --no-pager
  ```

## Scripts peligrosos

`scripts/reset_demo_data.py` borra datos operativos de Lempis. En producción está
bloqueado salvo que se defina una confirmación adicional explícita.

IGH no se borra por defecto. Para borrar IGH también haría falta:

```bash
LEMPIS_RESET_INCLUDE_IGH=DELETE_IGH_DATA
```

No uses este script en producción salvo que el objetivo sea destruir datos
después de un backup verificado.

## Recuperación de clientes IGH

Si los clientes históricos estaban en tablas antiguas, revisar primero:

- `inva-clientes`
- `inva_aves_granja_clientes`
- `lempis_clientes`
- `ivg_clientes`

No copiar datos entre tablas sin revisar campos y duplicados.
