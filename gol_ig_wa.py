"""Gol del Aficionado A en las historias de Instagram.

Parte 1 (esta): fotos de los jugadores y su celebración generada con IA, que se revisa y se aprueba en la intranet.
- Se sube una foto de cada jugador (de cuerpo entero, con la equipación).
- Gemini genera a ese mismo jugador celebrando un gol sobre fondo verde liso, y se le quita el verde.
- Se aprueba (o se genera otra). En el partido se usa la aprobada, sin esperar a la IA.
- La imagen del gol: fondo rojo con franjas y la grada con bufandas, nombres de los equipos en vertical, el jugador
  celebrando por encima del "GOL" (ancho y en perspectiva, como un MATCH DAY), el resultado grande debajo y abajo
  fecha · goleador · minuto. Gol del rival: lo mismo sin jugador ni "GOL", solo el resultado.
Ficheros en datos_privados/gol/ (fotos, candidatos, aprobadas) y estado en datos_privados/gol/estado.json.
"""
import io
import os
import re
import json
import base64
import threading
import unicodedata
from datetime import datetime
from flask import Blueprint, request, jsonify, Response

gol_bp = Blueprint('gol_ig_wa', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(BASE_DIR, 'datos_privados', 'gol')
ESTADO = os.path.join(DIR, 'estado.json')
EQUIPO = 'AFICIONADO A'
W, H = 1080, 1920
F_BOLD = os.path.join(BASE_DIR, 'static', 'fonts', 'Montserrat-ExtraBold.ttf')
F_MED = os.path.join(BASE_DIR, 'static', 'fonts', 'Montserrat-Medium.ttf')
GRADA = os.path.join(BASE_DIR, 'static', 'img', 'gol', 'grada.jpg')
_MESES = ['ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE']
_lock = threading.Lock()

PROMPT_CELEBRACION = (
    "Use the person in this photo. Create a photorealistic, full-body image of the SAME person: keep exactly the same "
    "face, facial features, hairstyle, skin tone and build, so that he is clearly recognisable. He wears exactly the "
    "same red football kit as in the photo (same shirt with its badges and logos, same shorts, same socks and the same "
    "boots). He is celebrating a goal with passion: running towards the camera with both arms spread wide and his "
    "mouth open, shouting with joy. The whole body must be visible from the top of the head to the boots, nothing "
    "cut off, centred, with some empty space around. Background: a completely flat, uniform pure chroma-key green "
    "(#00FF00) with no shadows, no floor and no objects. Bright, even studio lighting. No text, no ball, no other people.")


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.upper().split())


def _slug(nombre):
    return re.sub(r'[^a-z0-9]+', '_', _norm(nombre).lower()).strip('_')[:60]


def _ruta(tipo, slug):
    ext = 'jpg' if tipo == 'foto' else 'png'
    return os.path.join(DIR, tipo, f'{slug}.{ext}')


def _leer():
    try:
        with open(ESTADO, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _escribir(d):
    os.makedirs(DIR, exist_ok=True)
    tmp = ESTADO + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ESTADO)


def _marcar(slug, **kw):
    with _lock:
        d = _leer()
        d.setdefault(slug, {}).update(kw)
        _escribir(d)


PLANTILLA = os.path.join(DIR, 'plantilla.json')


def jugadores():
    """Plantilla del Aficionado A: los que han salido en las actas de la Federación esta temporada (nombre del acta,
    'APELLIDOS, NOMBRE') más los añadidos a mano. El Aficionado A no está en la hoja JUGADORES de la intranet."""
    try:
        with open(PLANTILLA, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        d = {}
    return sorted(set(d.get('acta', [])) | set(d.get('manual', [])), key=_norm)


def _guardar_plantilla(**kw):
    with _lock:
        try:
            with open(PLANTILLA, 'r', encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            d = {}
        d.update(kw)
        os.makedirs(DIR, exist_ok=True)
        with open(PLANTILLA, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=1)


def actualizar_plantilla():
    """Recorre los partidos jugados del Aficionado A esta temporada y junta los jugadores de sus actas."""
    import calendar
    import competicion_scraper as cs
    from alineacion_ig_wa import _sesion, COMPETICION
    try:
        _guardar_plantilla(actualizando=True, error='')
        sess = _sesion()
        cod, desde, hasta = cs._temporada_actual()
        hoy = datetime.now().strftime('%d-%m-%Y')
        partidos = [m for m in cs.fetch_temporada_matches(sess, cod_temporada=cod, fecha_desde=desde, fecha_hasta=hoy)
                    if _norm(m.get('competicion')).startswith(COMPETICION)
                    and 'FUENTELARREYNA' in _norm(f"{m.get('nombre_casa')} {m.get('nombre_fuera')}")]
        mapas, nombres = {}, set()
        for m in partidos:
            dd, mm, yy = m['fecha'].split('-')
            if (mm, yy) not in mapas:
                ult = calendar.monthrange(int(yy), int(mm))[1]
                mapas[(mm, yy)] = cs.fetch_codacta_map(sess, cod_temporada=cod, fecha_desde=f'01-{mm}-{yy}',
                                                       fecha_hasta=f'{ult:02d}-{mm}-{yy}')
            hora = (m.get('hora') or '').strip()[:5]
            codacta = mapas[(mm, yy)].get(f"{m['fecha']}_{hora}_{m.get('cod_equipo_casa')}_{m.get('cod_equipo_fuera')}")
            if not codacta:
                continue
            al = cs.fetch_alineacion_convocados(sess, codacta, True) or {}
            lado = 'casa' if 'FUENTELARREYNA' in _norm(m.get('nombre_casa')) else 'fuera'
            nombres |= {j['nombre'].strip() for j in al.get(lado, []) if j.get('nombre')}
        _guardar_plantilla(acta=sorted(nombres), actualizando=False, actualizada=datetime.now().isoformat(timespec='minutes'),
                           partidos=len(partidos))
        print(f'[GOL] Plantilla del Aficionado A: {len(nombres)} jugadores de {len(partidos)} partidos', flush=True)
    except Exception as e:
        _guardar_plantilla(actualizando=False, error=str(e)[:200])
        print(f'[GOL] Error actualizando la plantilla: {e}', flush=True)


# ---------- IA: celebración ----------

def _clave_gemini():
    try:
        with open(os.path.join(BASE_DIR, 'secretos.json'), encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    return cfg.get('gemini_api_key') or cfg.get('GEMINI_API_KEY') or os.environ.get('GEMINI_API_KEY')


def _modelo_imagen(clave):
    import requests
    r = requests.get('https://generativelanguage.googleapis.com/v1beta/models', params={'key': clave, 'pageSize': 300}, timeout=30)
    nombres = [m['name'] for m in r.json().get('models', [])
               if 'image' in m['name'] and 'generateContent' in (m.get('supportedGenerationMethods') or [])]
    for preferido in ('models/gemini-2.5-flash-image', 'models/gemini-2.5-flash-image-preview'):
        if preferido in nombres:
            return preferido
    if not nombres:
        raise RuntimeError('La clave de Gemini no tiene ningún modelo de imágenes disponible')
    return sorted(nombres)[-1]


def generar_celebracion_ia(foto_jpg):
    """PNG (RGB, fondo verde) del jugador celebrando, generado por Gemini a partir de su foto."""
    import requests
    clave = _clave_gemini()
    if not clave:
        raise RuntimeError('No hay clave de Gemini configurada')
    modelo = _modelo_imagen(clave)
    cuerpo = {'contents': [{'parts': [{'text': PROMPT_CELEBRACION},
                                      {'inline_data': {'mime_type': 'image/jpeg', 'data': base64.b64encode(foto_jpg).decode()}}]}],
              'generationConfig': {'responseModalities': ['IMAGE', 'TEXT'], 'imageConfig': {'aspectRatio': '9:16'}}}
    r = requests.post(f'https://generativelanguage.googleapis.com/v1beta/{modelo}:generateContent', params={'key': clave},
                      json=cuerpo, timeout=240)
    if r.status_code == 400 and 'imageConfig' in r.text:  # modelos sin esa opción
        cuerpo['generationConfig'].pop('imageConfig')
        r = requests.post(f'https://generativelanguage.googleapis.com/v1beta/{modelo}:generateContent', params={'key': clave},
                          json=cuerpo, timeout=240)
    j = r.json()
    if r.status_code != 200:
        raise RuntimeError(((j.get('error') or {}).get('message') or r.text)[:200])
    for c in j.get('candidates', []):
        for p in (c.get('content') or {}).get('parts', []):
            d = p.get('inline_data') or p.get('inlineData')
            if d:
                return base64.b64decode(d['data'])
    raise RuntimeError('La IA no ha devuelto ninguna imagen (puede que la haya rechazado). Prueba con otra foto.')


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


def _generar(slug, nombre):
    try:
        with open(_ruta('foto', slug), 'rb') as f:
            foto = f.read()
        recorte = quitar_verde(generar_celebracion_ia(foto))
        os.makedirs(os.path.dirname(_ruta('candidato', slug)), exist_ok=True)
        recorte.save(_ruta('candidato', slug))
        _marcar(slug, generando=False, error='', candidato_en=datetime.now().isoformat(timespec='minutes'))
        print(f'[GOL] Celebración generada: {nombre}', flush=True)
    except Exception as e:
        _marcar(slug, generando=False, error=str(e)[:200])
        print(f'[GOL] Error generando {nombre}: {e}', flush=True)


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


@gol_bp.route('/api/gol/jugadores')
def api_jugadores():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        if not os.path.exists(PLANTILLA):  # primera vez: se saca de las actas de la Federación
            _guardar_plantilla(actualizando=True)
            threading.Thread(target=actualizar_plantilla, daemon=True).start()
        with open(PLANTILLA, 'r', encoding='utf-8') as f:
            meta = json.load(f)
        est = _leer()
        out = []
        for n in jugadores():
            s = _slug(n)
            e = est.get(s, {})
            out.append({'nombre': n, 'corto': nombre_corto(n), 'slug': s, 'foto': os.path.exists(_ruta('foto', s)),
                        'candidato': os.path.exists(_ruta('candidato', s)), 'aprobada': os.path.exists(_ruta('aprobada', s)),
                        'generando': bool(e.get('generando')), 'error': e.get('error', '')})
        return jsonify({'status': 'ok', 'jugadores': out, 'actualizando': bool(meta.get('actualizando')),
                        'actualizada': meta.get('actualizada', ''), 'error_plantilla': meta.get('error', '')})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]})


@gol_bp.route('/api/gol/plantilla', methods=['POST'])
def api_plantilla():
    """{'accion': 'actualizar'} → vuelve a leer las actas; {'accion': 'anadir', 'nombre': ...} → jugador a mano."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    data = request.json or {}
    if data.get('accion') == 'anadir':
        nombre = ' '.join(str(data.get('nombre', '')).split()).upper()
        if len(nombre) < 3:
            return jsonify({'status': 'error', 'message': 'Escribe el nombre del jugador'})
        try:
            with open(PLANTILLA, 'r', encoding='utf-8') as f:
                manual = json.load(f).get('manual', [])
        except Exception:
            manual = []
        _guardar_plantilla(manual=sorted(set(manual) | {nombre}))
        return jsonify({'status': 'ok'})
    _guardar_plantilla(actualizando=True)
    threading.Thread(target=actualizar_plantilla, daemon=True).start()
    return jsonify({'status': 'ok', 'message': 'Leyendo las actas de la Federación…'})


def _jugador_de(slug):
    return next((n for n in jugadores() if _slug(n) == slug), None)


@gol_bp.route('/api/gol/foto', methods=['POST'])
def api_foto():
    """Sube la foto de un jugador (se gira según el móvil y se reduce a 2000 px)."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    from PIL import Image, ImageOps
    slug = request.form.get('slug', '')
    f = request.files.get('foto')
    if not f or not _jugador_de(slug):
        return jsonify({'status': 'error', 'message': 'Falta la foto o el jugador'})
    try:
        im = ImageOps.exif_transpose(Image.open(f.stream)).convert('RGB')
        im.thumbnail((2000, 2000))
        os.makedirs(os.path.dirname(_ruta('foto', slug)), exist_ok=True)
        im.save(_ruta('foto', slug), quality=92)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'No se pudo guardar la foto: {e}'[:200]})


@gol_bp.route('/api/gol/generar', methods=['POST'])
def api_generar():
    """Genera (en segundo plano) la celebración con IA a partir de la foto."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    slug = (request.json or {}).get('slug', '')
    nombre = _jugador_de(slug)
    if not nombre or not os.path.exists(_ruta('foto', slug)):
        return jsonify({'status': 'error', 'message': 'Primero sube la foto del jugador'})
    if _leer().get(slug, {}).get('generando'):
        return jsonify({'status': 'ok', 'message': 'Ya se está generando'})
    _marcar(slug, generando=True, error='')
    threading.Thread(target=_generar, args=(slug, nombre), daemon=True).start()
    return jsonify({'status': 'ok', 'message': 'Generando… tarda 20-60 segundos'})


@gol_bp.route('/api/gol/aprobar', methods=['POST'])
def api_aprobar():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    slug = (request.json or {}).get('slug', '')
    if not os.path.exists(_ruta('candidato', slug)):
        return jsonify({'status': 'error', 'message': 'No hay celebración generada'})
    os.makedirs(os.path.dirname(_ruta('aprobada', slug)), exist_ok=True)
    os.replace(_ruta('candidato', slug), _ruta('aprobada', slug))
    _marcar(slug, aprobada_en=datetime.now().isoformat(timespec='minutes'))
    return jsonify({'status': 'ok'})


@gol_bp.route('/api/gol/img/<tipo>/<slug>')
def api_img(tipo, slug):
    """Foto, celebración candidata o aprobada (solo para la pantalla de la intranet)."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    if tipo not in ('foto', 'candidato', 'aprobada') or not re.fullmatch(r'[a-z0-9_]+', slug or ''):
        return jsonify({'status': 'error'}), 404
    ruta = _ruta(tipo, slug)
    if not os.path.exists(ruta):
        return jsonify({'status': 'error'}), 404
    with open(ruta, 'rb') as f:
        return Response(f.read(), mimetype='image/jpeg' if tipo == 'foto' else 'image/png', headers={'Cache-Control': 'no-store'})


@gol_bp.route('/api/gol/vista')
def api_vista():
    """La historia del gol con ese jugador (candidata o aprobada), con datos de ejemplo. ?slug=…&tipo=candidato|aprobada|rival"""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    from PIL import Image
    slug, tipo = request.args.get('slug', ''), request.args.get('tipo', 'aprobada')
    try:
        hoy = datetime.now().date()
        if tipo == 'rival':
            png = imagen_gol('1-1', 'CLUB FUENTELARREYNA A', 'RIVAL DE EJEMPLO', fecha_txt(hoy))
        else:
            nombre = _jugador_de(slug)
            ruta = _ruta(tipo if tipo in ('candidato', 'aprobada') else 'aprobada', slug)
            if not nombre or not os.path.exists(ruta):
                return jsonify({'status': 'error', 'message': 'No hay celebración de ese jugador'})
            png = imagen_gol('1-0', 'CLUB FUENTELARREYNA A', 'RIVAL DE EJEMPLO', fecha_txt(hoy), nombre_corto(nombre),
                             "MIN 34'", Image.open(ruta))
        return Response(png, mimetype='image/png', headers={'Cache-Control': 'no-store'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]})
