"""Diseño de la historia del gol del Aficionado A (lo usa gol_live_wa.py).

Fondo rojo con franjas y la grada con bufandas, nombres de los equipos en vertical, el jugador celebrando (recortado,
sin fondo) por encima del "GOL" (ancho y en perspectiva, como un MATCH DAY), el resultado grande debajo y abajo
fecha · goleador · minuto, en una tarjeta con bordes redondeados sobre blanco. Sin jugador: solo el resultado.
"""
import io
import os
import unicodedata
from datetime import datetime
from flask import Blueprint, request, jsonify, Response

gol_bp = Blueprint('gol_ig_wa', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
W, H = 1080, 1920
F_BOLD = os.path.join(BASE_DIR, 'static', 'fonts', 'Montserrat-ExtraBold.ttf')
F_MED = os.path.join(BASE_DIR, 'static', 'fonts', 'Montserrat-Medium.ttf')
GRADA = os.path.join(BASE_DIR, 'static', 'img', 'gol', 'grada.jpg')
_MESES = ['ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE']


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.upper().split())


def quitar_verde(png):
    """Quita el fondo verde (croma) y el reflejo verde de los bordes. Devuelve RGBA recortado."""
    from PIL import Image, ImageChops
    im = Image.open(io.BytesIO(png)).convert('RGB')
    r, g, b = im.split()
    maxrb = ImageChops.lighter(r, b)
    verdor = ImageChops.subtract(g, maxrb)  # cuánto más verde que rojo/azul
    alfa = verdor.point(lambda v: 255 if v < 25 else 0 if v > 90 else int(255 * (90 - v) / 65))
    g2 = ImageChops.darker(g, ImageChops.add(maxrb, Image.new('L', g.size, 10)))  # sin halo verde
    out = Image.merge('RGBA', (r, g2, b, alfa))
    caja = alfa.point(lambda v: 255 if v > 128 else 0).getbbox()
    return out.crop(caja) if caja else out


# ---------- imagen del gol ----------

def _fuente(ruta, tam):
    from PIL import ImageFont
    return ImageFont.truetype(ruta, tam)


def _resolver(a, b):
    """Gauss para el sistema 8x8 de la perspectiva (sin numpy)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


def _perspectiva(lienzo, arriba):
    from PIL import Image
    w, h = lienzo.size
    origen = [(0, 0), (w, 0), (w, h), (0, h)]
    destino = [(w * arriba, 0), (w * (1 - arriba), 0), (w, h), (0, h)]
    filas, b = [], []
    for (x, y), (u, v) in zip(destino, origen):
        filas.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.append(u)
        filas.append([0, 0, 0, x, y, 1, -v * x, -v * y]); b.append(v)
    return lienzo.transform((w, h), Image.PERSPECTIVE, _resolver(filas, b), Image.BICUBIC)


def _texto_grande(texto, tam, ancho_final, ensanchar=1.3, arriba=0.12, desvanecer=0.0):
    from PIL import Image, ImageDraw, ImageChops
    f = _fuente(F_BOLD, tam)
    caja = ImageDraw.Draw(Image.new('L', (1, 1))).textbbox((0, 0), texto, font=f)
    lienzo = Image.new('RGBA', (caja[2] - caja[0] + 40, caja[3] - caja[1] + 40), (0, 0, 0, 0))
    ImageDraw.Draw(lienzo).text((20 - caja[0], 20 - caja[1]), texto, font=f, fill='white')
    lienzo = lienzo.resize((int(lienzo.width * ensanchar), lienzo.height))
    if arriba:
        lienzo = _perspectiva(lienzo, arriba)
    if desvanecer:
        grad = Image.linear_gradient('L').resize(lienzo.size).point(lambda v: 255 - int(v * desvanecer))
        lienzo.putalpha(ImageChops.multiply(lienzo.split()[3], grad))
    return lienzo.resize((ancho_final, int(lienzo.height * ancho_final / lienzo.width)), Image.LANCZOS)


def _fondo():
    from PIL import Image, ImageDraw, ImageFilter, ImageChops, ImageOps
    # Rojo vivo en el centro, más oscuro hacia los bordes
    radial = Image.radial_gradient('L').resize((int(W * 1.6), int(H * 1.6)))
    radial = radial.crop((int(W * 1.6 * 0.5 - W * 0.55), int(H * 1.6 * 0.5 - H * 0.35),
                          int(W * 1.6 * 0.5 - W * 0.55) + W, int(H * 1.6 * 0.5 - H * 0.35) + H))
    img = Image.composite(Image.new('RGB', (W, H), (150, 14, 20)), Image.new('RGB', (W, H), (232, 36, 42)),
                          radial.point(lambda v: min(255, int(v * 1.25))))
    # Franjas diagonales oscuras arriba a la derecha que se desvanecen hacia abajo
    capa = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    dc = ImageDraw.Draw(capa)
    for x0, ancho, alfa in [(640, 120, 80), (820, 160, 105), (1040, 140, 90), (1220, 120, 80)]:
        dc.polygon([(x0, 0), (x0 + ancho, 0), (x0 + ancho - 1400, 2040), (x0 - 1400, 2040)], fill=(60, 0, 4, alfa))
    capa = capa.filter(ImageFilter.GaussianBlur(1.5))
    mascara = ImageChops.invert(Image.linear_gradient('L').resize((W, H))).point(lambda v: max(0, min(255, int((v - 110) * 2.2))))
    capa.putalpha(ImageChops.multiply(capa.split()[3], mascara))
    img.paste(capa, (0, 0), capa)
    # Grada con bufandas desde la altura del GOL hasta abajo, teñida de rojo
    alto_g = H - 1120
    g = Image.open(GRADA).convert('L')
    g = g.crop((0, int(g.height * 0.40), g.width, g.height))
    g = g.resize((W, int(g.height * W / g.width)))
    g = g.crop((0, max(0, g.height - alto_g), W, g.height)).resize((W, alto_g))
    g = ImageOps.autocontrast(g)
    rojo = Image.merge('RGB', [g.point(lambda v: int(110 + v * 0.55)), g.point(lambda v: int(8 + v * 0.10)),
                                g.point(lambda v: int(12 + v * 0.12))])
    m = Image.linear_gradient('L').resize((W, alto_g)).point(lambda v: int(min(1, v / 110) * 175))
    img.paste(rojo, (0, H - alto_g), m)
    # Grano suave
    ruido = Image.effect_noise((W, H), 18).convert('RGB')
    return Image.blend(img, ImageChops.overlay(img, ruido), 0.25)


def _vertical(img, x, y, texto, fuente):
    from PIL import Image, ImageDraw
    capa = Image.new('RGBA', (1400, 40), (0, 0, 0, 0))
    ImageDraw.Draw(capa).text((0, 4), ' '.join(texto).replace('   ', '     '), font=fuente, fill=(255, 255, 255, 235))
    capa = capa.crop(capa.getbbox()).rotate(-90, expand=True)
    if capa.height > H - 2 * y:
        capa = capa.resize((capa.width, H - 2 * y))
    img.alpha_composite(capa, (x, y))


def imagen_gol(resultado, nuestro, rival, fecha, goleador='', minuto='', jugador_png=None):
    """PNG de la historia. jugador_png=None → gol del rival (solo resultado)."""
    from PIL import Image, ImageDraw, ImageFilter
    img = _fondo().convert('RGBA')
    f_v = _fuente(F_MED, 22)
    _vertical(img, 64, 70, nuestro, f_v)
    _vertical(img, 104, 70, rival, f_v)
    if jugador_png is not None:
        gol = _texto_grande('GOL', 600, 1260, ensanchar=1.5, arriba=0.17, desvanecer=0.30)
        gol = gol.resize((gol.width, int(gol.height * 0.92)), Image.LANCZOS)
        gol = gol.crop(gol.getbbox())
        sale = int(gol.width * 0.045)
        gol = gol.resize((W + 2 * sale, gol.height), Image.LANCZOS)
        img.alpha_composite(gol, (-sale, 1130))
        j = jugador_png.convert('RGBA')
        alto = 1150
        j = j.resize((int(j.width * alto / j.height), alto), Image.LANCZOS)
        if j.width > W - 120:
            j = j.resize((W - 120, int(j.height * (W - 120) / j.width)), Image.LANCZOS)
        x, y = (W - j.width) // 2 + 10, 180 + (alto - j.height)
        m = 80
        alfa = Image.new('L', (j.width + 2 * m, j.height + 2 * m), 0)
        alfa.paste(j.split()[3], (m, m))
        halo = Image.new('RGBA', alfa.size, (255, 255, 255, 0))
        halo.putalpha(alfa.filter(ImageFilter.GaussianBlur(16)).point(lambda v: int(v * 0.30)))
        sombra = Image.new('RGBA', alfa.size, (50, 0, 0, 0))
        sombra.putalpha(alfa.filter(ImageFilter.GaussianBlur(24)).point(lambda v: int(v * 0.55)))
        img.alpha_composite(sombra, (x - m + 24, y - m + 28))
        img.alpha_composite(halo, (x - m, y - m))
        img.alpha_composite(j, (x, y))
        y_res, ancho_res = 1480, 700
    else:
        y_res, ancho_res = 760, 900  # gol del rival: solo el resultado, en el centro
    res = _texto_grande(resultado, 420, ancho_res, ensanchar=1.15, arriba=0.0)
    sombra_r = Image.new('RGBA', res.size, (60, 0, 4, 0))
    sombra_r.putalpha(res.split()[3].point(lambda v: int(v * 0.45)))
    img.alpha_composite(sombra_r, ((W - res.width) // 2 + 8, y_res + 10))
    img.alpha_composite(res, ((W - res.width) // 2, y_res))
    d = ImageDraw.Draw(img)
    f_l = _fuente(F_BOLD, 24)
    for xx, txt, anc in ((60, fecha, 'ls'), (W // 2, goleador, 'ms'), (W - 60, minuto, 'rs')):
        if txt:
            d.text((xx, 1860), txt, font=f_l, fill='white', anchor=anc)
    # Tarjeta con bordes redondeados sobre fondo blanco
    margen, radio = 36, 60
    tarjeta = img.convert('RGB').resize((W - 2 * margen, int((W - 2 * margen) * H / W)), Image.LANCZOS)
    if tarjeta.height > H - 2 * margen:
        tarjeta = img.convert('RGB').resize((int((H - 2 * margen) * W / H), H - 2 * margen), Image.LANCZOS)
    mascara = Image.new('L', tarjeta.size, 0)
    ImageDraw.Draw(mascara).rounded_rectangle((0, 0, tarjeta.width - 1, tarjeta.height - 1), radio, fill=255)
    final = Image.new('RGB', (W, H), 'white')
    final.paste(tarjeta, ((W - tarjeta.width) // 2, (H - tarjeta.height) // 2), mascara)
    buf = io.BytesIO()
    final.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def fecha_txt(d):
    return f'{d.day:02d} {_MESES[d.month - 1]} {d.year}'


def nombre_corto(nombre):
    """Nombre del acta 'GIMENEZ DEL CASTILLO, JAIME' → 'JAIME GIMENEZ'; escrito a mano, tal cual (sin teléfonos)."""
    if ',' in str(nombre):
        from alineacion_ig_wa import nombre_corto as nc
        return nc(nombre)
    palabras = [w for w in str(nombre).split() if not any(c.isdigit() for c in w)]
    return ' '.join(palabras[:3]).upper()


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@gol_bp.route('/api/gol/vista')
def api_vista():
    """Ejemplo de la historia del gol del rival (solo resultado)."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        png = imagen_gol('1-1', 'CLUB FUENTELARREYNA A', 'RIVAL DE EJEMPLO', fecha_txt(datetime.now().date()), '', "MIN 34'")
        return Response(png, mimetype='image/png', headers={'Cache-Control': 'no-store'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]})
