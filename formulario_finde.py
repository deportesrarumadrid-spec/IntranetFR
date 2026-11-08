"""Aviso formulario de partido (fin de semana) por WhatsApp + valoración rápida respondiendo al mensaje.

A cada entrenador de un equipo con partido ese finde (sin Aficionados ni Debutantes), un WhatsApp privado
por partido: 2 horas después del inicio (o a las 17:00 si no se sabe la hora) y, si sigue sin formulario,
otro al día siguiente a las 17:00 (domingo si jugó el sábado, lunes si jugó el domingo). Deja de enviarse
en cuanto el formulario está hecho (formulario de Google o de la intranet).

El mensaje pide solo lo que le falta: formulario y/o audio. Si ya ha hecho las dos cosas no se le envía.
Audio: el entrenador manda un audio a ese chat (WAHA avisa a /api/whatsapp/webhook). Si lleva varios equipos
se le pregunta de cuál es (y se entiende su respuesta escrita). Se marca AUDIO ENVIADO en el INFORME FINDE,
se reenvía a las personas configuradas con el texto "Audio EQUIPO - Nombre" y al entrenador se le responde 👌.
Estado en datos_privados/formulario_finde.json.
"""
import os
import re
import json
import hashlib
import unicodedata
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from flask import Blueprint, request, jsonify

finde_bp = Blueprint('formulario_finde', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'datos_privados', 'formulario_finde.json')
LOCK_FILE = os.path.join(BASE_DIR, 'datos_privados', '.formulario_finde.lock')
URL_BASE = 'https://intranet.clubfuentelarreyna.com'
FORM_URL = 'https://docs.google.com/forms/d/e/1FAIpQLSezt79kgOZisk52yRn8PF_5eWBBXeNxbVfCuRifIjxQo6hR5A/viewform'
HORA_RECORDATORIO = '17:00'
HORAS_TRAS_INICIO = 2


# ---------- almacenamiento (varios procesos de gunicorn: bloqueo de fichero) ----------

@contextmanager
def _bloqueo():
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
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
    d.setdefault('activo', True)
    d.setdefault('envios', {})
    return d


def _escribir(d):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.upper().split())


def _bonito(eq):
    return (' '.join(eq.upper().split()).replace('PREBENJAMIN', 'PREBENJAMÍN')
            .replace('BENJAMIN', 'BENJAMÍN').replace('ALEVIN', 'ALEVÍN'))


def sabado_de(d):
    """Sábado del fin de semana al que pertenece d (viernes → el día siguiente; sáb, dom y lunes → ese finde)."""
    if d.weekday() == 4:
        return d + timedelta(days=1)
    return d - timedelta(days=(d.weekday() - 5) % 7)


# ---------- horarios de la Federación (definitivos el viernes a primera hora) ----------

FED_DIR = os.path.join(BASE_DIR, 'datos_privados', 'partidos_federacion')
HORA_DESCARGA = 8  # el viernes a partir de las 8:00 se descargan los horarios de vie, sáb y dom
_ultimo_intento = {}


def _fichero_fed(sat):
    return os.path.join(FED_DIR, f'{sat.isoformat()}.json')


def leer_federacion(sat):
    try:
        with open(_fichero_fed(sat), 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def descargar_federacion(sat):
    """Descarga de la RFFM (Campos y Horas) los partidos del club de viernes a domingo y los guarda."""
    import competicion_scraper as cs
    from horarios import _rffm_session
    vie, dom = sat - timedelta(days=1), sat + timedelta(days=1)
    cod, _, _ = cs._temporada_actual()
    matches = cs.fetch_temporada_matches(_rffm_session(), cod_temporada=cod,
                                         fecha_desde=vie.strftime('%d-%m-%Y'), fecha_hasta=dom.strftime('%d-%m-%Y'))
    partidos = []
    for m in matches:
        for nuestro, rival in ((m['nombre_casa'], m['nombre_fuera']), (m['nombre_fuera'], m['nombre_casa'])):
            if 'FUENTELARREYNA' in nuestro.upper():
                partidos.append({'fecha': m.get('fecha', ''), 'hora': m.get('hora', ''), 'competicion': m.get('competicion', ''),
                                 'deporte': m.get('deporte', ''), 'nuestro': nuestro, 'rival': rival})
    datos = {'descargado': datetime.now().strftime('%Y-%m-%dT%H:%M'), 'partidos': partidos}
    os.makedirs(FED_DIR, exist_ok=True)
    tmp = _fichero_fed(sat) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _fichero_fed(sat))
    print(f"[FINDE] Horarios de la Federación descargados: {len(partidos)} partidos ({vie:%d/%m}-{dom:%d/%m})", flush=True)
    return datos


def _claves_federacion(p):
    """Nombres posibles del equipo para un partido de la Federación (p. ej. {'ALEVIN A', 'ALEVIN A F7'}) y su formato."""
    from competicion_scraper import detect_categoria_y_letra
    comp = (p.get('competicion') or '').upper()
    cat, _ = detect_categoria_y_letra(comp.replace('COPA', ''))
    if not cat or cat == 'OTROS':
        return set(), ''
    m = re.search(r'"([^"]+)"', p.get('nuestro') or '')
    if m:
        letra = m.group(1).strip().upper()
    else:
        ult = (p.get('nuestro') or '').strip().upper().split()
        letra = ult[-1] if ult and len(ult[-1]) <= 2 and ult[-1].isalpha() else 'A'
    dep = (p.get('deporte') or '').upper().replace(' ', '').replace('Ú', 'U')
    fmt = 'F7' if (cat.endswith('F7') or 'FUTBOL-7' in dep or 'F7' in dep or 'F-7' in comp) else 'F11'
    base = cat.replace(' F7', '').strip()
    if 'FEMENIN' in comp:  # solo el equipo femenino (no confundir con 'ALEVIN A F7')
        return {_norm(f'{base} FEMENINO'), _norm(f'{base} FEMENINO {letra}')}, fmt
    return {_norm(f'{base} {letra}'), _norm(f'{base} {letra} {fmt}')}, fmt


# ---------- partidos del finde ----------

def partidos_finde(sat):
    """{EQUIPO (nombre de la configuración): {'fecha': date, 'hora': 'HH:MM' o '', 'origen': ...}} con partido
    viernes, sábado o domingo. La hora sale de la Federación (descargada el viernes); si no está, de las
    convocatorias o del calendario (sin hora: se envía a las 17:00)."""
    from deportivo import _get_equipos_config_path
    dias = {sat - timedelta(days=1), sat, sat + timedelta(days=1)}
    try:
        with open(_get_equipos_config_path(), encoding='utf-8') as f:
            equipos = [e['nombre'] for e in json.load(f).get('equipos', []) if e.get('nombre')]
    except Exception:
        equipos = []
    equipos = [e for e in equipos if not _norm(e).startswith(('AFICIONADO', 'DEBUTANTE'))]

    def _fecha(txt):
        p = [int(x) for x in (txt or '').replace('/', '-').split('-')]
        return date(p[2], p[1], p[0])

    def _hora(txt):
        h = (txt or '').strip()[:5]
        return h if re.match(r'^\d{1,2}:\d{2}$', h) and h not in ('00:00', '0:00') else ''

    fed, otras = [], []  # (claves de nombre, fecha, hora, formato)
    for p in (leer_federacion(sat) or {}).get('partidos', []):
        try:
            f = _fecha(p.get('fecha'))
        except Exception:
            continue
        claves, fmt = _claves_federacion(p)
        if f in dias and claves:
            fed.append((claves, f, _hora(p.get('hora')), fmt))
    try:
        from competicion_scraper import load_convocatorias_report
        for e in (load_convocatorias_report() or {}).get('entries', []):
            try:
                f = _fecha(e.get('fecha'))
            except Exception:
                continue
            if f in dias:
                base = _norm(f"{e.get('categoria', '')} {e.get('letra', '')}")
                otras.append(({base}, f, _hora(e.get('hora')), ''))
    except Exception as ex:
        print(f"[FINDE] convocatorias: {ex}", flush=True)
    try:
        from competicion_scraper import construir_obj_semanales
        for p in construir_obj_semanales():
            try:
                f = _fecha(p.get('fecha'))
            except Exception:
                continue
            if f in dias:
                base = _norm(f"{p.get('categoria', '')} {p.get('letra', '')}")
                fmt = _norm(p.get('formato', ''))
                otras.append(({base, _norm(f"{base} {fmt}")}, f, '', fmt))
    except Exception as ex:
        print(f"[FINDE] calendario: {ex}", flush=True)

    def _fmt(n):
        return 'F11' if 'F11' in n.split() else ('F7' if 'F7' in n.split() else '')

    def _buscar(n, entradas):
        exactas = [x for x in entradas if n in x[0]]
        # parecidas (p. ej. 'INFANTIL B' ↔ 'INFANTIL B F11'), pero nunca F7 con F11
        return exactas or [x for x in entradas if any(n.startswith(k) or k.startswith(n) for k in x[0] if k)
                           and not (x[3] and _fmt(n) and x[3] != _fmt(n))]

    out = {}
    for eq in equipos:
        n = _norm(eq)
        for origen, entradas in (('federacion', fed), ('calendario', otras)):
            parecidas = _buscar(n, entradas)
            if parecidas:
                fecha = min(x[1] for x in parecidas)
                hora = next((x[2] for x in parecidas if x[1] == fecha and x[2]), '')
                out[eq] = {'fecha': fecha, 'hora': hora, 'origen': origen}
                break
    return out



def _momentos(partido):
    """Momentos de envío: tras el partido (+2 h, o 17:00 si no hay hora) y recordatorio al día siguiente 17:00."""
    f = partido['fecha']
    hh, mm = [int(x) for x in HORA_RECORDATORIO.split(':')]
    if partido.get('hora'):
        h, m = [int(x) for x in partido['hora'].split(':')]
        primero = datetime(f.year, f.month, f.day, h, m) + timedelta(hours=HORAS_TRAS_INICIO)
    else:
        primero = datetime(f.year, f.month, f.day, hh, mm)
    sig = f + timedelta(days=1)
    return [primero, datetime(sig.year, sig.month, sig.day, hh, mm)]


def _entrenadores(equipos_objetivo):
    """[(usuario, telefono, equipo)] de PERFILES para los equipos dados."""
    from app import client, NOMBRE_EXCEL
    p = client.open(NOMBRE_EXCEL).worksheet('PERFILES').get_all_values()
    h = [x.strip().upper() for x in (p[0] if p else [])]
    i_u, i_t, i_e = h.index('USUARIO'), h.index('TELEFONO'), h.index('EQUIPO')
    objetivo = {_norm(e): e for e in equipos_objetivo}
    out, vistos = [], set()
    for r in p[1:]:
        g = lambda i: r[i].strip() if i < len(r) else ''
        for e in g(i_e).split(','):
            eq = objetivo.get(_norm(e))
            if eq and g(i_u) and (g(i_t), eq) not in vistos:
                vistos.add((g(i_t), eq))
                out.append((g(i_u), g(i_t), eq))
    return out


def _formulario_hecho(sat, equipo, cache={}):
    from deportivo import formularios_partido_hechos
    from app import client, NOMBRE_EXCEL
    if 'filas' not in cache or cache.get('ts', 0) < datetime.now().timestamp() - 60:
        cache['filas'] = formularios_partido_hechos(client, NOMBRE_EXCEL)
        cache['ts'] = datetime.now().timestamp()
    n = ' '.join(unicodedata.normalize('NFD', equipo).encode('ascii', 'ignore').decode().lower().split())
    return any(eq == n and sat - timedelta(days=1) <= f <= sat + timedelta(days=5) for f, eq in cache['filas'])


def texto_mensaje(usuario, equipo, falta_formulario=True, falta_audio=True):
    # Formato fijado por Dirección Deportiva (negrita de WhatsApp con *...*); solo sale lo que le falta
    nombre = (usuario or '').split()[0].capitalize() if usuario else ''
    t = f'Hola {nombre}, queda pendiente\n*{_bonito(equipo)}*\n'
    if falta_formulario:
        t += f'→formulario de partido de:\n📝 {FORM_URL}\n'
    if falta_audio:
        t += '→envío audio a este chat\n'
    return t + '\nDirección Deportiva'


def _clave(sat, equipo, telefono):
    return f"{sat.isoformat()}|{_norm(equipo)}|{''.join(c for c in telefono if c.isdigit())}"


def _enviar_y_registrar(usuario, telefono, equipo, sat, momento, prueba=False, falta_formulario=True, falta_audio=True):
    from whatsapp_envios import enviar_con_id
    ok, detalle, msg_id = enviar_con_id(telefono, texto_mensaje(usuario, equipo, falta_formulario, falta_audio))
    with _bloqueo():
        d = _leer()
        k = _clave(sat, equipo, telefono) + ('|prueba' if prueba else '')
        reg = d['envios'].setdefault(k, {'sabado': sat.isoformat(), 'equipo': equipo, 'usuario': usuario,
                                         'telefono': telefono, 'enviados': [], 'msg_ids': [], 'prueba': prueba})
        reg['enviados'].append(momento.strftime('%Y-%m-%dT%H:%M') + ('' if ok else ' ERROR'))
        if msg_id:
            reg['msg_ids'].append(msg_id)
        _escribir(d)
    return ok, detalle


def plan(sat):
    """Lo previsto para un finde: [{usuario, telefono, equipo, fecha, hora, momentos, enviados, hecho}]."""
    partidos = partidos_finde(sat)
    d = _leer()
    out = []
    for usuario, telefono, equipo in _entrenadores(partidos):
        reg = d['envios'].get(_clave(sat, equipo, telefono), {})
        out.append({'usuario': usuario, 'telefono': telefono, 'equipo': equipo,
                    'fecha': partidos[equipo]['fecha'].isoformat(), 'hora': partidos[equipo]['hora'],
                    'origen': partidos[equipo].get('origen', ''),
                    'momentos': [m.strftime('%Y-%m-%dT%H:%M') for m in _momentos(partidos[equipo])],
                    'enviados': reg.get('enviados', [])})
    sin_entrenador = sorted(set(partidos) - {e for _, t, e in _entrenadores(partidos) if t})
    return out, sin_entrenador


def tick(ahora):
    """Lo llama push_scheduler cada minuto; trabaja cada 5 min de viernes a lunes."""
    if ahora.weekday() not in (4, 5, 6, 0) or ahora.minute % 5:
        return
    d = _leer()
    if not d.get('activo'):
        return
    sat = sabado_de(ahora.date())
    if ahora.weekday() == 4 and ahora.hour < HORA_DESCARGA:
        return
    # Horarios definitivos: una sola descarga (si falla, se reintenta cada 30 min)
    if leer_federacion(sat) is None and (ahora - _ultimo_intento.get(sat, datetime.min)) > timedelta(minutes=30):
        _ultimo_intento[sat] = ahora
        try:
            descargar_federacion(sat)
        except Exception as e:
            print(f"[FINDE] No se pudieron descargar los horarios de la Federación: {e}", flush=True)
    partidos = partidos_finde(sat)
    if not partidos:
        return
    from app import app as _app
    from deportivo import audios_finde
    with _app.app_context():
        audios = audios_finde(sat.strftime('%d/%m/%Y'))
    for usuario, telefono, equipo in _entrenadores(partidos):
        if not telefono:
            continue
        reg = _leer()['envios'].get(_clave(sat, equipo, telefono), {})
        hechos = {e[:16] for e in reg.get('enviados', [])}
        for m in _momentos(partidos[equipo]):
            ms = m.strftime('%Y-%m-%dT%H:%M')
            if m > ahora or ms in hechos or ahora - m > timedelta(hours=3):
                continue  # aún no toca, ya enviado, o demasiado tarde (servidor parado)
            falta_form = not _formulario_hecho(sat, equipo)
            falta_audio = audios.get(equipo.upper()) != 'SI'
            if not falta_form and not falta_audio:
                break  # ya ha hecho las dos cosas
            ok, detalle = _enviar_y_registrar(usuario, telefono, equipo, sat, m,
                                              falta_formulario=falta_form, falta_audio=falta_audio)
            print(f"[FINDE] {usuario} · {equipo} · {ms}: {'OK' if ok else detalle}", flush=True)
            break


# ---------- audios del entrenador ----------

def semana_sabado(d):
    """Sábado del finde al que se apunta un audio: la semana va de viernes a jueves."""
    return d - timedelta(days=(d.weekday() - 4) % 7) + timedelta(days=1)


def _entrenador_por_tel(tel):
    """(usuario, [equipos sin Aficionados/Debutantes]) del usuario de la intranet con ese teléfono, o None."""
    from app import client, NOMBRE_EXCEL
    if not tel:
        return None
    p = client.open(NOMBRE_EXCEL).worksheet('PERFILES').get_all_values()
    h = [x.strip().upper() for x in (p[0] if p else [])]
    i_u, i_t, i_e = h.index('USUARIO'), h.index('TELEFONO'), h.index('EQUIPO')
    for r in p[1:]:
        g = lambda i: r[i].strip() if i < len(r) else ''
        t = ''.join(c for c in g(i_t) if c.isdigit())
        if t and len(t) >= 9 and tel.endswith(t[-9:]):
            eqs = [e.strip().upper() for e in g(i_e).split(',')
                   if e.strip() and not _norm(e).startswith(('AFICIONADO', 'DEBUTANTE'))]
            return (g(i_u), eqs) if eqs else None
    return None


def _es_audio(payload):
    media = payload.get('media') or {}
    if str(media.get('mimetype') or '').startswith('audio'):
        return True
    if str(payload.get('type') or '').lower() in ('ptt', 'audio', 'voice'):
        return True
    return bool(payload.get('hasMedia')) and 'audioMessage' in json.dumps(payload.get('_data') or {})[:4000]


_PALABRAS_TODOS = ('los dos', 'las dos', 'ambos', 'todos', 'los 2', 'los tres', 'de los dos', 'para los dos')


def equipos_de_respuesta(texto, equipos):
    """Qué equipo(s) dice el entrenador ("el F11", "alevín a", "los dos"...). Lista vacía si no se entiende."""
    t = ' '.join(re.sub(r'[^a-z0-9 ]', ' ', _norm(texto).lower()).split())
    if not t:
        return []
    if any(f' {p} ' in f' {t} ' for p in _PALABRAS_TODOS):
        return list(equipos)
    palabras = t.split()
    tokens = {e: _norm(e).lower().split() for e in equipos}
    contexto = {'el', 'la', 'del', 'de', 'equipo', 'f7', 'f11'} | {w for ts in tokens.values() for w in ts if len(w) > 1}
    elegidos = []
    for e, ts in tokens.items():
        otros = {w for o, ws in tokens.items() if o != e for w in ws}
        propios = [w for w in ts if w not in otros] or ts
        for w in propios:
            if len(w) > 1 and w in palabras:
                elegidos.append(e); break
            if len(w) == 1 and (palabras == [w] or any(palabras[i] == w and i > 0 and palabras[i - 1] in contexto
                                                       for i in range(len(palabras)))):
                elegidos.append(e); break
    return elegidos


def _destinatarios_audio(d):
    """Personas a las que se reenvían los audios (por defecto, Rubén Pozón)."""
    if 'reenviar' not in d:
        try:
            from app import client, NOMBRE_EXCEL
            p = client.open(NOMBRE_EXCEL).worksheet('PERFILES').get_all_values()
            h = [x.strip().upper() for x in p[0]]
            i_u, i_t = h.index('USUARIO'), h.index('TELEFONO')
            d['reenviar'] = [{'usuario': r[i_u].strip(), 'telefono': r[i_t].strip()} for r in p[1:]
                             if len(r) > max(i_u, i_t) and 'POZON' in _norm(r[i_u]) and r[i_t].strip()][:1]
        except Exception:
            d['reenviar'] = []
    return d['reenviar']


def _registrar_audio(equipos, msg_ids, usuario, tel_entrenador):
    """Marca AUDIO ENVIADO, reenvía el/los audio(s) con su etiqueta y responde 👌 al entrenador."""
    from deportivo import marcar_audio_finde
    from whatsapp_envios import enviar_por_waha, _waha, WAHA_SESION
    sat = semana_sabado(date.today())
    for eq in equipos:
        marcar_audio_finde(sat.strftime('%d/%m/%Y'), eq.upper(), 'SI')
    nombre = (usuario or '').split()[0].capitalize() if usuario else ''
    etiqueta = f"Audio {' y '.join(_bonito(e) for e in equipos)} - {nombre}"
    with _bloqueo():
        d = _leer()
        destinatarios = _destinatarios_audio(d)
        _escribir(d)
    for dest in destinatarios:
        num = ''.join(c for c in dest.get('telefono', '') if c.isdigit())
        num = '34' + num if len(num) == 9 else num
        if len(num) < 10:
            continue
        enviar_por_waha(num, etiqueta)
        for mid in msg_ids:
            r = _waha('POST', '/api/forwardMessage', json={'session': WAHA_SESION, 'chatId': f'{num}@c.us', 'messageId': mid})
            if r.status_code >= 300:
                print(f"[FINDE] No se pudo reenviar el audio a {dest.get('usuario')}: HTTP {r.status_code} {r.text[:200]}", flush=True)
    enviar_por_waha(tel_entrenador, '👌')
    print(f"[FINDE] {etiqueta}: registrado y reenviado a {len(destinatarios)}", flush=True)


def _pregunta_equipo(equipos, repetir=False):
    lista = ', '.join(_bonito(e) for e in equipos)
    todos = 'los dos' if len(equipos) == 2 else 'todos'
    return (('No te he entendido 🙏 ' if repetir else '') + f'¿De qué equipo es este audio? {lista} o {todos}')


def _telefono_de(payload, origen):
    """Teléfono (solo cifras) de quien escribe. WhatsApp a veces manda un id interno '...@lid' en vez del
    número: se busca el número real en los datos del mensaje o se le pregunta a WAHA."""
    if origen and not origen.endswith('@lid'):
        return ''.join(c for c in origen.split('@')[0] if c.isdigit())
    propio = ''
    try:
        from whatsapp_envios import _waha, WAHA_SESION
        lid = origen.split('@')[0]
        r = _waha('GET', f'/api/{WAHA_SESION}/lids/{lid}', timeout=10)
        if r.status_code < 300:
            pn = str((r.json() or {}).get('pn') or '')
            if pn:
                return ''.join(c for c in pn.split('@')[0] if c.isdigit())
        me = _waha('GET', f'/api/sessions/{WAHA_SESION}', timeout=10).json().get('me') or {}
        propio = ''.join(c for c in str(me.get('id', '')).split('@')[0] if c.isdigit())
    except Exception as e:
        print(f"[FINDE] No se pudo resolver {origen}: {e}", flush=True)
    # Último recurso: cualquier número '...@s.whatsapp.net' / '@c.us' dentro del mensaje que no sea el del club
    destino = ''.join(c for c in str(payload.get('to') or '').split('@')[0] if c.isdigit())
    for num in re.findall(r'(\d{9,15})@(?:s\.whatsapp\.net|c\.us)', json.dumps(payload)):
        if num not in (propio, destino):
            return num
    return ''


def procesar_mensaje(payload):
    """Mensaje recibido por WhatsApp: audios de entrenadores (y su respuesta de a qué equipo pertenecen)."""
    from whatsapp_envios import enviar_por_waha
    if payload.get('fromMe'):
        return None
    cuerpo = (payload.get('body') or '').strip()
    origen = str(payload.get('from') or '')
    if origen.endswith('@g.us'):
        return None  # mensajes de grupos: no
    tel = _telefono_de(payload, origen)
    audio = _es_audio(payload)
    print(f"[FINDE] Mensaje recibido de {origen} (tel {tel or '?'}){' AUDIO' if audio else ''}: {cuerpo[:60]!r}", flush=True)
    ent = _entrenador_por_tel(tel)
    if not ent:
        return None  # solo entrenadores
    usuario, equipos = ent
    msg_id = payload.get('id')
    if isinstance(msg_id, dict):
        msg_id = msg_id.get('_serialized')

    if audio:
        if len(equipos) == 1:
            _registrar_audio(equipos, [msg_id], usuario, tel)
            return '👌'
        with _bloqueo():
            d = _leer()
            pend = d.setdefault('audios_pendientes', {}).setdefault(tel, {'msg_ids': [], 'equipos': equipos})
            ya_preguntado = bool(pend['msg_ids'])
            pend['msg_ids'].append(msg_id)
            pend['equipos'] = equipos
            _escribir(d)
        if not ya_preguntado:
            enviar_por_waha(tel, _pregunta_equipo(equipos))
        return 'pregunta'

    pend = _leer().get('audios_pendientes', {}).get(tel)
    if not pend or not cuerpo:
        return None
    elegidos = equipos_de_respuesta(cuerpo, pend['equipos'])
    if not elegidos:
        enviar_por_waha(tel, _pregunta_equipo(pend['equipos'], repetir=True))
        return 'repetir'
    with _bloqueo():
        d = _leer()
        pend = d.get('audios_pendientes', {}).pop(tel, None)
        _escribir(d)
    if pend:
        _registrar_audio(elegidos, pend['msg_ids'], usuario, tel)
    return '👌'


def secreto_webhook():
    return hashlib.sha256((os.environ.get('WAHA_API_KEY', '') + '|webhook').encode()).hexdigest()[:32]


@finde_bp.route('/api/whatsapp/webhook', methods=['POST'])
def api_webhook():
    if not os.environ.get('WAHA_API_KEY') or request.args.get('k') != secreto_webhook():
        return jsonify({'status': 'error'}), 403
    data = request.get_json(silent=True) or {}
    if data.get('event') in ('message', 'message.any'):
        try:
            procesar_mensaje(data.get('payload') or {})
        except Exception as e:
            print(f"[FINDE] webhook: {e}", flush=True)
    return jsonify({'status': 'ok'})


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@finde_bp.route('/api/whatsapp/formulario_finde', methods=['GET', 'POST'])
def api_cfg():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    with _bloqueo():
        d = _leer()
        if request.method == 'POST':
            data = request.json or {}
            if 'activo' in data:
                d['activo'] = bool(data['activo'])
            if 'reenviar' in data:
                d['reenviar'] = [{'usuario': (x.get('usuario') or '').strip(), 'telefono': (x.get('telefono') or '').strip()}
                                 for x in (data.get('reenviar') or []) if (x.get('telefono') or '').strip()]
        reenviar = _destinatarios_audio(d)
        _escribir(d)
    return jsonify({'status': 'ok', 'activo': d['activo'], 'reenviar': reenviar})


@finde_bp.route('/api/whatsapp/formulario_finde/vista')
def api_vista():
    """Lo previsto para el finde (el que viene, o el actual si es sáb/dom/lun). No envía nada."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        hoy = date.today()
        sat = sabado_de(hoy) if hoy.weekday() in (4, 5, 6, 0) else hoy + timedelta(days=(5 - hoy.weekday()) % 7)
        filas, sin_ent = plan(sat)
        fed = leer_federacion(sat)
        from deportivo import audios_finde
        audios = audios_finde(sat.strftime('%d/%m/%Y'))
        for f in filas:
            f['hecho'] = _formulario_hecho(sat, f['equipo'])
            f['audio'] = audios.get(f['equipo'].upper()) == 'SI'
        return jsonify({'status': 'ok', 'sabado': sat.isoformat(), 'filas': filas, 'sin_entrenador': sin_ent,
                        'federacion': (fed or {}).get('descargado', '')})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)[:200]}), 500
