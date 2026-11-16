"""Historias de Instagram con las clasificaciones (de momento, solo prueba por WhatsApp a Rubén Pozón).

- Senior A (Aficionado A): modelo rojo con las 10 primeras filas (escudo, equipo, J, Pts); si vamos por debajo del 10º,
  nuestra fila se añade debajo.
- Resto: una historia por categoría (Aficionado B, Juveniles, Cadetes, Infantiles, Alevines, Benjamines, Prebenjamines)
  con un bloque por equipo: a la izquierda división, grupo y nombre corto (JUV A); a la derecha 5 filas alrededor de
  nuestra posición. Nosotros en blanco, el resto en rojo; posición en verde los 2 primeros y rojo oscuro los 3 últimos.
Los datos salen de la caché de la Federación que ya guarda la intranet (static/data/rffm_cache.json).
"""
import io
import os
import re
import random
import unicodedata
from datetime import date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
F_GOTHIC = os.path.join(BASE_DIR, 'static', 'fonts', 'LeagueGothic.ttf')
F_NEGRITA = os.path.join(BASE_DIR, 'static', 'fonts', 'Montserrat-ExtraBold.ttf')
F_MEDIA = os.path.join(BASE_DIR, 'static', 'fonts', 'Montserrat-Medium.ttf')
ESCUDO_CLUB = os.path.join(BASE_DIR, 'static', 'ESCUDO SIN FONDO.png')
W, H = 1080, 1920
ROJO = (227, 30, 36)
ROJO_OSCURO = (128, 10, 16)
VERDE = (36, 128, 58)
NEGRO = (25, 25, 28)

ORDEN = ['AFICIONADO', 'JUVENIL', 'CADETE', 'INFANTIL', 'ALEVIN', 'BENJAMIN', 'PREBENJAMIN']
TITULO = {'AFICIONADO': 'AFICIONADOS', 'JUVENIL': 'JUVENILES', 'CADETE': 'CADETES', 'INFANTIL': 'INFANTILES',
          'ALEVIN': 'ALEVINES', 'BENJAMIN': 'BENJAMINES', 'PREBENJAMIN': 'PREBENJAMINES'}
CORTO = {'AFICIONADO': 'AFI', 'JUVENIL': 'JUV', 'CADETE': 'CAD', 'INFANTIL': 'INF', 'ALEVIN': 'ALE',
         'BENJAMIN': 'BEN', 'PREBENJAMIN': 'PREBEN'}
LARGO = {'AFICIONADO': 'AFICIONADO', 'JUVENIL': 'JUVENIL', 'CADETE': 'CADETE', 'INFANTIL': 'INFANTIL',
         'ALEVIN': 'ALEVÍN', 'BENJAMIN': 'BENJAMÍN', 'PREBENJAMIN': 'PREBENJAMÍN'}
ROMANOS = ['', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII', 'XIII', 'XIV', 'XV',
           'XVI', 'XVII', 'XVIII', 'XIX', 'XX']


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.upper().split())


def _f(ruta, tam):
    from PIL import ImageFont
    return ImageFont.truetype(ruta, tam)


def _encajar(d, texto, ruta, tam, ancho, minimo=14):
    while tam > minimo and d.textlength(texto, font=_f(ruta, tam)) > ancho:
        tam -= 1
    return _f(ruta, tam)


def _png(img):
    buf = io.BytesIO()
    img.convert('RGB').save(buf, format='PNG', optimize=True)
    return buf.getvalue()


# ---------- datos ----------

def _es_nuestro(nombre):
    return 'FUENTELARREYNA' in _norm(nombre)


def _letra(nombre):
    m = re.search(r'"\s*([A-Z])\s*"', nombre or '')
    return m.group(1) if m else ''


def _categoria(comp):
    n = _norm(comp)
    for c in ('PREBENJAMIN', 'BENJAMIN', 'ALEVIN', 'INFANTIL', 'CADETE', 'JUVENIL', 'AFICIONADO'):
        if c in n:
            return c
    return None


def _division(comp):
    """'PRIMERA JUVENIL - Grupo 8' → ('PRIMERA', 8); 'DIVISIÓN DE HONOR BENJAMÍN F-7 - Grupo 3 - …' → ('DIVISIÓN DE HONOR', 3)."""
    txt = ' '.join(str(comp or '').upper().split())
    m = re.search(r'GRUPO\s*(\d+)', txt)
    grupo = int(m.group(1)) if m else None
    cat = _categoria(comp)
    palabras = txt.split(' - ')[0].split()
    antes, despues, visto = [], [], False
    for p in palabras:
        if cat and _norm(p).startswith(cat[:6]):
            visto = True
            continue
        (despues if visto else antes).append(p)
    antes = [p for p in antes if p != 'FEMENINO']
    despues = [p for p in despues if p not in ('F-7', 'F7', 'FEMENINO')]
    div = ' '.join(antes) or ' '.join(despues)
    if 'FEMENINO' in txt:
        div = (div + ' FEM').strip()
    return div.replace('SEGUNDA FASE ', '2ª FASE '), grupo


def _limpio(nombre):
    """Nombre del rival como en el ejemplo: sin puntos ('A.D. FUNDACIÓN' → 'AD FUNDACIÓN')."""
    s = re.sub(r'\b((?:[A-ZÑ]\.){2,})', lambda m: m.group(1).replace('.', '') + ' ', str(nombre or '').upper())
    s = re.sub(r'"\s*([A-Z])\s*"', r' "\1"', s)
    return ' '.join(s.replace('.', ' ').split())


def temporada(hoy=None):
    hoy = hoy or date.today()
    a = hoy.year if hoy.month >= 7 else hoy.year - 1
    return f'{a % 100:02d}-{(a + 1) % 100:02d}'


def equipos(cache):
    """[{cat, letra, nuestro, comp, division, grupo, clasif, pos, f7, fem}] — uno por cada equipo nuestro en cada grupo."""
    out = []
    for e in cache.get('equipos', []):
        comp = e.get('nombre', '')
        cat = _categoria(comp)
        clasif = e.get('clasificacion') or []
        if not cat or not clasif:
            continue
        div, grupo = _division(comp)
        f7 = 'F-7' in comp.upper() or 'F7' in comp.upper()
        fem = 'FEMENINO' in comp.upper()
        for i, r in enumerate(clasif):
            if _es_nuestro(r.get('equipo')):
                out.append({'cat': cat, 'letra': _letra(r['equipo']), 'nuestro': r['equipo'], 'comp': comp,
                            'division': div, 'grupo': grupo, 'clasif': clasif, 'idx': i, 'f7': f7, 'fem': fem})
    out.sort(key=lambda x: (ORDEN.index(x['cat']), x['fem'], x['f7'], x['letra'] or 'Z'))
    return out


def etiqueta(eq):
    """Nombre corto de la izquierda: JUV A, ALE F7 B, ALE FEM…"""
    base = CORTO[eq['cat']]
    if eq['fem']:
        return f"{base} FEM {eq['letra']}".strip()
    if eq['cat'] == 'ALEVIN' and eq['f7']:
        base += ' F7'
    return f"{base} {eq['letra']}".strip()


def nombre_nuestro(eq):
    n = LARGO[eq['cat']]
    if eq['fem']:
        n += ' FEM'
    elif eq['cat'] == 'ALEVIN' and eq['f7']:
        n += ' F7'
    return f"{n} {eq['letra']}".strip()


def ventana(clasif, idx, n=5):
    ini = max(0, min(idx - n // 2, len(clasif) - n))
    return list(range(ini, min(len(clasif), ini + n)))


def jornada(clasif):
    try:
        return max(int(r.get('jugados') or 0) for r in clasif)
    except ValueError:
        return 0


# ---------- dibujo: comunes ----------

def _paralelo(d, x0, y0, x1, y1, color, sesgo=12, borde=None, ancho_borde=3):
    pts = [(x0 + sesgo, y0), (x1, y0), (x1 - sesgo, y1), (x0, y1)]
    d.polygon(pts, fill=color)
    if borde:
        d.line(pts + [pts[0]], fill=borde, width=ancho_borde, joint='curve')


def _pincelada(d, rnd, x0, y0, largo, grosor, angulo, color):
    """Trazo de pincel: banda inclinada con bordes y extremos irregulares."""
    import math
    ca, sa = math.cos(angulo), math.sin(angulo)
    nx, ny = -sa, ca
    pasos = 40
    arriba, abajo = [], []
    for i in range(pasos + 1):
        t = i / pasos
        cx, cy = x0 + ca * largo * t, y0 + sa * largo * t
        g = grosor * (0.55 + 0.45 * math.sin(math.pi * min(1, t * 1.15)) ** 0.4)
        a, b = g / 2 * rnd.uniform(0.85, 1.12), g / 2 * rnd.uniform(0.85, 1.12)
        arriba.append((cx + nx * a, cy + ny * a))
        abajo.append((cx - nx * b, cy - ny * b))
    d.polygon(arriba + abajo[::-1], fill=color)
    for _ in range(int(largo / 30)):  # cerdas sueltas
        t = rnd.uniform(0, 1)
        off = rnd.uniform(-grosor / 2, grosor / 2)
        cx, cy = x0 + ca * largo * t + nx * off, y0 + sa * largo * t + ny * off
        l2 = rnd.uniform(20, 70)
        d.line([(cx, cy), (cx + ca * l2, cy + sa * l2)], fill=color, width=rnd.randint(2, 5))


def _fondo_blanco():
    from PIL import Image, ImageDraw, ImageFilter
    rnd = random.Random(7)
    img = Image.new('RGBA', (W, H), (238, 238, 238, 255))
    ruido = Image.effect_noise((W // 4, H // 4), 18).resize((W, H)).filter(ImageFilter.GaussianBlur(6))
    papel = Image.merge('RGBA', [ruido.point(lambda v: 210 + v // 6)] * 3 + [Image.new('L', (W, H), 255)])
    img = Image.blend(img, papel, 0.6)
    capa = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    import math
    for (y, x, ang, gro, col) in [(-30, -120, 0.26, 80, ROJO), (10, 420, 0.22, 60, (200, 22, 30)),
                                  (90, -200, 0.20, 44, ROJO), (40, 720, 0.30, 56, ROJO), (140, 600, 0.24, 30, (200, 22, 30))]:
        _pincelada(d, rnd, x, y, 700, gro, ang, col + (255,))
    for (y, x, ang, gro, col) in [(1890, -160, -0.16, 70, ROJO), (1940, 330, -0.18, 66, (200, 22, 30)),
                                  (1850, 680, -0.20, 46, ROJO), (1960, 600, -0.16, 84, ROJO), (1820, 40, -0.12, 28, (200, 22, 30))]:
        _pincelada(d, rnd, x, y, 760, gro, ang, col + (255,))
    capa = capa.filter(ImageFilter.GaussianBlur(0.8))
    img.alpha_composite(capa)
    return img


# ---------- modelo por categoría (fondo blanco) ----------

def imagen_categoria(eqs, titulo=''):
    from PIL import ImageDraw
    img = _fondo_blanco()
    d = ImageDraw.Draw(img)
    arriba, abajo = 300, 1660
    if titulo:
        f = _f(F_GOTHIC, 64)
        d.text((W / 2, 265), titulo, font=f, fill=ROJO, anchor='ms')
        arriba = 300
    n = len(eqs)
    gap_bloque = 70 if n <= 3 else 44
    filas = 5 * n
    paso = min(66, (abajo - arriba - gap_bloque * (n - 1)) / filas)
    alto = paso * 0.8
    total = filas * paso + gap_bloque * (n - 1)
    y = arriba + (abajo - arriba - total) / 2
    xl0, xl1 = 40, 330          # columna de la izquierda
    xp0, xp1 = 360, 455         # posición
    xn0, xn1 = 462, 905         # nombre
    xpts = 1040                 # puntos (derecha)
    for eq in eqs:
        idxs = ventana(eq['clasif'], eq['idx'])
        nfil = len(eq['clasif'])
        y_bloque = y
        for k, i in enumerate(idxs):
            r = eq['clasif'][i]
            y0 = y + k * paso
            y1 = y0 + alto
            pos = int(r.get('pos') or i + 1)
            col_pos = VERDE if pos <= 2 else (ROJO_OSCURO if pos > nfil - 3 else ROJO)
            _paralelo(d, xp0, y0, xp1, y1, col_pos, sesgo=10)
            fpos = _f(F_GOTHIC, int(alto * 0.95))
            d.text(((xp0 + xp1) / 2, (y0 + y1) / 2 + 2), f'{pos}º', font=fpos, fill='white', anchor='mm')
            if _es_nuestro(r['equipo']):  # también otro equipo nuestro del mismo grupo, con su letra
                _paralelo(d, xn0, y0, xn1, y1, 'white', sesgo=10, borde=ROJO, ancho_borde=4)
                texto, color = nombre_nuestro({**eq, 'letra': _letra(r['equipo'])}), ROJO
            else:
                _paralelo(d, xn0, y0, xn1, y1, ROJO, sesgo=10)
                texto, color = _limpio(r['equipo']), 'white'
            fn = _encajar(d, texto, F_GOTHIC, int(alto * 0.9), xn1 - xn0 - 40, minimo=int(alto * 0.5))
            d.text(((xn0 + xn1) / 2, (y0 + y1) / 2 + 2), texto, font=fn, fill=color, anchor='mm')
            d.text((xpts, (y0 + y1) / 2 + 2), str(r.get('puntos', '')), font=_f(F_GOTHIC, int(paso * 1.05)), fill=ROJO, anchor='rm')
        # columna de la izquierda, centrada en el bloque
        alto_b = len(idxs) * paso - (paso - alto)
        cy = y_bloque + alto_b / 2
        div = eq['division'] or ''
        fdiv = _encajar(d, div, F_NEGRITA, 26, xl1 - xl0 - 40, minimo=16)
        d.text((xl0 + 38, cy - 62), div, font=fdiv, fill=NEGRO, anchor='ls')
        if eq['grupo']:
            d.text((xl0 + 38, cy - 36), f"GRUPO {eq['grupo']}", font=_f(F_NEGRITA, 17), fill=NEGRO, anchor='ls')
        _paralelo(d, xl0 + 8, cy - 86, xl0 + 22, cy - 34, ROJO, sesgo=6)  # rayita roja
        _paralelo(d, xl0, cy - 22, xl1, cy + 44, ROJO, sesgo=14)
        et = etiqueta(eq)
        fet = _encajar(d, et, F_GOTHIC, 70, xl1 - xl0 - 40, minimo=34)
        d.text(((xl0 + xl1) / 2, cy + 13), et, font=fet, fill='white', anchor='mm')
        y += len(idxs) * paso + gap_bloque
    return _png(img)


# ---------- modelo Senior A (rojo, con escudos) ----------

def _escudo(nombre):
    from PIL import Image
    if _es_nuestro(nombre):
        return Image.open(ESCUDO_CLUB).convert('RGBA')
    try:
        from alineacion_ig_wa import _escudo_rival, _rival
        return _escudo_rival(_rival(nombre))
    except Exception:
        return None


def _pegar_escudo(img, e, cx, cy, lado):
    from PIL import Image
    e = e.crop(e.getbbox() or (0, 0, e.width, e.height))
    k = min(lado / e.width, lado / e.height)
    e = e.resize((max(1, round(e.width * k)), max(1, round(e.height * k))), Image.LANCZOS)
    img.alpha_composite(e, (int(cx - e.width / 2), int(cy - e.height / 2)))


def imagen_senior(eq, hoy=None):
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new('RGBA', (W, H), (255, 255, 255, 255))
    # fondo: degradado gris muy claro hacia abajo
    g = Image.linear_gradient('L').resize((W, H)).point(lambda v: 255 - v // 9)
    img = Image.merge('RGBA', [g, g, g, Image.new('L', (W, H), 255)])
    # banda roja de arriba con borde inclinado
    banda = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    db = ImageDraw.Draw(banda)
    db.polygon([(0, 0), (W, 0), (W, 520), (0, 600)], fill=ROJO + (255,))
    rad = Image.radial_gradient('L').resize((W * 2, 1300)).crop((W // 2, 0, W // 2 + W, 650))
    sombra = Image.new('RGBA', (W, 650), ROJO_OSCURO + (255,))
    sombra.putalpha(rad.point(lambda v: int(v * 0.75)))
    banda_sombra = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    banda_sombra.paste(sombra, (0, 0))
    mask = banda.getchannel('A')
    banda.alpha_composite(Image.composite(banda_sombra, Image.new('RGBA', (W, H), (0, 0, 0, 0)), mask))
    img.alpha_composite(banda)
    d = ImageDraw.Draw(img)
    # "26" grande de fondo, transparente
    temp = temporada(hoy)
    marca = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(marca).text((W - 20, 560), temp.split('-')[1], font=_f(F_GOTHIC, 640), fill=(255, 255, 255, 34), anchor='rs')
    img.alpha_composite(marca)
    d = ImageDraw.Draw(img)
    _pegar_escudo(img, Image.open(ESCUDO_CLUB).convert('RGBA'), 110, 120, 110)
    d.text((70, 330), 'TEMP', font=_f(F_GOTHIC, 110), fill='white', anchor='ls')
    d.text((70, 450), temp, font=_f(F_GOTHIC, 150), fill='white', anchor='ls')
    div = (eq['division'] or '').title().replace('Preferente', 'Preferente')
    grupo = f" G.{ROMANOS[eq['grupo']]}" if eq['grupo'] and eq['grupo'] < len(ROMANOS) else ''
    d.text((72, 510), f"CLASIFICACIÓN J.{jornada(eq['clasif'])}", font=_f(F_NEGRITA, 40), fill='white', anchor='ls')
    d.text((72, 556), f"{div}{grupo}", font=_f(F_NEGRITA, 30), fill='white', anchor='ls')
    # tabla
    clasif = eq['clasif']
    filas = list(range(min(10, len(clasif))))
    if eq['idx'] >= 10:
        filas.append(eq['idx'])
    x0, x1 = 60, W - 60
    cab_y = 680
    d.rectangle([x0, cab_y, x1, cab_y + 64], fill=ROJO)
    fcab = _f(F_NEGRITA, 24)
    cx_pos, x_esc, x_nom, x_j, x_pts = x0 + 50, x0 + 150, x0 + 210, x1 - 150, x1 - 55
    d.text((cx_pos, cab_y + 32), 'NO', font=fcab, fill='white', anchor='mm')
    d.text((x_nom, cab_y + 32), 'EQUIPO', font=fcab, fill='white', anchor='lm')
    d.text((x_j, cab_y + 32), 'J', font=fcab, fill='white', anchor='mm')
    d.text((x_pts, cab_y + 32), 'Pts', font=fcab, fill='white', anchor='mm')
    paso = 92
    y = cab_y + 64
    for k, i in enumerate(filas):
        r = clasif[i]
        if k == 10:  # nuestra fila, separada
            y += 24
            for xx in range(x0, x1, 22):
                d.line([(xx, y - 12), (xx + 10, y - 12)], fill=(190, 190, 190), width=3)
        cy = y + paso / 2
        d.rectangle([x0, y, x1, y + paso], fill='white')
        d.line([(x0 + 20, y + paso), (x1 - 20, y + paso)], fill=(228, 228, 228), width=2)
        pos = int(r.get('pos') or i + 1)
        nuestro = i == eq['idx']
        if pos <= 2:
            d.ellipse([cx_pos - 24, cy - 24, cx_pos + 24, cy + 24], fill=ROJO)
            d.text((cx_pos, cy + 1), str(pos), font=_f(F_NEGRITA, 26), fill='white', anchor='mm')
        else:
            d.text((cx_pos, cy + 1), str(pos), font=_f(F_NEGRITA, 26), fill=NEGRO, anchor='mm')
        esc = _escudo(r['equipo'])
        if esc is not None:
            _pegar_escudo(img, esc, x_esc, cy, 66)
            d = ImageDraw.Draw(img)
        nombre = 'CLUB FUENTELARREYNA' if nuestro else _limpio(r['equipo'])
        fn = _encajar(d, nombre, F_NEGRITA, 27, x_j - x_nom - 40, minimo=17)
        col = ROJO if nuestro else NEGRO
        d.text((x_nom, cy + 1), nombre, font=fn, fill=col, anchor='lm')
        d.text((x_j, cy + 1), str(r.get('jugados', '')), font=_f(F_NEGRITA, 27), fill=ROJO if nuestro else NEGRO, anchor='mm')
        d.text((x_pts, cy + 1), str(r.get('puntos', '')), font=_f(F_NEGRITA, 30), fill=ROJO, anchor='mm')
        y += paso
    sombra = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rectangle([x0, cab_y, x1, y], outline=(0, 0, 0, 40), width=6)
    img.alpha_composite(sombra.filter(ImageFilter.GaussianBlur(8)), (0, 0))
    return _png(img)


# ---------- todas las historias ----------

def historias(cache, hoy=None):
    """[(titulo, png)]: una por categoría, todas con el mismo modelo (el Aficionado A va con los Aficionados).
    imagen_senior (modelo rojo con escudos) se deja hecho por si se vuelve a usar."""
    eqs = equipos(cache)
    out = []
    for cat in ORDEN:
        grupo = [e for e in eqs if e['cat'] == cat]
        if grupo:
            out.append((TITULO[cat], imagen_categoria(grupo)))
    return out


# ---------- subida automática (lunes 10:00) ----------
import json
import threading
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify

clasif_ig_bp = Blueprint('clasif_instagram', __name__)
DATA_FILE = os.path.join(BASE_DIR, 'datos_privados', 'clasif_instagram.json')
DIA_ENVIO, HORA_ENVIO = 0, (10, 0)  # lunes 10:00 (a las 09:30 se pide a la Federación la clasificación al día)
_ocupado = threading.Lock()


def _leer():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault('activo', True)
    return d


def _escribir(d):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def _cache():
    from competicion_scraper import load_cached_data
    c = load_cached_data()
    if c.get('status') == 'demo':
        raise RuntimeError('no hay clasificaciones de la Federación descargadas')
    return c


def publicar(solo_whatsapp=False):
    """Sube las historias (o, en prueba, solo las manda a Rubén Pozón) y avisa por WhatsApp. Devuelve (ok, texto)."""
    from objetivos_finde import enviar_imagen
    from instagram_historias import _tel_ruben
    cache = _cache()
    hs = historias(cache)
    if not hs:
        return False, 'No hay clasificaciones'
    tel = _tel_ruben()
    act = cache.get('last_updated', '')
    if solo_whatsapp:
        if not tel:
            return False, 'No encuentro el teléfono de Rubén Pozón en PERFILES'
        for i, (t, png) in enumerate(hs):
            ok, det = enviar_imagen(f'{tel}@c.us', png, f'🧪 PRUEBA clasificación {t} ({i + 1}/{len(hs)}) · datos del {act}')
            if not ok:
                return False, det
        return True, f'Enviadas a Rubén Pozón las {len(hs)} historias (no se ha subido nada a Instagram)'
    from instagram_historias import subir_historia
    subidas, fallos = [], []
    for t, png in hs:
        try:
            ok, det = subir_historia(png)
        except Exception as e:
            ok, det = False, str(e)[:200]
        (subidas if ok else fallos).append(t if ok else f'{t}: {det}')
    texto = (f"✅ Clasificaciones subidas a las historias de Instagram ({len(subidas)}): {', '.join(subidas)}" if not fallos else
             "⚠️ No se han podido subir todas las clasificaciones a Instagram:\n" + '\n'.join(f'• {f}' for f in fallos)
             + (f"\n(Sí se han subido: {', '.join(subidas)})" if subidas else ''))
    texto += f"\nDatos de la Federación del {act}"
    if tel:
        enviar_imagen(f'{tel}@c.us', hs[0][1], texto)
    d = _leer()
    d['ultima_subida'] = {'fecha': datetime.now().isoformat(timespec='minutes'), 'ok': not fallos, 'texto': texto}
    _escribir(d)
    print(f'[CLASIF IG] {texto}', flush=True)
    return not fallos, texto


def tick(ahora):
    """Cada minuto (push_scheduler). Lunes: a las 09:30 refresca la caché de la Federación; de 10:00 a 10:30, una vez, sube."""
    if ahora.weekday() != DIA_ENVIO:
        return
    d = _leer()
    if not d.get('activo'):
        return
    if ahora.hour == 9 and ahora.minute == 30:
        try:
            from competicion_scraper import intentar_autosync_background
            intentar_autosync_background()
        except Exception as e:
            print(f'[CLASIF IG] sync: {e}', flush=True)
        return
    inicio = ahora.replace(hour=HORA_ENVIO[0], minute=HORA_ENVIO[1], second=0, microsecond=0)
    hoy = ahora.date().isoformat()
    if d.get('ultimo') == hoy or not (inicio <= ahora < inicio + timedelta(minutes=30)):
        return
    d['ultimo'] = hoy  # marcar antes de subir: nunca dos veces el mismo lunes
    _escribir(d)

    def _job():
        with _ocupado:
            try:
                publicar()
            except Exception as e:
                print(f'[CLASIF IG] {e}', flush=True)
    threading.Thread(target=_job, daemon=True).start()


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@clasif_ig_bp.route('/api/whatsapp/clasif_ig', methods=['GET', 'POST'])
def api_estado():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    d = _leer()
    if request.method == 'POST' and 'activo' in (request.json or {}):
        d['activo'] = bool(request.json['activo'])
        _escribir(d)
    return jsonify({'status': 'ok', 'activo': d['activo'], 'ultima': d.get('ultima_subida') or {}})


@clasif_ig_bp.route('/api/whatsapp/clasif_ig/prueba', methods=['POST'])
def api_prueba():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    if not _ocupado.acquire(blocking=False):
        return jsonify({'status': 'error', 'message': 'Ya se está enviando, espera un momento'})
    try:
        ok, det = publicar(solo_whatsapp=True)
        return jsonify({'status': 'ok' if ok else 'error', 'message': det})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]})
    finally:
        _ocupado.release()
