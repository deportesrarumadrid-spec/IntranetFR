"""Alineación del Aficionado A (Preferente) en las historias de Instagram.

Unos 15 minutos antes de cada partido la Federación activa "Descargar alineaciones". Desde 30 minutos antes se mira
cada 2 minutos; en cuanto sale, se monta la imagen sobre la plantilla del club (rival arriba, titulares en el orden
del acta con dorsal, suplentes debajo; nombres "NOMBRE APELLIDO"), se sube a la historia y se avisa a Rubén Pozón.
Si a los 10 minutos del inicio no ha salido, no se sube nada y se le avisa. Estado en datos_privados/alineacion_ig_wa.json.
"""
import io
import os
import json
import unicodedata
from datetime import datetime, date, timedelta
from flask import Blueprint, request, jsonify, Response

alineacion_bp = Blueprint('alineacion_ig_wa', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'datos_privados', 'alineacion_ig_wa.json')
PLANTILLA = os.path.join(BASE_DIR, 'static', 'img', 'alineacion', 'plantilla.png')
F_NEGRITA = os.path.join(BASE_DIR, 'static', 'fonts', 'Montserrat-ExtraBold.ttf')
F_NORMAL = os.path.join(BASE_DIR, 'static', 'fonts', 'Montserrat-Medium.ttf')
COMPETICION = 'PREFERENTE AFICIONADO'
ANTES_MIN, DESPUES_MIN, CADA_MIN = 30, 10, 2
ROJO = (227, 30, 36)
_PARTICULAS = {'DE', 'DEL', 'LA', 'LAS', 'LOS', 'Y', 'SAN', 'SANTA', 'VAN', 'VON', 'DA', 'DI', 'DOS'}


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.upper().split())


def _leer():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        d = {}
    d['activo'] = True  # siempre activo: sin casilla ACTIVO (Rubén Pozón, 18/11/2026)
    d.setdefault('partidos', {})
    return d


def _escribir(d):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


# ---------- nombres ----------

def nombre_corto(nombre_acta):
    """'GIMENEZ DEL CASTILLO, JAIME' → 'JAIME GIMENEZ'; 'DE LA ROSA GUERRA, FREILIN GUILLERM' → 'FREILIN DE LA ROSA'."""
    if ',' not in nombre_acta:
        return ' '.join(nombre_acta.split()).upper()
    apellidos, nombre = [x.strip() for x in nombre_acta.split(',', 1)]
    palabras, primero = apellidos.split(), []
    for w in palabras:
        primero.append(w)
        if w.upper() not in _PARTICULAS:
            break
    return f"{nombre.split()[0] if nombre else ''} {' '.join(primero)}".strip().upper()


def nombres_unicos(jugadores):
    """Nombre corto de cada jugador; si dos coinciden, se añade el segundo apellido."""
    cortos = [nombre_corto(j['nombre']) for j in jugadores]
    for i, c in enumerate(cortos):
        if cortos.count(c) > 1 and ',' in jugadores[i]['nombre']:
            apellidos = jugadores[i]['nombre'].split(',')[0].split()
            cortos[i] = f"{c} {apellidos[-1].upper()}" if len(apellidos) > 1 else c
    return cortos


def _rival(nombre):
    return ' '.join(nombre.replace('"', ' ').replace("'", ' ').split()).upper()


# ---------- imagen ----------

def _fuente(ruta, tam):
    from PIL import ImageFont
    return ImageFont.truetype(ruta, tam)


def _encajar(draw, texto, ruta, tam, ancho, minimo=18):
    while tam > minimo:
        f = _fuente(ruta, tam)
        if draw.textlength(texto, font=f) <= ancho:
            return f
        tam -= 2
    return _fuente(ruta, minimo)


def imagen(rival, titulares, suplentes):
    """PNG 1080x1920. titulares/suplentes: [{'numero', 'nombre'}] en el orden del acta."""
    from PIL import Image, ImageDraw
    img = Image.open(PLANTILLA).convert('RGB')
    d = ImageDraw.Draw(img)
    # Rival encima de TITULARES (zona blanca de la izquierda: x 67-540)
    f_riv = _encajar(d, rival, F_NEGRITA, 46, 470)
    d.text((67, 120), rival, font=f_riv, fill=ROJO, anchor='ls')
    # Titulares: dorsal en blanco dentro de la barra roja (x 75-195), nombre en rojo a la derecha
    y0, y1 = 372, 1352
    alto = (y1 - y0) / max(len(titulares), 11)
    nombres = nombres_unicos(titulares + suplentes)
    tit_n, sup_n = nombres[:len(titulares)], nombres[len(titulares):]
    f_nom = min((_encajar(d, n, F_NEGRITA, 44, 330) for n in tit_n), key=lambda f: f.size, default=_fuente(F_NEGRITA, 44))
    f_dor = _fuente(F_NEGRITA, min(46, f_nom.size + 4))
    for i, (j, n) in enumerate(zip(titulares, tit_n)):
        y = y0 + alto * i + alto / 2
        d.text((180, y), str(j['numero']), font=f_dor, fill='white', anchor='rm')
        d.text((210, y), n, font=f_nom, fill=ROJO, anchor='lm')
    # Suplentes: "Nombre Apellido, ..." en varias líneas debajo de "Suplentes:"
    texto = ', '.join(' '.join(w.capitalize() for w in n.split()) for n in sup_n)
    f_sup = _fuente(F_NORMAL, 30)
    lineas, linea = [], ''
    for palabra in texto.split(' '):
        prueba = f'{linea} {palabra}'.strip()
        if d.textlength(prueba, font=f_sup) <= 455:
            linea = prueba
        else:
            lineas.append(linea)
            linea = palabra
    if linea:
        lineas.append(linea)
    for i, l in enumerate(lineas[:7]):
        d.text((88, 1580 + i * 46), l, font=f_sup, fill=ROJO, anchor='ls')
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


# ---------- imagen con fotos (modelo nuevo) ----------
# Plantilla con 12 huecos (3 filas × 4): 11 titulares en el orden del acta + el entrenador. Foto del jugador sin fondo
# encima de cada barra blanca, dorsal arriba a la derecha y "NOMBRE APELLIDO" en la barra; abajo los escudos (primero
# el local) y los suplentes. Las fotos están en la carpeta de Drive "FOTOS ALINEACION AFICIONADO A", con el nombre que
# va en la barra ("ISAAC TORRES.jpg"); se cruzan con el acta por nombre + primer apellido.

PLANTILLA_FOTOS = os.path.join(BASE_DIR, 'static', 'img', 'alineacion', 'plantilla_fotos.png')
CARPETA_FOTOS = '1AkPbk4nlahVx_QiRsAzk-tPi2ScvA_36'  # creada una vez a mano: no crear otra desde aquí
ENTRENADOR = 'ERNESTO LOPEZ'
ESCUDO_CLUB = os.path.join(BASE_DIR, 'static', 'ESCUDO SIN FONDO.png')
DIR = os.path.join(BASE_DIR, 'datos_privados', 'alineacion')
COLUMNAS = [(34, 281), (288, 536), (543, 791), (799, 1046)]
BARRAS = [(457, 512), (808, 863), (1159, 1215)]
ALTO_FOTO = 295
_recortando = False


def fotos():
    """[{'id', 'nombre', 'mod'}] de la carpeta de fotos de la alineación."""
    from gol_live_wa import _drive
    r = _drive().get('https://www.googleapis.com/drive/v3/files', params={
        'q': f"'{CARPETA_FOTOS}' in parents and trashed=false and mimeType contains 'image/'",
        'fields': 'files(id,name,modifiedTime)', 'pageSize': 300})
    r.raise_for_status()
    return [{'id': f['id'], 'mod': f.get('modifiedTime', ''),
             'nombre': ' '.join(os.path.splitext(f['name'])[0].replace('_', ' ').split()).upper()}
            for f in r.json().get('files', [])]


def foto_de(nombre_acta, lista):
    """Foto del jugador del acta ('TORRES MARTIN, PEDRO ISAAC' ↔ 'ISAAC TORRES'): uno de sus nombres + el principio
    de sus apellidos."""
    if ',' not in nombre_acta:
        return next((f for f in lista if _norm(f['nombre']) == _norm(nombre_acta)), None)
    ape, nom = [_norm(x).split() for x in nombre_acta.split(',', 1)]
    for f in lista:
        w = _norm(f['nombre']).split()
        if len(w) >= 2 and w[0] in nom and ape[:len(w) - 1] == w[1:]:
            return f
    return None


def _ruta_recorte(f):
    return os.path.join(DIR, 'recortes', f"{f['id']}_{''.join(c for c in f.get('mod', '') if c.isdigit())}.png")


def recorte_de(f):
    from gol_live_wa import recorte, foto_bytes
    return recorte(foto_bytes(f), _ruta_recorte(f))


def preparar_recortes():
    """Quita el fondo a una foto nueva de la carpeta (las demás en las siguientes llamadas)."""
    for f in fotos():
        ruta = _ruta_recorte(f)
        if not os.path.exists(ruta) and not os.path.exists(ruta + '.error'):
            try:
                recorte_de(f)
                print(f"[ALINEACION] Fondo quitado: {f['nombre']}", flush=True)
            except Exception as e:
                print(f"[ALINEACION] Fondo de {f['nombre']}: {e}", flush=True)
                os.makedirs(os.path.dirname(ruta), exist_ok=True)
                open(ruta + '.error', 'w').write(str(e)[:300])
            return


ESCUDOS_DIR = os.path.join(BASE_DIR, 'static', 'img', 'alineacion', 'escudos')  # rivales del grupo (Federación)


def _fichero_escudo(rival):
    import re
    return os.path.join(ESCUDOS_DIR, re.sub(r'[^A-Z0-9]+', '_', _norm(rival)).strip('_') + '.png')


def _escudo_rival(rival):
    """Escudo (RGBA) del rival, de los descargados de la Federación (static/img/alineacion/escudos), o None."""
    from PIL import Image
    ruta = _fichero_escudo(rival)
    return Image.open(ruta).convert('RGBA') if os.path.exists(ruta) else None


def cabeza(rec):
    """(x_centro, y_arriba, ancho, alto) de la cabeza en la silueta recortada, o None. Baja por el centro del cuerpo
    desde arriba hasta el cuello (donde la silueta se estrecha)."""
    import numpy as np
    a = np.array(rec.split()[3]) > 128
    H, W = a.shape
    _, xs = np.nonzero(a[int(H * 0.4):int(H * 0.6)])
    if not len(xs):
        return None
    margen = max(2, W // 30)

    def tramo(y, x):
        fila = a[y]
        if not fila[x]:
            cerca = np.nonzero(fila[max(0, x - margen):x + margen])[0]
            if not len(cerca):
                return None
            x = max(0, x - margen) + int(cerca[len(cerca) // 2])
        i = j = x
        while i > 0 and fila[i - 1]:
            i -= 1
        while j < W - 1 and fila[j + 1]:
            j += 1
        return i, j

    cx = int(np.median(xs))
    top = next((y for y in range(H) if (t := tramo(y, cx)) and t[1] - t[0] >= 3), None)
    if top is None:
        return None
    maxw, centro, anchos = 0, cx, []
    for y in range(top, H):
        t = tramo(y, centro)
        if not t:
            break
        w, dy = t[1] - t[0], y - top
        fin = (dy > max(6, 0.6 * maxw) and w < 0.8 * maxw) or (dy > 0.9 * maxw > 20 and w > 1.6 * maxw)
        if fin:  # cuello (no un mechón de pelo de arriba), o se junta con hombros/manos
            # mediana y no máximo: una mano pegada a la cabeza (saludo) no la hace más ancha
            return centro, top, float(np.median(anchos)), dy
        anchos.append(w)
        if w > maxw:
            maxw, centro = w, (t[0] + t[1]) // 2
    return None


def _fondo_celda(ancho, alto, claro=True):
    """Rectángulo con degradado rojo (más claro en el centro de arriba), como los huecos del ejemplo."""
    import numpy as np
    from PIL import Image
    yy, xx = np.mgrid[0:alto, 0:ancho]
    d = np.sqrt(((xx - ancho / 2) / ancho) ** 2 + ((yy - alto * 0.35) / alto) ** 2) / 0.75
    d = np.clip(d, 0, 1)[..., None]
    centro, borde = (np.array([228, 48, 52]), np.array([150, 14, 20])) if claro else \
        (np.array([150, 16, 22]), np.array([92, 6, 10]))
    rgb = (centro * (1 - d) + borde * d).astype('uint8')
    return Image.fromarray(np.dstack([rgb, np.full((alto, ancho), 255, 'uint8')]), 'RGBA')


CABEZA_ANCHO = 74   # ancho de la cabeza en el hueco (igual para todos)
CABEZA_ARRIBA = 26  # distancia de la coronilla al borde de arriba del hueco


def _pegar_jugador(img, rec, x0, x1, y_bar):
    """Jugador dentro de su hueco, todos a la misma distancia: misma anchura de cabeza y cabeza a la misma altura.
    Lo que sobra por abajo o por los lados se corta en el borde del hueco."""
    from PIL import Image
    ancho_c, alto_c = x1 - x0, ALTO_FOTO
    c = cabeza(rec)
    if c:
        cx, top, w, _ = c
    else:
        cx, top, w = rec.width // 2, 0, rec.width / 3.2
    s = CABEZA_ANCHO / w
    parte = rec.resize((max(1, int(rec.width * s)), max(1, int(rec.height * s))), Image.LANCZOS)
    celda = Image.new('RGBA', (ancho_c, alto_c), (0, 0, 0, 0))
    dx, dy = int(ancho_c / 2 - cx * s), int(CABEZA_ARRIBA - top * s)
    # recortar lo que se sale del hueco (alpha_composite no admite posiciones negativas)
    caja = (max(0, -dx), max(0, -dy), min(parte.width, ancho_c - dx), min(parte.height, alto_c - dy))
    if caja[2] > caja[0] and caja[3] > caja[1]:
        celda.alpha_composite(parte.crop(caja), (max(0, dx), max(0, dy)))
    img.alpha_composite(celda, (x0, y_bar - alto_c))


def _escudo_en(img, escudo, cx, cy, lado):
    from PIL import Image
    e = escudo.convert('RGBA')
    e = e.crop(e.getbbox() or (0, 0, e.width, e.height))
    k = min(lado / e.width, lado / e.height)  # también agranda los pequeños: todos a la misma altura
    e = e.resize((max(1, round(e.width * k)), max(1, round(e.height * k))), Image.LANCZOS)
    img.alpha_composite(e, (int(cx - e.width / 2), int(cy - e.height / 2)))


def imagen_fotos(rival, titulares, suplentes, local=True, escudo_rival=None, lista_fotos=None, fondo_claro=True):
    """PNG 1080x1920 con el modelo de fotos. lista_fotos: fotos() (se lee de Drive si no se da).
    Si no hay escudo del rival, sale solo el nuestro (imagen_fotos.sin_escudo queda en True)."""
    from PIL import Image, ImageDraw
    lista = fotos() if lista_fotos is None else lista_fotos
    img = Image.open(PLANTILLA_FOTOS).convert('RGBA')
    escudo = Image.open(ESCUDO_CLUB).convert('RGBA')
    huecos = [(j, False) for j in titulares[:11]] + [({'nombre': ENTRENADOR, 'numero': ''}, True)]
    d = ImageDraw.Draw(img)
    for i, (j, es_entrenador) in enumerate(huecos):
        (x0, x1), (b0, b1) = COLUMNAS[i % 4], BARRAS[i // 4]
        img.alpha_composite(_fondo_celda(x1 - x0, ALTO_FOTO - 8, fondo_claro), (x0, b0 - ALTO_FOTO + 8))
        f = next((x for x in lista if _norm(x['nombre']) == ENTRENADOR), None) if es_entrenador else foto_de(j['nombre'], lista)
        rec = None
        if f:
            try:
                rec = recorte_de(f)
            except Exception as e:
                print(f"[ALINEACION] foto de {f['nombre']}: {e}", flush=True)
        if rec is not None:
            _pegar_jugador(img, rec, x0, x1, b0)
        else:  # sin foto: el escudo del club en su hueco
            _escudo_en(img, escudo, (x0 + x1) / 2, b0 - ALTO_FOTO / 2, 150)
        d = ImageDraw.Draw(img)
        nombre = f['nombre'] if f else (ENTRENADOR if es_entrenador else nombre_corto(j['nombre']))
        fn = _encajar(d, nombre, F_NEGRITA, 28, x1 - x0 - 18, minimo=14)
        d.text(((x0 + x1) / 2, (b0 + b1) / 2 + 1), nombre, font=fn, fill=ROJO, anchor='mm')
        if not es_entrenador and j.get('numero'):
            d.text((x1 - 12, b0 - ALTO_FOTO + 18), str(j['numero']), font=_fuente(F_NEGRITA, 34), fill='white', anchor='ra')
    # Escudos (primero el local), a la izquierda de la raya de los suplentes
    otro = escudo_rival if escudo_rival is not None else _escudo_rival(rival)
    imagen_fotos.sin_escudo = otro is None
    if otro is not None:
        izq, der = (escudo, otro) if local else (otro, escudo)
        _escudo_en(img, izq, 110, 1690, 130)
        _escudo_en(img, der, 260, 1690, 130)
    else:
        _escudo_en(img, escudo, 185, 1690, 150)
    # Suplentes: "NOMBRE APELLIDO, ..." a la derecha de la raya, debajo de "SUPLENTES:"
    d = ImageDraw.Draw(img)
    nombres = []
    for j in suplentes:
        f = foto_de(j['nombre'], lista)
        nombres.append(f['nombre'] if f else nombre_corto(j['nombre']))
    trozos = [n + (',' if i < len(nombres) - 1 else '') for i, n in enumerate(nombres)]  # no partir un nombre
    for tam in (28, 26, 24, 22, 20, 18):
        fs = _fuente(F_NORMAL, tam)
        lineas, linea = [], ''
        for palabra in trozos:
            prueba = f'{linea} {palabra}'.strip()
            if d.textlength(prueba, font=fs) <= 660:
                linea = prueba
            else:
                lineas.append(linea)
                linea = palabra
        if linea:
            lineas.append(linea)
        paso = int(tam * 1.45)
        if len(lineas) * paso <= 1795 - 1640:
            break
    for i, l in enumerate(lineas):
        d.text((379, 1660 + i * paso), l, font=fs, fill='white', anchor='ls')
    buf = io.BytesIO()
    img.convert('RGB').save(buf, format='PNG', optimize=True)
    return buf.getvalue()


# ---------- Federación ----------

def _sesion():
    from horarios import _rffm_session
    return _rffm_session()


def partidos_del_dia(dia, sess=None):
    """Partidos del Aficionado A ese día: [{'fecha', 'hora', 'casa', 'fuera', 'local', 'cod_casa', 'cod_fuera'}]."""
    import competicion_scraper as cs
    sess = sess or _sesion()
    cod, _, _ = cs._temporada_actual()
    f = dia.strftime('%d-%m-%Y')
    out = []
    for m in cs.fetch_temporada_matches(sess, cod_temporada=cod, fecha_desde=f, fecha_hasta=f):
        if not _norm(m.get('competicion')).startswith(COMPETICION):
            continue
        local = 'FUENTELARREYNA' in _norm(m.get('nombre_casa'))
        if not local and 'FUENTELARREYNA' not in _norm(m.get('nombre_fuera')):
            continue
        hora = (m.get('hora') or '').strip()[:5]
        if not hora or hora in ('00:00', '0:00'):
            continue
        out.append({'fecha': m['fecha'], 'hora': hora, 'casa': m.get('nombre_casa', ''), 'fuera': m.get('nombre_fuera', ''),
                    'local': local, 'cod_casa': str(m.get('cod_equipo_casa')), 'cod_fuera': str(m.get('cod_equipo_fuera'))})
    return out


def alineacion(p, sess=None):
    """(rival, titulares, suplentes) si la Federación ya tiene la alineación de ese partido, o None."""
    import competicion_scraper as cs
    sess = sess or _sesion()
    dd, mm, yy = p['fecha'].split('-')
    ultimo = __import__('calendar').monthrange(int(yy), int(mm))[1]
    mapa = cs.fetch_codacta_map(sess, cod_temporada=cs._temporada_actual()[0],
                                fecha_desde=f'01-{mm}-{yy}', fecha_hasta=f'{ultimo:02d}-{mm}-{yy}')
    codacta = mapa.get(f"{p['fecha']}_{p['hora']}_{p['cod_casa']}_{p['cod_fuera']}")
    if not codacta:
        return None
    al = cs.fetch_alineacion_convocados(sess, codacta, True)
    nuestros = (al or {}).get('casa' if p['local'] else 'fuera') or []
    if len(nuestros) < 11:
        return None
    return (_rival(p['fuera'] if p['local'] else p['casa']),
            [j for j in nuestros if j.get('titular')], [j for j in nuestros if not j.get('titular')])


# ---------- envío ----------

def _tel_ruben():
    from informe_veo import _telefono_ruben
    tel = ''.join(c for c in _telefono_ruben() if c.isdigit())
    return '34' + tel if len(tel) == 9 else tel


def imagen_elegida(p, datos):
    """Modelo con fotos; si falla (Drive, plantilla…), el modelo de texto de siempre."""
    rival, tit, sup = datos
    try:
        return imagen_fotos(rival, tit, sup, local=p.get('local', True))
    except Exception as e:
        print(f'[ALINEACION] modelo con fotos: {e}; uso el de texto', flush=True)
        return imagen(rival, tit, sup)


def publicar(p, datos, solo_whatsapp=False):
    """Monta la imagen, la sube a la historia (salvo prueba) y avisa a Rubén. Devuelve (ok, texto)."""
    from objetivos_finde import enviar_imagen
    from app import enviar_whatsapp
    rival, tit, sup = datos
    imagen_fotos.sin_escudo = False
    png = imagen_elegida(p, datos)
    nota = (f"\n⚠️ No tengo el escudo de {rival}: ha salido solo el nuestro."
            if getattr(imagen_fotos, 'sin_escudo', False) else '')
    tel = _tel_ruben()
    if solo_whatsapp:
        if not tel:
            return False, 'No encuentro el teléfono de Rubén Pozón en PERFILES'
        ok, det = enviar_imagen(f'{tel}@c.us', png, f'🧪 PRUEBA alineación vs {rival} (no se ha subido a Instagram){nota}')
        return (True, 'Imagen enviada a Rubén Pozón') if ok else (False, det)
    try:
        from instagram_historias import subir_historia
        ok, det = subir_historia(png)
    except Exception as e:
        ok, det = False, str(e)[:200]
    texto = (f"✅ Alineación del AFICIONADO A vs {rival} subida a la historia de Instagram" if ok else
             f"⚠️ No se ha podido subir la alineación del AFICIONADO A vs {rival} a Instagram: {det}") + nota
    if tel:
        enviar_imagen(f'{tel}@c.us', png, texto)
    print(f'[ALINEACION] {texto}', flush=True)
    return ok, texto


def tick(ahora):
    """Cada minuto (push_scheduler): a las 07:00 mira si hoy juega el Aficionado A; desde 30 min antes del partido,
    cada 2 min busca la alineación; a los 10 min del inicio sin alineación, avisa y lo deja.
    Cada 10 min, en segundo plano, quita el fondo a las fotos nuevas de la carpeta."""
    global _recortando
    if ahora.minute % 10 == 7 and not _recortando:
        import threading

        def _job():
            global _recortando
            try:
                preparar_recortes()
            except Exception as e:
                print(f'[ALINEACION] recortes: {e}', flush=True)
            finally:
                _recortando = False
        _recortando = True
        threading.Thread(target=_job, daemon=True).start()
    d = _leer()
    if not d.get('activo'):
        return
    hoy = ahora.date().isoformat()
    if d.get('consultado') != hoy and ahora.hour >= 7:
        d['consultado'] = hoy
        _escribir(d)
        try:
            for p in partidos_del_dia(ahora.date()):
                clave = f"{p['fecha']}_{p['hora']}"
                d = _leer()
                d['partidos'].setdefault(clave, {**p, 'estado': 'pendiente'})
                _escribir(d)
        except Exception as e:
            print(f'[ALINEACION] partidos de hoy: {e}', flush=True)
        return
    for clave, p in list(d['partidos'].items()):
        if p.get('estado') != 'pendiente':
            continue
        inicio = datetime.strptime(f"{p['fecha']} {p['hora']}", '%d-%m-%Y %H:%M')
        if ahora < inicio - timedelta(minutes=ANTES_MIN) or ahora.minute % CADA_MIN:
            continue
        if ahora > inicio + timedelta(minutes=DESPUES_MIN):
            p['estado'] = 'sin_alineacion'
            d['partidos'][clave] = p
            _escribir(d)
            from app import enviar_whatsapp
            rival = _rival(p['fuera'] if p['local'] else p['casa'])
            enviar_whatsapp(_tel_ruben(), f"⚠️ A los {DESPUES_MIN} minutos del inicio no ha salido en la Federación la alineación "
                                          f"del AFICIONADO A vs {rival}: no se ha subido nada a Instagram.")
            continue
        try:
            datos = alineacion(p)
        except Exception as e:
            print(f'[ALINEACION] {clave}: {e}', flush=True)
            continue
        if not datos:
            continue
        p['estado'] = 'subiendo'  # marcar antes de subir: nunca dos veces
        d['partidos'][clave] = p
        _escribir(d)
        ok, texto = publicar(p, datos)
        d = _leer()
        d['partidos'][clave].update({'estado': 'subida' if ok else 'error', 'resultado': texto,
                                     'en': ahora.isoformat(timespec='minutes')})
        _escribir(d)


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@alineacion_bp.route('/api/whatsapp/alineacion', methods=['GET', 'POST'])
def api_estado():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    d = _leer()
    if request.method == 'POST' and 'activo' in (request.json or {}):
        d['activo'] = bool(request.json['activo'])
        _escribir(d)
    partidos = sorted(d['partidos'].values(), key=lambda p: datetime.strptime(f"{p['fecha']} {p['hora']}", '%d-%m-%Y %H:%M'),
                      reverse=True)[:5]
    return jsonify({'status': 'ok', 'activo': d['activo'], 'partidos': [
        {'fecha': p['fecha'], 'hora': p['hora'], 'rival': _rival(p['fuera'] if p['local'] else p['casa']),
         'estado': p.get('estado'), 'resultado': p.get('resultado', '')} for p in partidos]})


def _ultimo_partido():
    """Último partido del Aficionado A ya jugado (en las 3 últimas semanas), para la prueba."""
    sess = _sesion()
    hoy = date.today()
    for i in range(0, 22):
        dia = hoy - timedelta(days=i)
        ps = partidos_del_dia(dia, sess)
        if ps:
            return ps[0], sess
    return None, sess


@alineacion_bp.route('/api/whatsapp/alineacion/prueba', methods=['POST'])
def api_prueba():
    """Imagen con la alineación del último partido, solo por WhatsApp a Rubén (no se sube a Instagram)."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        p, sess = _ultimo_partido()
        if not p:
            return jsonify({'status': 'error', 'message': 'No encuentro ningún partido reciente del Aficionado A'})
        datos = alineacion(p, sess)
        if not datos:
            return jsonify({'status': 'error', 'message': f"La Federación no tiene la alineación del {p['fecha']}"})
        ok, det = publicar(p, datos, solo_whatsapp=True)
        if not ok:
            return jsonify({'status': 'error', 'message': det})
        return jsonify({'status': 'ok', 'message': f"Enviada a Rubén Pozón la alineación del {p['fecha']} vs {datos[0]}"})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]})


@alineacion_bp.route('/api/whatsapp/alineacion/imagen')
def api_imagen():
    """Ver la imagen del último partido en el navegador."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        p, sess = _ultimo_partido()
        datos = alineacion(p, sess) if p else None
        if not datos:
            return jsonify({'status': 'error', 'message': 'No hay alineación reciente'})
        return Response(imagen_elegida(p, datos), mimetype='image/png', headers={'Cache-Control': 'no-store'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]})
