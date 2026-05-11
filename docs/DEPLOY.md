# Deploy de Lempis en AWS Lightsail

Guía paso a paso para montar Lempis en la instancia existente de Lightsail,
**compartiendo el servidor MySQL con Invagro pero en una DB separada**.

---

## 0. Pre-requisitos

- ✅ Dominio comprado: `lempis.com` (Cloudflare)
- ✅ Instancia Lightsail Ubuntu 22.04 (2 GB RAM) — ya activa
- ✅ DB Lightsail MySQL 8.0 — ya activa
- ⚠️ **Activar snapshots automáticos** del DB antes de mezclar datos

### Activar backups de la DB (importante)

Lightsail → Bases de datos → Database-1 → pestaña **Instantáneas** →
"Habilitar instantáneas automáticas" → frecuencia diaria. ~$1-3/mes.

---

## 1. Crear DB y usuario aislados para Lempis

Conéctate al MySQL desde MySQL Workbench (ya lo tienes configurado).
Pega y ejecuta:

```sql
-- 1) Crear base de datos
CREATE DATABASE IF NOT EXISTS lempis_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- 2) Crear usuario solo para Lempis (NO compartir con Invagro)
CREATE USER IF NOT EXISTS 'lempis_user'@'%' IDENTIFIED BY 'CAMBIA-ESTE-PASSWORD-FUERTE';

-- 3) Dar permisos solo a su DB
GRANT ALL PRIVILEGES ON lempis_db.* TO 'lempis_user'@'%';
FLUSH PRIVILEGES;

-- 4) Verificar
SHOW DATABASES;
SHOW GRANTS FOR 'lempis_user'@'%';
```

> **Tip de seguridad:** genera un password fuerte con `openssl rand -base64 24`
> y guárdalo en un gestor de contraseñas. NO lo compartas con Invagro.

---

## 2. Subir el código a la instancia

Desde tu Mac (en local):

```bash
cd "/Users/luisrivera/Documents/Saas Honduras"

# Comprimir sin venv ni archivos basura
tar --exclude='venv' --exclude='__pycache__' --exclude='.git' \
    --exclude='instance' --exclude='.env' \
    -czf /tmp/lempis.tar.gz .

# Subir a la instancia (cambia la ruta de la key)
scp -i ~/.ssh/LightsailKey.pem /tmp/lempis.tar.gz \
    ubuntu@34.195.216.217:/tmp/

# Entrar a la instancia
ssh -i ~/.ssh/LightsailKey.pem ubuntu@34.195.216.217
```

Ya dentro de la instancia:

```bash
# Crear carpeta del proyecto
sudo mkdir -p /var/www/lempis
sudo chown ubuntu:ubuntu /var/www/lempis
cd /var/www/lempis
tar -xzf /tmp/lempis.tar.gz

# Crear venv e instalar
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 3. Crear archivo `.env` en producción

```bash
cd /var/www/lempis
cp .env.example .env
nano .env
```

Edita estos valores:

```
FLASK_ENV=production
SECRET_KEY=PEGA-UN-RESULTADO-DE-openssl-rand-base64-48
APP_HOST=127.0.0.1
APP_PORT=5001

DATABASE_URL=mysql://lempis_user:TU-PASSWORD-DB@ls-2bfaa22cfdc7ff048e57bf0cc7680cde22b2bb84.cmpokeawm2j7.us-east-1.rds.amazonaws.com:3306/lempis_db

APP_NAME=Lempis
APP_DOMAIN=lempis.com
SESSION_COOKIE_SECURE=True

# Email — usa lo mismo que Invagro o un Gmail dedicado
MAIL_USERNAME=tu-correo@gmail.com
MAIL_PASSWORD=tu-app-password
MAIL_DEFAULT_SENDER=no-reply@lempis.com
```

Genera el SECRET_KEY así:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## 4. Inicializar tablas + seed

```bash
cd /var/www/lempis
source venv/bin/activate
python scripts/init_db.py
# (opcional) crear tenant demo
python scripts/create_demo_tenant.py
```

Deberías ver:
```
→ Creando tablas...
→ Seedeando países...
→ Seedeando roles...
→ Seedeando planes...
✅ Base de datos lista.
```

---

## 5. Crear servicio systemd para Gunicorn

```bash
sudo nano /etc/systemd/system/lempis.service
```

Pega:

```ini
[Unit]
Description=Lempis SaaS Gunicorn
After=network.target

[Service]
User=ubuntu
Group=www-data
WorkingDirectory=/var/www/lempis
Environment="PATH=/var/www/lempis/venv/bin"
EnvironmentFile=/var/www/lempis/.env
ExecStart=/var/www/lempis/venv/bin/gunicorn \
    --workers 3 \
    --bind 127.0.0.1:5001 \
    --access-logfile /var/log/lempis-access.log \
    --error-logfile /var/log/lempis-error.log \
    wsgi:application
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Activar y arrancar:

```bash
sudo touch /var/log/lempis-access.log /var/log/lempis-error.log
sudo chown ubuntu:ubuntu /var/log/lempis-*.log

sudo systemctl daemon-reload
sudo systemctl enable lempis
sudo systemctl start lempis
sudo systemctl status lempis     # debería decir "active (running)"
```

Probar localmente desde la instancia:
```bash
curl -I http://127.0.0.1:5001/
# debería responder 200 OK
```

---

## 6. Configurar Nginx con vhost para lempis.com

```bash
sudo nano /etc/nginx/sites-available/lempis.com
```

Pega:

```nginx
server {
    listen 80;
    server_name lempis.com www.lempis.com;

    client_max_body_size 10M;

    location /static/ {
        alias /var/www/lempis/static/;
        expires 30d;
        access_log off;
    }

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Activar:

```bash
sudo ln -s /etc/nginx/sites-available/lempis.com /etc/nginx/sites-enabled/
sudo nginx -t                # debe decir "syntax is ok"
sudo systemctl reload nginx
```

---

## 7. DNS en Cloudflare

En Cloudflare → lempis.com → DNS → agregar:

| Tipo | Nombre | Contenido            | Proxy   |
|------|--------|----------------------|---------|
| A    | @      | 34.195.216.217       | ✅ on    |
| A    | www    | 34.195.216.217       | ✅ on    |

Esperar 1-5 minutos. Verifica:
```bash
dig lempis.com +short
# debe devolver la IP de Lightsail o de Cloudflare proxy
```

---

## 8. SSL gratis con Let's Encrypt

**Importante:** primero pon Cloudflare en modo "DNS only" (nube gris) para
el certificado, luego puedes volver a activar el proxy.

```bash
sudo apt update
sudo apt install -y certbot python3-certbot-nginx

sudo certbot --nginx -d lempis.com -d www.lempis.com \
    --redirect --agree-tos -m tu-correo@gmail.com --no-eff-email
```

Si todo va bien, el sitio queda en HTTPS automáticamente.
Certbot renueva solo cada 60 días.

Verifica renovación:
```bash
sudo certbot renew --dry-run
```

Después puedes volver a poner Cloudflare en "Proxied" (nube naranja) y en
SSL/TLS → modo **Full (strict)**.

---

## 9. Abrir puertos en Lightsail

En Lightsail → Instancia Ubuntu-2 → Redes → Puertos:
- HTTP 80 → permitir
- HTTPS 443 → permitir
- SSH 22 → permitir solo desde tu IP (más seguro)

---

## 10. Verificación final

- [ ] https://lempis.com → carga landing
- [ ] https://lempis.com/auth/signup → crear tenant funciona
- [ ] https://lempis.com/auth/login → login con demo@example.com / demo1234
- [ ] https://lempis.com/app/ → dashboard
- [ ] `sudo systemctl status lempis` → active
- [ ] `tail -f /var/log/lempis-error.log` → sin errores

---

## 🚨 Cómo actualizar Lempis en el futuro

```bash
# En tu Mac, después de cambios:
cd "/Users/luisrivera/Documents/Saas Honduras"
tar --exclude='venv' --exclude='__pycache__' --exclude='.git' \
    --exclude='instance' --exclude='.env' \
    -czf /tmp/lempis.tar.gz .
scp -i ~/.ssh/LightsailKey.pem /tmp/lempis.tar.gz ubuntu@34.195.216.217:/tmp/

# En la instancia:
ssh -i ~/.ssh/LightsailKey.pem ubuntu@34.195.216.217
cd /var/www/lempis
tar -xzf /tmp/lempis.tar.gz   # sobreescribe código
source venv/bin/activate
pip install -r requirements.txt   # por si agregaste libs
sudo systemctl restart lempis
```

> **Próximo paso recomendado:** configurar deploy con `git pull` desde un
> repo privado (GitHub) en lugar de subir tar.gz manualmente.

---

## 🐛 Troubleshooting rápido

**"502 Bad Gateway" en el browser:**
```bash
sudo systemctl status lempis
sudo journalctl -u lempis -n 50
tail -100 /var/log/lempis-error.log
```

**MySQL: "Access denied for user 'lempis_user'":**
Revisar el password en `.env` y que el GRANT esté bien con `SHOW GRANTS FOR 'lempis_user'@'%';`

**Memoria llena (instancia de 2GB):**
Reduce workers de Gunicorn de 3 → 2:
```ini
--workers 2
```

**Cloudflare redirige en loop:**
SSL/TLS en Cloudflare debe estar en "Full (strict)", NO en "Flexible".
