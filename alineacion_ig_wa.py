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
    d.setdefault('activo', False)
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


def publicar(p, datos, solo_whatsapp=False):
    """Monta la imagen, la sube a la historia (salvo prueba) y avisa a Rubén. Devuelve (ok, texto)."""
    from objetivos_finde import enviar_imagen
    from app import enviar_whatsapp
    rival, tit, sup = datos
    png = imagen(rival, tit, sup)
    tel = _tel_ruben()
    if solo_whatsapp:
        if tel:
            enviar_imagen(f'{tel}@c.us', png, f'🧪 PRUEBA alineación vs {rival} (no se ha subido a Instagram)')
        return True, 'Imagen enviada a Rubén Pozón'
    try:
        from instagram_historias import subir_historia
        ok, det = subir_historia(png)
    except Exception as e:
        ok, det = False, str(e)[:200]
    texto = (f"✅ Alineación del AFICIONADO A vs {rival} subida a la historia de Instagram" if ok else
             f"⚠️ No se ha podido subir la alineación del AFICIONADO A vs {rival} a Instagram: {det}")
    if tel:
        enviar_imagen(f'{tel}@c.us', png, texto)
    print(f'[ALINEACION] {texto}', flush=True)
    return ok, texto


def tick(ahora):
    """Cada minuto (push_scheduler): a las 07:00 mira si hoy juega el Aficionado A; desde 30 min antes del partido,
    cada 2 min busca la alineación; a los 10 min del inicio sin alineación, avisa y lo deja."""
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
        publicar(p, datos, solo_whatsapp=True)
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
        return Response(imagen(*datos), mimetype='image/png', headers={'Cache-Control': 'no-store'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]})
