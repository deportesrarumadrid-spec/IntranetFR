"""Quita el fondo de una foto de una persona (rembg, modelo isnet-general-use) y guarda un PNG transparente recortado.

Se ejecuta en un proceso aparte para que la memoria del modelo se libere al terminar (el servidor tiene 2 GB).
Uso: venv/bin/python recortar_fondo.py entrada salida.png
La primera vez descarga el modelo (~180 MB) a ~/.u2net.
"""
import sys
from PIL import Image, ImageOps


def main(entrada, salida):
    from rembg import remove, new_session
    im = ImageOps.exif_transpose(Image.open(entrada)).convert('RGB')
    im.thumbnail((1600, 1600))
    out = remove(im, session=new_session('isnet-general-use'))
    caja = out.split()[3].point(lambda v: 255 if v > 128 else 0).getbbox()
    (out.crop(caja) if caja else out).save(salida)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
