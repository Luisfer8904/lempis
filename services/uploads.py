"""
Manejo de subida de archivos (imágenes de productos, logos, etc.).
Usa Pillow para redimensionar y convertir a JPEG con calidad razonable.

Storage: static/uploads/products/<tenant_id>/<product_id>.jpg

En producción esto se sirve por Nginx desde la misma carpeta static.
Para escala mayor, migrar a S3/R2 cambiando solo este módulo.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from flask import current_app
from PIL import Image


ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}
MAX_DIMENSIONS = (1200, 1200)   # max width/height in pixels
JPEG_QUALITY = 85


def is_allowed_image(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def _uploads_dir(*subpath: str) -> Path:
    """Path absoluto en /static/uploads/<subpath>"""
    base = Path(current_app.root_path) / "static" / "uploads"
    full = base.joinpath(*subpath)
    full.mkdir(parents=True, exist_ok=True)
    return full


def save_product_image(file_storage, tenant_id: int, product_id: int) -> Optional[str]:
    """
    Guarda y redimensiona la imagen de un producto.
    Retorna la URL relativa pública o None si no hubo archivo válido.
    """
    if not file_storage or not file_storage.filename:
        return None
    if not is_allowed_image(file_storage.filename):
        return None

    folder = _uploads_dir("products", str(tenant_id))
    filename = f"{product_id}.jpg"
    filepath = folder / filename

    # Abrir, convertir a RGB, redimensionar, guardar como JPEG
    img = Image.open(file_storage.stream)
    if img.mode in ("RGBA", "LA", "P"):
        # Aplanar transparencia con fondo blanco
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        if img.mode == "RGBA":
            background.paste(img, mask=img.split()[-1])
        else:
            background.paste(img)
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    img.thumbnail(MAX_DIMENSIONS, Image.LANCZOS)
    img.save(filepath, "JPEG", quality=JPEG_QUALITY, optimize=True)

    # URL pública (servida por Flask en dev, Nginx en prod)
    return f"/static/uploads/products/{tenant_id}/{filename}"


def delete_product_image(image_url: Optional[str]) -> None:
    """Elimina la imagen del disco si pertenece a nuestra carpeta de uploads."""
    if not image_url or "/static/uploads/products/" not in image_url:
        return
    # image_url es algo como /static/uploads/products/3/47.jpg
    rel = image_url.lstrip("/")
    full = Path(current_app.root_path) / rel
    if full.exists() and full.is_file():
        try:
            full.unlink()
        except OSError:
            pass  # no romper la operación si falla el delete
