"""
Crea un backup SQL de la base configurada en DATABASE_URL.

Uso:
  python scripts/backup_mysql.py

El archivo se guarda en ./backups con timestamp. Requiere `mysqldump`.
"""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]


def _database_url() -> str:
    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SystemExit("DATABASE_URL no está configurado.")
    if url.startswith("mysql+pymysql://"):
        url = url.replace("mysql+pymysql://", "mysql://", 1)
    if not url.startswith("mysql://"):
        raise SystemExit("backup_mysql.py solo soporta MySQL.")
    return url


def main() -> None:
    parsed = urlparse(_database_url())
    database = parsed.path.lstrip("/")
    if not database:
        raise SystemExit("DATABASE_URL no incluye nombre de base de datos.")

    backup_dir = ROOT / "backups"
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_path = backup_dir / f"{database}_{timestamp}.sql"

    env = os.environ.copy()
    env["MYSQL_PWD"] = unquote(parsed.password or "")
    command = [
        "mysqldump",
        "--single-transaction",
        "--routines",
        "--triggers",
        "--set-gtid-purged=OFF",
        "-h",
        parsed.hostname or "localhost",
        "-P",
        str(parsed.port or 3306),
        "-u",
        unquote(parsed.username or ""),
        database,
    ]

    with output_path.open("wb") as output:
        result = subprocess.run(command, stdout=output, stderr=subprocess.PIPE, env=env, check=False)

    if result.returncode != 0:
        output_path.unlink(missing_ok=True)
        sys.stderr.write(result.stderr.decode("utf-8", errors="replace"))
        raise SystemExit(result.returncode)

    print(f"Backup creado: {output_path}")


if __name__ == "__main__":
    main()
