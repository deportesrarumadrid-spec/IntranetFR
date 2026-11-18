"""Aviso de cumpleaños a los entrenadores (WhatsApp privado, el mismo día a las 09:00).

- Fechas de nacimiento: licencias de la Federación (intranet.ffmadrid.es, página de licencias de cada equipo), solo la
  lista "en situación de alta y licencia en vigor", solo jugadores (no auxiliares), estado Activa.
- Los códigos de nuestros equipos salen del Listado de Partidos de la temporada (Federación).
- Se lee la Federación una vez por semana (lunes 04:00) o al pulsar LEER FEDERACIÓN. Solo se guarda nombre, fecha de
  nacimiento y equipo, en datos_privados/cumpleanos.json (nunca DNI ni datos médicos).
- A cada entrenador con teléfono en PERFILES de ese equipo: un mensaje por equipo con todos los que cumplen ese día.
"""
import os
import re
import json
import threading
import unicodedata
from datetime import datetime, date, timedelta
from flask import Blueprint, request, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'datos_privados', 'cumpleanos.json')
URL_LIC = 'https://intranet.ffmadrid.es/nfg/NPcd/NFG_GC_VisLicenciasEquipo'
COD_PRIMARIA_LIC = '1000188'
HORA_ENVIO = (9, 0)
LECTURA = (0, 4)  # lunes a las 04:00 se vuelve a leer la Federación
# Licencias de cuerpo técnico / auxiliares (por si van mezclados con los jugadores)
LIC_STAFF = {'E', 'EPR', 'EN', 'ENT', 'D', 'DE', 'DEL', 'AUX', 'AU', 'M', 'MED', 'FIS', 'PF', 'PRE', 'T', 'TEC', 'UT', 'ENF', 'EA'}
CATS = ('PREBENJAMIN', 'BENJAMIN', 'ALEVIN', 'INFANTIL', 'CADETE', 'JUVENIL', 'AFICIONADO', 'CHUPETIN')

cumple_bp = Blueprint('cumpleanos', __name__)
_ocupado = threading.Lock()


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.upper().split())


def _bonito(equipo):
    return (' '.join(str(equipo).upper().split()).replace('PREBENJAMIN', 'PREBENJAMÍN')
            .replace('BENJAMIN', 'BENJAMÍN').replace('ALEVIN', 'ALEVÍN'))


def _leer():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault('activo', False)
    d.setdefault('equipos', [])
    return d


def _escribir(d):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


# ---------- equipos: clave (categoría, letra, F7, femenino) ----------

def _clave(texto, letra=''):
    """('ALEVIN', 'A', True, False) a partir de 'ALEVIN A F7', 'ALEVÍN F-7 ... "A"', etc."""
    n = _norm(texto)
    cat = next((c for c in CATS if c in n), None)
    f7 = bool(re.search(r'\bF-?7\b|FUTBOL-?7|FUTBOL 7', n))
    fem = 'FEM' in n
    if not letra:
        m = re.search(r'"\s*([A-Z])\s*"', n)
        if m:
            letra = m.group(1)
        else:
            resto = re.sub(r'\bF-?7\b|\bF-?11\b|FEMENINO|FEM\b', ' ', n)
            if cat:
                resto = resto.replace(cat, ' ', 1)
            m = re.search(r'\b([A-H])\b', resto)
            letra = m.group(1) if m else 'A'
    return (cat, letra, f7, fem)


def _nombre_equipo(clave):
    cat, letra, f7, fem = clave
    return f"{cat or '?'} {letra}" + (' F7' if f7 else '') + (' FEM' if fem else '')


def _equipos_perfiles():
    """{EQUIPO tal cual en PERFILES: [(usuario, telefono)]} (todos los equipos, también Aficionados)."""
    from app import client, NOMBRE_EXCEL
    p = client.open(NOMBRE_EXCEL).worksheet('PERFILES').get_all_values()
    h = [x.strip().upper() for x in (p[0] if p else [])]
    i_u, i_t, i_e = h.index('USUARIO'), h.index('TELEFONO'), h.index('EQUIPO')
    out = {}
    for r in p[1:]:
        g = lambda i: r[i].strip() if i < len(r) else ''
        if not g(i_u):
            continue
        for e in g(i_e).split(','):
            e = ' '.join(e.upper().split())
            if e and (g(i_u), g(i_t)) not in out.setdefault(e, []):
                out[e].append((g(i_u), g(i_t)))
    return out


def _cruzar(clave, perfiles):
    """Nombre del equipo en PERFILES que corresponde a la clave de la Federación ('' si ninguno)."""
    exactos = [e for e in perfiles if _clave(e) == clave]
    if len(exactos) == 1:
        return exactos[0]
    # sin tener en cuenta F7 (por si en PERFILES no lo pone), solo si no hay duda
    flojos = [e for e in perfiles if _clave(e)[:2] == clave[:2] and _clave(e)[3] == clave[3]]
    return flojos[0] if len(flojos) == 1 else (exactos[0] if exactos else '')


# ---------- Federación ----------

def _sesion():
    import competicion_scraper as cs
    usuario, password = cs.cargar_credenciales_rffm()
    if not usuario:
        raise RuntimeError('No hay credenciales de la Federación guardadas en el servidor')
    s = cs.get_session()
    r = s.post('https://intranet.ffmadrid.es/nfg/NLogin', data={'NUser': usuario, 'NPass': password, 'LoginAjax': '1'}, timeout=30)
    m = re.search(r'var estado="(\d+)"', r.text)
    if not m or m.group(1) != '1':
        raise RuntimeError('La Federación no acepta el usuario y la contraseña guardados')
    return s


def _nuestros_equipos(s):
    """{codigo_equipo: {'competicion', 'nombre_fed'}} de nuestros equipos en el Listado de Partidos de la temporada."""
    import competicion_scraper as cs
    out = {}
    for m in cs.fetch_temporada_matches(s):
        for lado in ('casa', 'fuera'):
            nom = m.get(f'nombre_{lado}', '')
            if 'FUENTELARREYNA' in _norm(nom):
                cod = m.get(f'cod_equipo_{lado}')
                if cod and cod not in out:
                    out[cod] = {'competicion': f"{m.get('deporte', '')}, {m.get('competicion', '')}", 'nombre_fed': nom}
    return out


def _fecha(s):
    m = re.match(r'^\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\s*$', s or '')
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _nombre_persona(s):
    """'ARROYO DELMAS, NICOLAS' → 'Nicolas Arroyo Delmas'."""
    s = ' '.join(str(s or '').split())
    if ',' in s:
        ape, nom = s.split(',', 1)
        s = f'{nom.strip()} {ape.strip()}'
    return ' '.join(w.capitalize() if not re.match(r'^(DE|DEL|LA|LAS|LOS|Y)$', w, re.I) else w.lower()
                    for w in s.split())


def _licencias(s, cod_equipo):
    """Lee la página de licencias del equipo. Devuelve (jugadores [{nombre, fnac}], info de control)."""
    from bs4 import BeautifulSoup
    r = s.get(URL_LIC, params={'cod_primaria': COD_PRIMARIA_LIC, 'Codigo_Equipo': cod_equipo}, timeout=60)
    r.encoding = r.apparent_encoding or 'iso-8859-1'
    soup = BeautifulSoup(r.text, 'html.parser')
    texto = soup.get_text(' ')
    m = re.search(r'\((\d+)\s*J\s*\+\s*(\d+)\s*A\)', texto)
    n_jug = int(m.group(1)) if m else None
    filas = []
    for t in soup.find_all('table'):
        cab = None
        for tr in t.find_all('tr'):
            celdas = [' '.join(c.get_text(' ').split()) for c in tr.find_all(['th', 'td'])]
            nc = [_norm(c) for c in celdas]
            if cab is None:
                if any('NOMBRE' in c for c in nc) and any('F.NAC' in c or 'F. NAC' in c for c in nc) and any('F.FINAL' in c or 'F. FINAL' in c for c in nc):
                    cab = nc  # solo la tabla de licencias en vigor tiene F.Final (la de vinculaciones no)
                continue
            if len(celdas) < len(cab) - 2:
                continue
            col = lambda k: next((celdas[i] for i, c in enumerate(cab) if k in c and i < len(celdas)), '')
            fnac = _fecha(col('F.NAC') or col('F. NAC'))
            nombre = col('NOMBRE')
            if not fnac or not nombre:
                continue
            filas.append({'nombre': nombre, 'fnac': fnac, 'lic': _norm(col('LICENCIA')),
                          'estado': _norm(col('ESTADO')), 'ffinal': _fecha(col('F.FINAL') or col('F. FINAL'))})
        if cab is not None and filas:
            break
    hoy = date.today()
    validas = [f for f in filas if 'ACTIV' in f['estado'] and (not f['ffinal'] or f['ffinal'] >= hoy)]
    jug = [f for f in validas if f['lic'] not in LIC_STAFF]
    # Si sobran filas respecto al "(N J + M A)" de la página, se quitan los adultos más mayores (auxiliares)
    if n_jug is not None and len(jug) > n_jug:
        jug = sorted(jug, key=lambda f: f['fnac'], reverse=True)[:n_jug]
    info = {'filas': len(filas), 'jugadores_pagina': n_jug, 'licencias': sorted({f['lic'] for f in validas})}
    return [{'nombre': _nombre_persona(f['nombre']), 'fnac': f['fnac'].isoformat()} for f in jug], info


def leer_federacion():
    """Lee todos nuestros equipos y guarda nombre + fecha de nacimiento. Devuelve (ok, texto)."""
    s = _sesion()
    equipos = _nuestros_equipos(s)
    if not equipos:
        return False, 'No he encontrado nuestros equipos en el Listado de Partidos de la Federación'
    perfiles = _equipos_perfiles()
    lista, fallos = [], []
    for cod, e in equipos.items():
        clave = _clave(e['competicion'], re.search(r'"\s*([A-Z])\s*"', _norm(e['nombre_fed'])).group(1)
                       if re.search(r'"\s*([A-Z])\s*"', _norm(e['nombre_fed'])) else 'A')
        try:
            jug, info = _licencias(s, cod)
        except Exception as ex:
            fallos.append(f'{_nombre_equipo(clave)}: {ex}'[:150])
            jug, info = [], {}
        en_perfiles = _cruzar(clave, perfiles)
        lista.append({'codigo': cod, 'equipo': _nombre_equipo(clave), 'competicion': e['competicion'],
                      'perfiles': en_perfiles, 'jugadores': jug, 'control': info,
                      'entrenadores': [u + ('' if t else ' (sin teléfono)') for u, t in perfiles.get(en_perfiles, [])]})
    lista.sort(key=lambda x: (CATS.index(x['equipo'].split()[0]) if x['equipo'].split()[0] in CATS else 99, x['equipo']))
    d = _leer()
    d['equipos'] = lista
    d['leido'] = datetime.now().isoformat(timespec='minutes')
    d['fallos_lectura'] = fallos
    _escribir(d)
    total = sum(len(x['jugadores']) for x in lista)
    texto = f'Leídos {total} jugadores de {len(lista)} equipos' + (f' ({len(fallos)} equipos con error)' if fallos else '')
    print(f'[CUMPLE] {texto}', flush=True)
    return True, texto


# ---------- mensajes ----------

def _edad(fnac, dia):
    return dia.year - fnac.year - ((dia.month, dia.day) < (fnac.month, fnac.day))


def _cumple_hoy(fnac, dia):
    if (fnac.month, fnac.day) == (2, 29) and not (dia.year % 4 == 0 and (dia.year % 100 != 0 or dia.year % 400 == 0)):
        return (dia.month, dia.day) == (2, 28)  # nacidos un 29 de febrero: el 28 los años no bisiestos
    return (fnac.month, fnac.day) == (dia.month, dia.day)


def texto_mensaje(equipo, cumplen, dia):
    """cumplen: [(nombre, fnac)]. Formato aprobado por Rubén Pozón."""
    eq = _bonito(equipo)
    if len(cumplen) == 1:
        n, f = cumplen[0]
        return f'🎂 Hoy cumple años *{n}* ({eq}), cumple {_edad(f, dia)} años. ¡No olvides felicitarle!'
    lineas = '\n'.join(f'• *{n}*, cumple {_edad(f, dia)} años' for n, f in cumplen)
    return f'🎂 Hoy cumplen años en {eq}:\n{lineas}\n¡No olvides felicitarles!'


def calcular(dia=None):
    """{'mensajes': [{usuario, telefono, equipo, texto}], 'avisos': [...]} para el día dado."""
    dia = dia or date.today()
    d = _leer()
    perfiles = _equipos_perfiles()
    mensajes, avisos = [], []
    for e in d['equipos']:
        cumplen = [(j['nombre'], date.fromisoformat(j['fnac'])) for j in e['jugadores']
                   if _cumple_hoy(date.fromisoformat(j['fnac']), dia)]
        if not cumplen:
            continue
        ents = [x for x in perfiles.get(e.get('perfiles') or '', []) if x[1]]
        if not ents:
            avisos.append(f"{e['equipo']}: hay cumpleaños pero ningún entrenador con teléfono")
            continue
        texto = texto_mensaje(e.get('perfiles') or e['equipo'], cumplen, dia)
        for usuario, tel in ents:
            mensajes.append({'usuario': usuario, 'telefono': tel, 'equipo': e['equipo'], 'texto': texto})
    return {'mensajes': mensajes, 'avisos': avisos}


def enviar(dia=None):
    from app import enviar_whatsapp
    res = calcular(dia)
    ok = 0
    for m in res['mensajes']:
        bien, det = enviar_whatsapp(m['telefono'], m['texto'])
        ok += bool(bien)
        if not bien:
            print(f"[CUMPLE] No enviado a {m['usuario']}: {det}", flush=True)
    for a in res['avisos']:
        print(f'[CUMPLE] {a}', flush=True)
    d = _leer()
    d['ultimo_envio'] = {'fecha': datetime.now().isoformat(timespec='minutes'), 'enviados': ok, 'total': len(res['mensajes'])}
    _escribir(d)
    return ok, len(res['mensajes'])


def tick(ahora):
    """Cada minuto (push_scheduler). Lunes 04:00 lee la Federación; cada día de 09:00 a 09:30, una vez, envía."""
    d = _leer()
    if not d.get('activo'):
        return
    hoy = ahora.date().isoformat()
    if (ahora.weekday(), ahora.hour) == LECTURA and d.get('leido', '')[:10] != hoy:
        d['leido'] = ahora.isoformat(timespec='minutes')  # marcar antes: un solo intento por lunes
        _escribir(d)
        _en_hilo(leer_federacion)
        return
    inicio = ahora.replace(hour=HORA_ENVIO[0], minute=HORA_ENVIO[1], second=0, microsecond=0)
    if d.get('ultimo') == hoy or not (inicio <= ahora < inicio + timedelta(minutes=30)):
        return
    d['ultimo'] = hoy  # marcar antes de enviar: nunca dos veces el mismo día
    _escribir(d)
    _en_hilo(lambda: enviar(ahora.date()))


def _en_hilo(fn):
    def _job():
        with _ocupado:
            try:
                fn()
            except Exception as e:
                print(f'[CUMPLE] {e}', flush=True)
    threading.Thread(target=_job, daemon=True).start()


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


def _resumen(d):
    out = []
    for e in d['equipos']:
        out.append({'equipo': e['equipo'], 'perfiles': e.get('perfiles', ''), 'jugadores': len(e['jugadores']),
                    'control': e.get('control', {}), 'entrenadores': e.get('entrenadores', [])})
    return out


@cumple_bp.route('/api/whatsapp/cumple', methods=['GET', 'POST'])
def api_estado():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    d = _leer()
    if request.method == 'POST' and 'activo' in (request.json or {}):
        d['activo'] = bool(request.json['activo'])
        _escribir(d)
    eqs = _resumen(d)  # sin consultar Google aquí: la pestaña carga muchas tarjetas a la vez
    return jsonify({'status': 'ok', 'activo': d['activo'], 'leido': d.get('leido', ''), 'equipos': eqs,
                    'fallos': d.get('fallos_lectura', []), 'ultimo_envio': d.get('ultimo_envio') or {},
                    'leyendo': _leyendo(d), 'ultima_lectura': d.get('ultima_lectura') or {}})


@cumple_bp.route('/api/whatsapp/cumple/leer', methods=['POST'])
def api_leer():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    d = _leer()
    if _leyendo(d):
        return jsonify({'status': 'error', 'message': 'Ya se está leyendo, espera un momento'})
    d['leyendo'] = datetime.now().isoformat(timespec='seconds')  # en el fichero: vale para todos los workers
    _escribir(d)

    def _leer_fed():
        try:
            ok, det = leer_federacion()
        except Exception as e:
            ok, det = False, f'Error: {e}'[:250]
        d2 = _leer()
        d2['leyendo'] = ''
        d2['ultima_lectura'] = {'ok': ok, 'texto': det, 'fecha': datetime.now().isoformat(timespec='minutes')}
        _escribir(d2)
    threading.Thread(target=_leer_fed, daemon=True).start()
    return jsonify({'status': 'ok', 'message': 'Leyendo la Federación...'})


def _leyendo(d):
    try:
        return datetime.now() - datetime.fromisoformat(d.get('leyendo') or '') < timedelta(minutes=10)
    except ValueError:
        return False


@cumple_bp.route('/api/whatsapp/cumple/prueba', methods=['POST'])
def api_prueba():
    """Manda a Rubén Pozón el mensaje como si hoy cumpliera años el primer jugador leído (y le dice a quién iría)."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    from app import enviar_whatsapp
    from instagram_historias import _tel_ruben
    d = _leer()
    e = next((x for x in d['equipos'] if x['jugadores']), None)
    if not e:
        return jsonify({'status': 'error', 'message': 'Primero pulsa LEER FEDERACIÓN'})
    tel = _tel_ruben()
    if not tel:
        return jsonify({'status': 'error', 'message': 'No encuentro el teléfono de Rubén Pozón en PERFILES'})
    j = e['jugadores'][0]
    f = date.fromisoformat(j['fnac'])
    hoy = date.today()
    dia = date(hoy.year, f.month, 28 if (f.month, f.day) == (2, 29) else f.day)
    ents = [u for u, t in _equipos_perfiles().get(e.get('perfiles') or '', []) if t]
    texto = ('🧪 PRUEBA (solo a ti). Este mensaje le llegaría a: ' + (', '.join(ents) or 'NADIE (no hay entrenador con teléfono)')
             + '\n\n' + texto_mensaje(e.get('perfiles') or e['equipo'], [(j['nombre'], f)], dia))
    ok, det = enviar_whatsapp(tel, texto)
    return jsonify({'status': 'ok' if ok else 'error', 'message': 'Enviado a Rubén Pozón' if ok else f'No enviado: {det}'})
