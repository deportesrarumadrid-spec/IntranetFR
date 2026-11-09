"""Goles del Aficionado A en directo → historia de Instagram.

Rubén Pozón o Joaquín Escriche escriben al WhatsApp del club "aficionado a gol de juan" (o "senior a 1-0 gol de juan",
"aficionado a gol del rival 1-1"). Solo cuenta durante el partido del Aficionado A (desde el inicio hasta 2 h después).
- Gol nuestro: se coge la celebración del jugador de la carpeta de Drive (la sube Rubén con fondo verde, que aquí se
  quita; si encajan varias se pregunta el apellido), se monta la historia y se manda a los dos; el primer OK la sube.
  Si no hay celebración de ese jugador, se avisa y se monta solo con el resultado.
- Gol del rival: historia sin jugador; igual, se sube con el primer OK.
- Sin resultado en el mensaje se pregunta. Otro gol en menos de 8 min → se pregunta si es OTRO o el MISMO.
- Recordatorio a los 5 y 10 min si nadie contesta; a los 30 min se deja sin subir.
- Bloqueo de fichero: dos mensajes a la vez (dos workers) nunca crean dos goles ni suben dos veces.
Estado en datos_privados/gol/goles.json.
"""
import io
import os
import re
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify

gol_live_bp = Blueprint('gol_live_wa', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(BASE_DIR, 'datos_privados', 'gol')
DATA_FILE = os.path.join(DIR, 'goles.json')
LOCK_FILE = os.path.join(DIR, '.goles.lock')
FOTOS_CACHE = os.path.join(DIR, 'drive')
AUTORIZADOS = ('RUBEN POZON', 'JOAQUIN ESCRICHE')
CORREO_CARPETA = 'infoclubfuentelarreyna@gmail.com'
NOMBRE_CARPETA = 'FOTOS GOL AFICIONADO A'
VENTANA_DUPLICADO_MIN = 8
DURACION_PARTIDO_H = 2


def _norm(s):
    from gol_ig_wa import _norm as n
    return n(s)


_hilos = threading.Lock()


@contextmanager
def _bloqueo():
    """Bloqueo entre hilos (mismo proceso) y entre workers de gunicorn (fichero)."""
    os.makedirs(DIR, exist_ok=True)
    with _hilos:
        f = open(LOCK_FILE, 'w')
        try:
            try:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX)
            except ImportError:  # Windows (pruebas locales)
                pass
            yield
        finally:
            f.close()


def _leer():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault('goles', [])
    d.setdefault('esperando', {})  # tel → {'gol': id, 'que': 'resultado'|'apellido'|'duplicado'|'eleccion'|'confirmar'}
    return d


def _escribir(d):
    os.makedirs(DIR, exist_ok=True)
    d['goles'] = d['goles'][-80:]
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def _tel9(t):
    return ''.join(c for c in str(t or '') if c.isdigit())[-9:]


def _wa(tel):
    t = ''.join(c for c in str(tel) if c.isdigit())
    return '34' + t if len(t) == 9 else t


def autorizados():
    """[(usuario, telefono)] de Rubén Pozón y Joaquín Escriche (PERFILES)."""
    from app import client, NOMBRE_EXCEL
    p = client.open(NOMBRE_EXCEL).worksheet('PERFILES').get_all_values()
    h = [x.strip().upper() for x in p[0]]
    i_u, i_t = h.index('USUARIO'), h.index('TELEFONO')
    out = []
    for buscado in AUTORIZADOS:
        r = next((r for r in p[1:] if len(r) > max(i_u, i_t) and _norm(r[i_u]) == buscado and r[i_t].strip()), None)
        if r:
            out.append((r[i_u].strip(), r[i_t].strip()))
    return out


def _texto(tel, txt):
    from whatsapp_envios import enviar_por_waha
    enviar_por_waha(_wa(tel), txt)


# ---------- carpeta de Drive con las fotos ----------

def _drive():
    from google.oauth2.service_account import Credentials
    from google.auth.transport.requests import AuthorizedSession
    creds = Credentials.from_service_account_file(os.path.join(BASE_DIR, 'secretos.json'),
                                                  scopes=['https://www.googleapis.com/auth/drive'])
    return AuthorizedSession(creds)


def carpeta():
    """Id de la carpeta de fotos (la crea la intranet la primera vez y la comparte con el club)."""
    with _bloqueo():
        d = _leer()
        if d.get('carpeta_id'):
            return d['carpeta_id']
    s = _drive()
    r = s.post('https://www.googleapis.com/drive/v3/files', json={'name': NOMBRE_CARPETA, 'mimeType': 'application/vnd.google-apps.folder'})
    r.raise_for_status()
    fid = r.json()['id']
    s.post(f'https://www.googleapis.com/drive/v3/files/{fid}/permissions', params={'sendNotificationEmail': 'true'},
           json={'type': 'user', 'role': 'writer', 'emailAddress': CORREO_CARPETA}).raise_for_status()
    with _bloqueo():
        d = _leer()
        d['carpeta_id'] = fid
        _escribir(d)
    return fid


def fotos():
    """[{'id', 'nombre'}] de las fotos de la carpeta ('Jaime Gimenez.jpg' → nombre 'JAIME GIMENEZ')."""
    fid = carpeta()
    r = _drive().get('https://www.googleapis.com/drive/v3/files', params={
        'q': f"'{fid}' in parents and trashed=false and mimeType contains 'image/'",
        'fields': 'files(id,name,modifiedTime)', 'pageSize': 200})
    r.raise_for_status()
    out = []
    for f in r.json().get('files', []):
        nombre = ' '.join(re.sub(r'[_\-]+', ' ', os.path.splitext(f['name'])[0]).split()).upper()
        out.append({'id': f['id'], 'nombre': nombre, 'mod': f.get('modifiedTime', '')})
    return sorted(out, key=lambda x: _norm(x['nombre']))


def foto_bytes(f):
    """Bytes de la imagen de la celebración (caché local hasta que cambie en Drive)."""
    os.makedirs(FOTOS_CACHE, exist_ok=True)
    ruta = os.path.join(FOTOS_CACHE, f"{f['id']}_{re.sub(r'[^0-9]', '', f.get('mod', ''))}.img")
    if not os.path.exists(ruta):
        r = _drive().get(f"https://www.googleapis.com/drive/v3/files/{f['id']}", params={'alt': 'media'})
        r.raise_for_status()
        with open(ruta, 'wb') as fh:
            fh.write(r.content)
    with open(ruta, 'rb') as fh:
        return fh.read()


def recorte(datos):
    """Jugador recortado (RGBA): si la imagen ya trae transparencia se usa tal cual; si no, se quita el fondo verde."""
    from PIL import Image, ImageOps
    from gol_ig_wa import quitar_verde
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(datos)))
    if im.mode in ('RGBA', 'LA', 'P') and im.convert('RGBA').getextrema()[3][0] < 200:
        im = im.convert('RGBA')
        return im.crop(im.split()[3].point(lambda v: 255 if v > 128 else 0).getbbox() or (0, 0, im.width, im.height))
    im.thumbnail((2000, 2000))
    buf = io.BytesIO()
    im.convert('RGB').save(buf, 'PNG')
    return quitar_verde(buf.getvalue())


def buscar_jugador(consulta, lista):
    """Fotos cuyo nombre encaja con lo escrito ("juan", "juan perez")."""
    palabras = [w for w in _norm(consulta).split() if len(w) > 1]
    if not palabras:
        return []
    exactos = [f for f in lista if _norm(f['nombre']) == ' '.join(palabras)]
    return exactos or [f for f in lista if all(w in _norm(f['nombre']).split() for w in palabras)]


# ---------- partido y mensaje ----------

def partido_en_juego(ahora):
    """Partido del Aficionado A en juego ahora (desde el inicio hasta 2 h después), o None."""
    import alineacion_ig_wa as al
    hoy = ahora.strftime('%d-%m-%Y')
    partidos = [p for p in al._leer().get('partidos', {}).values() if p.get('fecha') == hoy]
    if not partidos:
        with _bloqueo():
            d = _leer()
            cache = d.get('partidos_hoy', {})
        if cache.get('fecha') != hoy:
            try:
                cache = {'fecha': hoy, 'lista': al.partidos_del_dia(ahora.date())}
            except Exception as e:
                print(f'[GOL] partidos de hoy: {e}', flush=True)
                cache = {'fecha': hoy, 'lista': []}
            with _bloqueo():
                d = _leer()
                d['partidos_hoy'] = cache
                _escribir(d)
        partidos = cache['lista']
    for p in partidos:
        inicio = datetime.strptime(f"{p['fecha']} {p['hora']}", '%d-%m-%Y %H:%M')
        if inicio <= ahora <= inicio + timedelta(hours=DURACION_PARTIDO_H):
            return {**p, 'inicio': inicio.isoformat(timespec='minutes')}
    return None


def minuto(p, ahora, texto=''):
    m = re.search(r"\b(\d{1,3})\s*(?:'|min\b|minuto)", texto.lower()) or re.search(r'\bmin(?:uto)?\s*(\d{1,3})\b', texto.lower())
    if m:
        return int(m.group(1))
    trans = int((ahora - datetime.fromisoformat(p['inicio'])).total_seconds() // 60) + 1
    return max(1, trans - 15 if trans > 50 else trans)  # 15 min de descanso


def entender(cuerpo):
    """{'rival': bool, 'resultado': '1-0'|None, 'jugador': texto} si es un mensaje de gol del Aficionado A, o None."""
    t = _norm(cuerpo)
    if 'GOL' not in t.split() and not t.startswith('GOL'):
        return None
    if not re.search(r'\b(AFICIONADO|SENIOR)\s*A\b', t):
        return None
    m = re.search(r'\b(\d{1,2})\s*[-–]\s*(\d{1,2})\b', cuerpo)
    resultado = f'{int(m.group(1))}-{int(m.group(2))}' if m else None
    rival = 'RIVAL' in t
    resto = re.sub(r'\b(AFICIONADO|SENIOR)\s*A\b', ' ', t)
    resto = re.sub(r'\b\d{1,2}\s*[-–]\s*\d{1,2}\b', ' ', resto)
    resto = re.sub(r"\b\d{1,3}\s*'?\s*(MIN|MINUTO)?\b|\bMIN(UTO)?\b", ' ', resto)
    resto = ' '.join(w for w in re.sub(r'[^A-Z ]', ' ', resto).split() if w not in ('GOL', 'GOOL', 'GOOOL', 'DE', 'DEL', 'EL', 'LA', 'RIVAL'))
    return {'rival': rival, 'resultado': resultado, 'jugador': resto}


def _resultado_de(texto):
    m = re.search(r'\b(\d{1,2})\s*[-–]\s*(\d{1,2})\b', texto or '')
    return f'{int(m.group(1))}-{int(m.group(2))}' if m else None


# ---------- flujo ----------

def _nuevo_id(d):
    return f"g{datetime.now():%Y%m%d%H%M%S}{len(d['goles'])}"


def _gol(d, gid):
    return next((g for g in d['goles'] if g['id'] == gid), None)


def _destinos(g):
    if g.get('prueba'):
        return [(g['pedido_por'], g['pedido_tel'])]
    return autorizados()


def procesar(tel, cuerpo, usuario):
    """Mensaje privado. True si era de goles (ya contestado)."""
    if not cuerpo or _norm(usuario) not in AUTORIZADOS:
        return False
    ahora = datetime.now()
    with _bloqueo():
        d = _leer()
        esp = d['esperando'].get(_tel9(tel))
    nuevo = entender(cuerpo)
    if esp and not nuevo:
        return _respuesta(tel, cuerpo, usuario, esp)
    if not nuevo:
        # "OK" / "no" cuando ya lo ha decidido el otro (o ha caducado): se le dice, sin hacer nada
        if re.match(r'^\s*(OK|SI|NO|VALE)\b', _norm(cuerpo)):
            with _bloqueo():
                d = _leer()
            recien = [g for g in d['goles'] if g.get('enviado_en') and g['estado'] in ('subiendo', 'subido', 'cancelado', 'caducado')
                      and ahora - datetime.fromisoformat(g['enviado_en']) < timedelta(minutes=45)]
            if recien:
                g = recien[-1]
                if g['estado'] == 'caducado':
                    _texto(tel, f"⌛ El gol {g['resultado']} ya caducó (30 min sin contestar): no se ha subido.")
                else:
                    quien = (g.get('elegido_por') or '').split()[0].capitalize() or 'otro'
                    _texto(tel, f'👌 Ya lo ha decidido {quien}: no hago nada más.')
                return True
        return False
    p = partido_en_juego(ahora)
    if not p:
        _texto(tel, '⚽ Ahora no está en juego ningún partido del Aficionado A (cuenta desde el inicio hasta 2 h después): no se sube nada.')
        return True
    return _nuevo_gol(tel, usuario, cuerpo, nuevo, p, ahora)


def _nuevo_gol(tel, usuario, cuerpo, nuevo, p, ahora, prueba=False):
    rival = (p['fuera'] if p['local'] else p['casa'])
    g = {'tipo': 'rival' if nuevo['rival'] else 'nuestro', 'resultado': nuevo['resultado'], 'consulta': nuevo['jugador'],
         'pedido_por': usuario, 'pedido_tel': _tel9(tel), 'creado': ahora.isoformat(timespec='seconds'),
         'minuto': minuto(p, ahora, cuerpo), 'fecha': p['fecha'], 'rival_nombre': rival, 'prueba': prueba, 'estado': 'nuevo'}
    with _bloqueo():
        d = _leer()
        g['id'] = _nuevo_id(d)
        # ¿Otro gol en los últimos 8 minutos? Ante la duda, se pregunta
        recientes = [x for x in d['goles'] if bool(x.get('prueba')) == prueba and x['estado'] not in ('cancelado', 'duplicado', 'error')
                     and ahora - datetime.fromisoformat(x['creado']) < timedelta(minutes=VENTANA_DUPLICADO_MIN)]
        d['goles'].append(g)
        if recientes and not prueba:
            r = recientes[-1]
            g['estado'] = 'duda_duplicado'
            g['duplicado_de'] = r['id']
            d['esperando'][_tel9(tel)] = {'gol': g['id'], 'que': 'duplicado'}
            _escribir(d)
            quien = r['pedido_por'].split()[0].capitalize()
            hace = int((ahora - datetime.fromisoformat(r['creado'])).total_seconds() // 60)
            desc = 'gol del rival' if r['tipo'] == 'rival' else f"gol de {r.get('jugador_nombre') or r.get('consulta') or '?'}"
            _texto(tel, f"⚠️ {quien} ya ha pedido hace {hace} min el {desc}{' (' + r['resultado'] + ')' if r.get('resultado') else ''}. "
                        f"¿Es *OTRO* gol o el *MISMO*? Responde OTRO o MISMO.")
            return True
        _escribir(d)
    return _seguir(g['id'], tel)


def _seguir(gid, tel):
    """Avanza un gol: pide lo que falte (resultado, apellido) o lo prepara."""
    with _bloqueo():
        d = _leer()
        g = _gol(d, gid)
    if not g.get('resultado'):
        _esperar(gid, tel, 'resultado')
        _texto(tel, '¿Qué resultado es? (local-visitante, por ejemplo *1-0*)')
        return True
    if g['tipo'] == 'nuestro' and not g.get('foto_id'):
        try:
            lista = fotos()
        except Exception as e:
            _texto(tel, f'⚠️ No puedo leer la carpeta de fotos de Drive: {str(e)[:120]}')
            return True
        cand = buscar_jugador(g.get('consulta', ''), lista)
        if len(cand) > 1:
            _esperar(gid, tel, 'apellido')
            _texto(tel, '¿Cuál? ' + ' / '.join(f['nombre'].title() for f in cand[:6]) + '. Dime el apellido.')
            return True
        with _bloqueo():
            d = _leer()
            x = _gol(d, gid)
            if cand:
                x.update({'foto_id': cand[0]['id'], 'foto_mod': cand[0]['mod'], 'jugador_nombre': cand[0]['nombre']})
            else:  # sin celebración de ese jugador: solo el resultado
                x.update({'foto_id': 'ninguna', 'jugador_nombre': ' '.join((g.get('consulta') or '').split()).upper()})
            _escribir(d)
        if not cand:
            _texto(tel, f"⚠️ No hay celebración de *{(g.get('consulta') or '?').title()}* en la carpeta: se monta solo con el resultado.")
    with _bloqueo():
        d = _leer()
        x = _gol(d, gid)
        if x['estado'] not in ('nuevo', 'duda_duplicado'):
            return True  # ya en marcha
        x['estado'] = 'generando'
        for t in list(d['esperando']):
            if d['esperando'][t].get('gol') == gid:
                d['esperando'].pop(t)
        _escribir(d)
    threading.Thread(target=_preparar, args=(gid,), daemon=True).start()
    _texto(tel, '⏳ Preparando la imagen…')
    return True


def _esperar(gid, tel, que):
    with _bloqueo():
        d = _leer()
        d['esperando'][_tel9(tel)] = {'gol': gid, 'que': que}
        _escribir(d)


def _respuesta(tel, cuerpo, usuario, esp):
    t = _norm(cuerpo)
    gid, que = esp['gol'], esp['que']
    with _bloqueo():
        d = _leer()
        g = _gol(d, gid)
    if not g:
        return False
    if que == 'resultado':
        r = _resultado_de(cuerpo)
        if not r:
            _texto(tel, 'No lo he entendido. Escribe el resultado así: *1-0* (local-visitante).')
            return True
        with _bloqueo():
            d = _leer()
            _gol(d, gid)['resultado'] = r
            d['esperando'].pop(_tel9(tel), None)
            _escribir(d)
        return _seguir(gid, tel)
    if que == 'apellido':
        with _bloqueo():
            d = _leer()
            _gol(d, gid)['consulta'] = f"{g.get('consulta', '')} {cuerpo}" if len(cuerpo.split()) == 1 and g.get('consulta') else cuerpo
            d['esperando'].pop(_tel9(tel), None)
            _escribir(d)
        return _seguir(gid, tel)
    if que == 'duplicado':
        if t.startswith(('MISMO', 'EL MISMO', 'ES EL MISMO', 'NO')):
            with _bloqueo():
                d = _leer()
                x = _gol(d, gid)
                x['estado'] = 'duplicado'
                d['esperando'].pop(_tel9(tel), None)
                orig = _gol(d, x.get('duplicado_de', ''))
                if orig and orig['estado'] in ('esperando_eleccion', 'esperando_confirmar'):  # que pueda seguir eligiendo
                    d['esperando'][_tel9(tel)] = {'gol': orig['id'], 'que': orig['estado'].replace('esperando_', '')}
                _escribir(d)
            _texto(tel, '👌 Entonces no hago nada, ya está en marcha.')
            return True
        if t.startswith(('OTRO', 'ES OTRO', 'SI')):
            with _bloqueo():
                d = _leer()
                x = _gol(d, gid)
                x['estado'] = 'nuevo'
                d['esperando'].pop(_tel9(tel), None)
                _escribir(d)
            return _seguir(gid, tel)
        _texto(tel, 'Responde *OTRO* (es un gol nuevo) o *MISMO* (es el mismo gol).')
        return True
    if que in ('eleccion', 'confirmar'):
        return _elegir(tel, cuerpo, usuario, gid, que)
    return False


def _preparar(gid):
    """Monta la historia del gol (con la celebración del jugador si la hay) y la manda para OK/NO."""
    import gol_ig_wa as gi
    from objetivos_finde import enviar_imagen
    with _bloqueo():
        d = _leer()
        g = dict(_gol(d, gid))
    rival = ' '.join(g['rival_nombre'].replace('"', ' ').replace("'", ' ').split()).upper()
    fecha = gi.fecha_txt(datetime.strptime(g['fecha'], '%d-%m-%Y').date())
    os.makedirs(os.path.join(DIR, 'opciones'), exist_ok=True)
    ruta = os.path.join(DIR, 'opciones', f'{gid}.png')
    try:
        jugador = None
        if g['tipo'] == 'nuestro' and g.get('foto_id') not in (None, 'ninguna'):
            jugador = recorte(foto_bytes({'id': g['foto_id'], 'mod': g.get('foto_mod', '')}))
        goleador = gi.nombre_corto(g.get('jugador_nombre') or '') if g['tipo'] == 'nuestro' else ''
        png = gi.imagen_gol(g['resultado'], 'CLUB FUENTELARREYNA A', rival, fecha, goleador, f"MIN {g['minuto']}'", jugador)
        with open(ruta, 'wb') as f:
            f.write(png)
    except Exception as e:
        with _bloqueo():
            d = _leer()
            _gol(d, gid)['estado'] = 'error'
            _escribir(d)
        _texto(g['pedido_tel'], f'⚠️ No se ha podido preparar la imagen del gol: {str(e)[:150]}')
        return
    destinos = _destinos(g)
    with _bloqueo():
        d = _leer()
        x = _gol(d, gid)
        x.update({'estado': 'esperando_confirmar', 'opciones': [ruta], 'enviado_en': datetime.now().isoformat(timespec='seconds'),
                  'recordatorios': 0})
        for _, tel in destinos:
            d['esperando'][_tel9(tel)] = {'gol': gid, 'que': 'confirmar'}
        _escribir(d)
    pre = '🧪 PRUEBA · ' if g.get('prueba') else ''
    que = 'Gol del rival' if g['tipo'] == 'rival' else f"Gol de {(g.get('jugador_nombre') or '?').title()}"
    for _, tel in destinos:
        enviar_imagen(f'{_wa(tel)}@c.us', png, f"{pre}{que} ({g['resultado']}, min {g['minuto']}). "
                                                 f"¿La subo a Instagram? Responde *OK* o *NO*.")


def _elegir(tel, cuerpo, usuario, gid, que):
    t = _norm(cuerpo)
    with _bloqueo():
        d = _leer()
        g = _gol(d, gid)
        if g['estado'] not in ('esperando_eleccion', 'esperando_confirmar'):
            d['esperando'].pop(_tel9(tel), None)
            _escribir(d)
            quien = (g.get('elegido_por') or '').split()[0].capitalize() if g.get('elegido_por') else 'otro'
            _texto(tel, f'👌 Ya lo ha decidido {quien}: no hago nada más.')
            return True
        if t.startswith(('NO', 'NINGUNA')):
            eleccion = 0
        elif t.startswith(('OK', 'SI', 'VALE', 'SUBE', 'DALE')):
            eleccion = 1
        else:
            _texto(tel, 'Responde *OK* para subirla o *NO*.')
            return True
        # La primera respuesta manda: se marca dentro del bloqueo, así nunca se sube dos veces
        g.update({'estado': 'subiendo' if eleccion else 'cancelado', 'elegido_por': usuario, 'eleccion': eleccion})
        for t9 in list(d['esperando']):
            if d['esperando'][t9].get('gol') == gid:
                d['esperando'].pop(t9)
        _escribir(d)
    destinos = _destinos(g)
    otros = [tl for _, tl in destinos if _tel9(tl) != _tel9(tel)]
    if not eleccion:
        _texto(tel, '👌 No se sube nada.')
        for o in otros:
            _texto(o, f"👌 {usuario.split()[0].capitalize()} ha decidido no subir el gol {g['resultado']}.")
        return True
    threading.Thread(target=_subir, args=(gid, eleccion, tel, otros, usuario), daemon=True).start()
    _texto(tel, '⏳ Subiendo a Instagram…')
    return True


def _subir(gid, eleccion, tel, otros, usuario):
    with _bloqueo():
        d = _leer()
        g = dict(_gol(d, gid))
    png = open(g['opciones'][eleccion - 1], 'rb').read()
    if g.get('prueba'):
        ok, det = True, 'prueba'
    else:
        try:
            from instagram_historias import subir_historia
            ok, det = subir_historia(png)
        except Exception as e:
            ok, det = False, str(e)[:150]
    with _bloqueo():
        d = _leer()
        _gol(d, gid).update({'estado': 'subido' if ok else 'error', 'subido_en': datetime.now().isoformat(timespec='seconds')})
        _escribir(d)
    if g.get('prueba'):
        txt = '🧪 PRUEBA: aquí se subiría a Instagram (no se ha subido nada).'
    else:
        txt = (f"✅ Gol {g['resultado']} subido a la historia de Instagram." if ok else
               f'⚠️ No se ha podido subir a Instagram: {det}')
    _texto(tel, txt)
    for o in otros:
        _texto(o, f"{txt} Le dio OK {usuario.split()[0].capitalize()}.")


def tick(ahora):
    """Recordatorios a los 5 y 10 min si nadie contesta; a los 30 min se deja sin subir."""
    with _bloqueo():
        d = _leer()
        pendientes = [g for g in d['goles'] if g['estado'] in ('esperando_eleccion', 'esperando_confirmar') and g.get('enviado_en')]
    for g in pendientes:
        pasado = (ahora - datetime.fromisoformat(g['enviado_en'])).total_seconds() / 60
        if pasado >= 30:
            with _bloqueo():
                d = _leer()
                x = _gol(d, g['id'])
                if x['estado'].startswith('esperando'):
                    x['estado'] = 'caducado'
                    for t9 in list(d['esperando']):
                        if d['esperando'][t9].get('gol') == g['id']:
                            d['esperando'].pop(t9)
                _escribir(d)
            for _, tel in _destinos(g):
                _texto(tel, f"⌛ Nadie ha contestado en 30 min: el gol {g['resultado']} no se sube.")
            continue
        toca = 2 if pasado >= 10 else 1 if pasado >= 5 else 0
        if toca > g.get('recordatorios', 0):
            with _bloqueo():
                d = _leer()
                _gol(d, g['id'])['recordatorios'] = toca
                _escribir(d)
            for _, tel in _destinos(g):
                _texto(tel, f"⏰ Recordatorio: gol {g['resultado']} pendiente. Responde OK para subirlo o NO.")


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@gol_live_bp.route('/api/gol/carpeta', methods=['GET', 'POST'])
def api_carpeta():
    """GET: enlace y fotos de la carpeta (la crea si no existe)."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        fid = carpeta()
        lista = fotos()
        with _bloqueo():
            goles = [{k: g.get(k) for k in ('creado', 'tipo', 'resultado', 'jugador_nombre', 'minuto', 'estado', 'pedido_por',
                                             'elegido_por', 'eleccion', 'prueba')} for g in _leer()['goles'][-8:]][::-1]
        return jsonify({'status': 'ok', 'url': f'https://drive.google.com/drive/folders/{fid}',
                        'fotos': [f['nombre'] for f in lista], 'goles': goles, 'correo': CORREO_CARPETA})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Drive: {e}'[:250]})


@gol_live_bp.route('/api/gol/prueba', methods=['POST'])
def api_prueba():
    """Simula "aficionado a 1-0 gol de <jugador>" ahora mismo: las opciones te llegan solo a ti y no se sube nada."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    from informe_veo import _telefono_ruben
    texto = (request.json or {}).get('texto', '')
    nuevo = entender(texto)
    if not nuevo:
        return jsonify({'status': 'error', 'message': 'Escríbelo como: aficionado a 1-0 gol de jaime'})
    tel = _telefono_ruben()
    ahora = datetime.now()
    p = {'fecha': ahora.strftime('%d-%m-%Y'), 'hora': (ahora - timedelta(minutes=34)).strftime('%H:%M'), 'local': True,
         'casa': 'CLUB FUENTELARREYNA "A"', 'fuera': 'RIVAL DE PRUEBA', 'inicio': (ahora - timedelta(minutes=34)).isoformat(timespec='minutes')}
    _nuevo_gol(tel, 'RUBEN POZON', texto, nuevo, p, ahora, prueba=True)
    return jsonify({'status': 'ok', 'message': 'Prueba en marcha: te llegará por WhatsApp (contesta como si fuera de verdad)'})
