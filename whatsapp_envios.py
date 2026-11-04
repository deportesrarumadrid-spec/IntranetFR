"""WhatsApp a entrenadores y grupos (D. Deportiva → WHATSAPP): envío inmediato, programado o periódico.

Los teléfonos salen de PERFILES. Los envíos programados se guardan en datos_privados/whatsapp_programados.json
y los lanza el bucle de push_scheduler (una vez por minuto). Los de una sola vez se borran al enviarse
(no hay historial); los periódicos pasan a su siguiente fecha. Las plantillas, en whatsapp_plantillas.json.
"""
import os
import json
import uuid
import threading
import calendar
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, session, current_app

whatsapp_bp = Blueprint('whatsapp', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROG_FILE = os.path.join(BASE_DIR, 'datos_privados', 'whatsapp_programados.json')
PLANT_FILE = os.path.join(BASE_DIR, 'datos_privados', 'whatsapp_plantillas.json')
_lock = threading.Lock()


def _permitido():
    usuario = session.get('usuario')
    if not usuario:
        return False
    perms = session.get('permisos', {})
    return perms.get('D.DEPORTIVA') == 'SI' or perms.get('D.DEP') == 'SI' or usuario.lower() == 'admin'


def _cargar(fichero=PROG_FILE):
    try:
        with open(fichero, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _guardar(lista, fichero=PROG_FILE):
    os.makedirs(os.path.dirname(fichero), exist_ok=True)
    tmp = fichero + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(lista, f, ensure_ascii=False, indent=1)
    os.replace(tmp, fichero)


# ---------- WAHA (WhatsApp propio en el servidor, sin límites) ----------
# Docker en 127.0.0.1:3000 con WAHA_API_KEY; la versión gratuita solo admite la sesión "default".
WAHA_SESION = 'default'


def _waha(metodo, ruta, **kw):
    import requests
    url = os.environ.get('WAHA_URL', '').rstrip('/') + ruta
    headers = {'X-Api-Key': os.environ.get('WAHA_API_KEY', ''), 'Accept': kw.pop('accept', 'application/json')}
    return requests.request(metodo, url, headers=headers, timeout=kw.pop('timeout', 25), **kw)


def enviar_por_waha(numero_limpio, mensaje):
    """numero_limpio: solo cifras (persona) o un id completo de chat, p. ej. '1203...@g.us' (grupo)."""
    chat_id = numero_limpio if '@' in numero_limpio else f'{numero_limpio}@c.us'
    try:
        r = _waha('POST', '/api/sendText', json={'session': WAHA_SESION, 'chatId': chat_id, 'text': mensaje})
        if r.status_code < 300:
            return True, ''
        try:
            j = r.json()
            detalle = j.get('message') or j.get('error') or r.text
        except Exception:
            detalle = r.text
        if r.status_code == 422 or 'status' in str(detalle).lower():
            detalle = 'El WhatsApp del club no está vinculado (escanea el QR de arriba)'
        return False, str(detalle)[:200]
    except Exception as e:
        return False, f'No se pudo conectar con WhatsApp del servidor: {e}'[:200]


@whatsapp_bp.route('/api/whatsapp/estado')
def api_estado():
    """Estado del WhatsApp del club: 'whapi' (servicio externo), o el estado de la sesión WAHA."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    if not os.environ.get('WAHA_URL'):
        return jsonify({'status': 'ok', 'modo': 'whapi'})
    try:
        r = _waha('GET', f'/api/sessions/{WAHA_SESION}')
        if r.status_code == 404:  # primera vez: crear y arrancar la sesión
            _waha('POST', '/api/sessions', json={'name': WAHA_SESION, 'start': True})
            return jsonify({'status': 'ok', 'modo': 'waha', 'estado': 'STARTING'})
        j = r.json()
        estado = j.get('status', '')
        if estado == 'STOPPED':  # arrancarla para que muestre el QR o reconecte
            _waha('POST', f'/api/sessions/{WAHA_SESION}/start')
        elif estado == 'FAILED':  # p. ej. el QR caducó sin escanear: reiniciar para sacar uno nuevo
            _waha('POST', f'/api/sessions/{WAHA_SESION}/restart')
        me = j.get('me') or {}
        numero = (me.get('id') or '').split('@')[0]
        return jsonify({'status': 'ok', 'modo': 'waha', 'estado': estado, 'numero': numero, 'nombre': me.get('pushName', '')})
    except Exception as e:
        return jsonify({'status': 'ok', 'modo': 'waha', 'estado': 'SIN_CONEXION', 'detalle': str(e)[:150]})


@whatsapp_bp.route('/api/whatsapp/qr')
def api_qr():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        r = _waha('GET', f'/api/{WAHA_SESION}/auth/qr', params={'format': 'image'}, accept='image/png')
        if r.status_code >= 300:
            return jsonify({'status': 'error', 'message': 'QR no disponible todavía'}), 404
        png = r.content
        if 'json' in r.headers.get('Content-Type', ''):  # algunas versiones devuelven {mimetype, data(base64)}
            import base64
            png = base64.b64decode(r.json().get('data', ''))
        return current_app.response_class(png, mimetype='image/png', headers={'Cache-Control': 'no-store'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)[:150]}), 500


@whatsapp_bp.route('/api/whatsapp/desvincular', methods=['POST'])
def api_desvincular():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        _waha('POST', f'/api/sessions/{WAHA_SESION}/logout')
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)[:150]}), 500


@whatsapp_bp.route('/api/whatsapp/grupos')
def api_grupos():
    """Grupos de WhatsApp en los que está el número del club: [{nombre, id}]."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    if not os.environ.get('WAHA_URL'):
        return jsonify({'status': 'ok', 'grupos': []})
    try:
        r = _waha('GET', f'/api/{WAHA_SESION}/groups', timeout=40)
        datos = r.json() if r.status_code < 300 else []
        items = list(datos.values()) if isinstance(datos, dict) else (datos or [])
        grupos = {}
        for g in items:
            if not isinstance(g, dict):
                continue
            gid = g.get('id') or g.get('JID') or g.get('jid') or ''
            if isinstance(gid, dict):
                gid = gid.get('_serialized') or ''
            nombre = g.get('subject') or g.get('name') or g.get('Name') or g.get('Subject') or ''
            if str(gid).endswith('@g.us') and nombre:
                grupos[gid] = nombre
        out = sorted(({'id': k, 'nombre': v} for k, v in grupos.items()), key=lambda x: x['nombre'].lower())
        return jsonify({'status': 'ok', 'grupos': out})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)[:150], 'grupos': []})


def _enviar_a_todos(destinatarios, texto):
    """Envía el texto a cada destinatario. Devuelve [{usuario, ok, detalle}]."""
    from app import enviar_whatsapp
    res = []
    for d in destinatarios:
        tel = d.get('telefono', '')
        if tel.endswith('@g.us'):  # grupo de WhatsApp
            ok, detalle = enviar_por_waha(tel, texto) if os.environ.get('WAHA_URL') else (False, 'Los grupos solo funcionan con el WhatsApp del servidor')
        else:
            ok, detalle = enviar_whatsapp(tel, texto)
        res.append({'usuario': d.get('usuario', ''), 'ok': bool(ok), 'detalle': detalle or ''})
    return res


@whatsapp_bp.route('/api/whatsapp/destinatarios')
def api_destinatarios():
    """Personas con teléfono (PERFILES) y el orden de los equipos para agruparlas."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        v = current_app.gs_client.open(current_app.gs_name).worksheet('PERFILES').get_all_values()
        h = [x.strip().upper() for x in (v[0] if v else [])]
        i_u, i_t = h.index('USUARIO'), h.index('TELEFONO')
        i_e = h.index('EQUIPO') if 'EQUIPO' in h else None
        i_ti = h.index('TIPO') if 'TIPO' in h else None
        out, equipos = [], set()
        for r in v[1:]:
            g = lambda i: (r[i].strip() if i is not None and i < len(r) else '')
            if not g(i_u) or not g(i_t):
                continue
            eqs = [e.strip().upper() for e in g(i_e).split(',') if e.strip()]
            equipos.update(eqs)
            out.append({'usuario': g(i_u), 'telefono': g(i_t), 'equipos': eqs, 'tipo': g(i_ti).upper()})
        out.sort(key=lambda x: x['usuario'].lower())
        try:
            from app import ordenar_equipos
            orden = ordenar_equipos(list(equipos))
        except Exception:
            orden = sorted(equipos)
        return jsonify({'status': 'ok', 'destinatarios': out, 'equipos': orden})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------- Repetición periódica ----------
# repetir = {'tipo': 'dia'|'semana'|'mes', 'hora': 'HH:MM', 'dia_semana': 0-6 (lunes=0), 'dia_mes': 1-31}

def _siguiente(rep, despues):
    """Próxima fecha (datetime) estrictamente posterior a `despues` según la repetición."""
    hh, mm = [int(x) for x in rep.get('hora', '10:00').split(':')]
    tipo = rep.get('tipo')
    if tipo == 'dia':
        c = despues.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return c if c > despues else c + timedelta(days=1)
    if tipo == 'semana':
        ds = int(rep.get('dia_semana', 0))
        c = (despues + timedelta(days=(ds - despues.weekday()) % 7)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        return c if c > despues else c + timedelta(days=7)
    if tipo == 'mes':
        dm = int(rep.get('dia_mes', 1))
        y, m = despues.year, despues.month
        for _ in range(3):
            c = datetime(y, m, min(dm, calendar.monthrange(y, m)[1]), hh, mm)  # si el mes no tiene ese día, el último
            if c > despues:
                return c
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    raise ValueError('Repetición no válida')


def _validar_repetir(rep):
    if not rep:
        return None
    try:
        tipo = rep.get('tipo')
        hh, mm = [int(x) for x in str(rep.get('hora', '')).split(':')]
        assert tipo in ('dia', 'semana', 'mes') and 0 <= hh < 24 and 0 <= mm < 60
        r = {'tipo': tipo, 'hora': f'{hh:02d}:{mm:02d}'}
        if tipo == 'semana':
            r['dia_semana'] = int(rep.get('dia_semana'))
            assert 0 <= r['dia_semana'] <= 6
        if tipo == 'mes':
            r['dia_mes'] = int(rep.get('dia_mes'))
            assert 1 <= r['dia_mes'] <= 31
        return r
    except Exception:
        raise ValueError('Repetición no válida')


def _limpiar_dest(lista):
    return [{'usuario': d.get('usuario', ''), 'telefono': d['telefono'].strip()}
            for d in (lista or []) if (d.get('telefono') or '').strip()]


@whatsapp_bp.route('/api/whatsapp/enviar', methods=['POST'])
def api_enviar():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    data = request.json or {}
    texto = (data.get('texto') or '').strip()
    dest = _limpiar_dest(data.get('destinatarios'))
    cuando = (data.get('cuando') or '').strip()  # '' = ahora; 'YYYY-MM-DDTHH:MM' = una vez
    if not texto or not dest:
        return jsonify({'status': 'error', 'message': 'Escribe el mensaje y elige al menos un destinatario'}), 400
    try:
        repetir = _validar_repetir(data.get('repetir'))
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    if not cuando and not repetir:
        return jsonify({'status': 'ok', 'resultados': _enviar_a_todos(dest, texto)})
    if repetir:
        cuando = _siguiente(repetir, datetime.now()).strftime('%Y-%m-%dT%H:%M')
    else:
        try:
            dt = datetime.strptime(cuando, '%Y-%m-%dT%H:%M')
        except Exception:
            return jsonify({'status': 'error', 'message': 'Fecha u hora no válida'}), 400
        if dt <= datetime.now():
            return jsonify({'status': 'error', 'message': 'La fecha y hora tienen que ser futuras'}), 400
    with _lock:
        lista = _cargar()
        lista.append({'id': uuid.uuid4().hex[:10], 'cuando': cuando, 'texto': texto, 'destinatarios': dest,
                      'repetir': repetir, 'creado_por': session.get('usuario', '')})
        _guardar(lista)
    return jsonify({'status': 'ok', 'programado': cuando, 'repetir': repetir})


@whatsapp_bp.route('/api/whatsapp/programados')
def api_programados():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    lista = sorted(_cargar(), key=lambda x: x.get('cuando', ''))
    return jsonify({'status': 'ok', 'programados': [
        {'id': p['id'], 'cuando': p['cuando'], 'texto': p['texto'], 'repetir': p.get('repetir'),
         'destinatarios': [d.get('usuario', '') for d in p.get('destinatarios', [])]} for p in lista]})


@whatsapp_bp.route('/api/whatsapp/programados/<pid>', methods=['DELETE'])
def api_borrar_programado(pid):
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    with _lock:
        _guardar([p for p in _cargar() if p.get('id') != pid])
    return jsonify({'status': 'ok'})


# ---------- Plantillas: mensaje + destinatarios guardados ----------

@whatsapp_bp.route('/api/whatsapp/plantillas', methods=['GET', 'POST'])
def api_plantillas():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    if request.method == 'GET':
        return jsonify({'status': 'ok', 'plantillas': sorted(_cargar(PLANT_FILE), key=lambda x: x.get('nombre', '').lower())})
    data = request.json or {}
    nombre = (data.get('nombre') or '').strip()
    texto = (data.get('texto') or '').strip()
    if not nombre or not texto:
        return jsonify({'status': 'error', 'message': 'La plantilla necesita nombre y mensaje'}), 400
    with _lock:
        lista = _cargar(PLANT_FILE)
        existente = next((x for x in lista if x.get('nombre', '').lower() == nombre.lower()), None)
        nueva = {'id': existente['id'] if existente else uuid.uuid4().hex[:10], 'nombre': nombre, 'texto': texto,
                 'destinatarios': _limpiar_dest(data.get('destinatarios'))}
        lista = [x for x in lista if x is not existente] + [nueva]
        _guardar(lista, PLANT_FILE)
    return jsonify({'status': 'ok', 'plantilla': nueva, 'actualizada': bool(existente)})


@whatsapp_bp.route('/api/whatsapp/plantillas/<pid>', methods=['DELETE'])
def api_borrar_plantilla(pid):
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    with _lock:
        _guardar([x for x in _cargar(PLANT_FILE) if x.get('id') != pid], PLANT_FILE)
    return jsonify({'status': 'ok'})


def procesar_pendientes():
    """Lo llama push_scheduler cada minuto: envía los programados cuya hora ya ha llegado.
    Los de una vez se quitan; los periódicos pasan a su siguiente fecha."""
    ahora_dt = datetime.now()
    ahora = ahora_dt.strftime('%Y-%m-%dT%H:%M')
    with _lock:
        lista = _cargar()
        toca = [p for p in lista if p.get('cuando', '') <= ahora]
        if not toca:
            return
        quedan = [p for p in lista if p.get('cuando', '') > ahora]
        for p in toca:
            if p.get('repetir'):
                try:
                    quedan.append(dict(p, cuando=_siguiente(p['repetir'], ahora_dt).strftime('%Y-%m-%dT%H:%M')))
                except Exception as e:
                    print(f"[WHATSAPP PROG] repetición inválida {p.get('id')}: {e}", flush=True)
        _guardar(quedan)
    for p in toca:
        res = _enviar_a_todos(p.get('destinatarios', []), p.get('texto', ''))
        ok = sum(1 for r in res if r['ok'])
        print(f"[WHATSAPP PROG] {p.get('cuando')}: enviado a {ok} de {len(res)}", flush=True)
