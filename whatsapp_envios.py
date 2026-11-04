"""WhatsApp a entrenadores (D. Deportiva → WHATSAPP): envío inmediato o programado (día y hora).

Los teléfonos salen de PERFILES. Los envíos programados se guardan en datos_privados/whatsapp_programados.json
y los lanza el bucle de push_scheduler (una vez por minuto). Una vez enviados se borran (no hay historial).
"""
import os
import json
import uuid
import threading
from datetime import datetime
from flask import Blueprint, request, jsonify, session, current_app

whatsapp_bp = Blueprint('whatsapp', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROG_FILE = os.path.join(BASE_DIR, 'datos_privados', 'whatsapp_programados.json')
_lock = threading.Lock()


def _permitido():
    usuario = session.get('usuario')
    if not usuario:
        return False
    perms = session.get('permisos', {})
    return perms.get('D.DEPORTIVA') == 'SI' or perms.get('D.DEP') == 'SI' or usuario.lower() == 'admin'


def _cargar():
    try:
        with open(PROG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _guardar(lista):
    os.makedirs(os.path.dirname(PROG_FILE), exist_ok=True)
    tmp = PROG_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(lista, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PROG_FILE)


# ---------- WAHA (WhatsApp propio en el servidor, sin límites) ----------
# Docker en 127.0.0.1:3000 con WAHA_API_KEY; la versión gratuita solo admite la sesión "default".
WAHA_SESION = 'default'


def _waha(metodo, ruta, **kw):
    import requests
    url = os.environ.get('WAHA_URL', '').rstrip('/') + ruta
    headers = {'X-Api-Key': os.environ.get('WAHA_API_KEY', ''), 'Accept': kw.pop('accept', 'application/json')}
    return requests.request(metodo, url, headers=headers, timeout=kw.pop('timeout', 25), **kw)


def enviar_por_waha(numero_limpio, mensaje):
    try:
        r = _waha('POST', '/api/sendText', json={'session': WAHA_SESION, 'chatId': f'{numero_limpio}@c.us', 'text': mensaje})
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


def _enviar_a_todos(destinatarios, texto):
    """Envía el texto a cada destinatario. Devuelve [{usuario, ok, detalle}]."""
    from app import enviar_whatsapp
    res = []
    for d in destinatarios:
        ok, detalle = enviar_whatsapp(d.get('telefono', ''), texto)
        res.append({'usuario': d.get('usuario', ''), 'ok': bool(ok), 'detalle': detalle or ''})
    return res


@whatsapp_bp.route('/api/whatsapp/destinatarios')
def api_destinatarios():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        v = current_app.gs_client.open(current_app.gs_name).worksheet('PERFILES').get_all_values()
        h = [x.strip().upper() for x in (v[0] if v else [])]
        i_u, i_t = h.index('USUARIO'), h.index('TELEFONO')
        i_e = h.index('EQUIPO') if 'EQUIPO' in h else None
        i_ti = h.index('TIPO') if 'TIPO' in h else None
        out = []
        for r in v[1:]:
            g = lambda i: (r[i].strip() if i is not None and i < len(r) else '')
            if not g(i_u) or not g(i_t):
                continue
            out.append({'usuario': g(i_u), 'telefono': g(i_t), 'equipo': g(i_e).upper(), 'tipo': g(i_ti).upper()})
        out.sort(key=lambda x: (x['equipo'] or 'ZZZ', x['usuario'].lower()))
        return jsonify({'status': 'ok', 'destinatarios': out})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@whatsapp_bp.route('/api/whatsapp/enviar', methods=['POST'])
def api_enviar():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    data = request.json or {}
    texto = (data.get('texto') or '').strip()
    dest = [d for d in (data.get('destinatarios') or []) if (d.get('telefono') or '').strip()]
    cuando = (data.get('cuando') or '').strip()  # '' = ahora; 'YYYY-MM-DDTHH:MM' = programado
    if not texto or not dest:
        return jsonify({'status': 'error', 'message': 'Escribe el mensaje y elige al menos un destinatario'}), 400
    if not cuando:
        res = _enviar_a_todos(dest, texto)
        return jsonify({'status': 'ok', 'resultados': res})
    try:
        dt = datetime.strptime(cuando, '%Y-%m-%dT%H:%M')
    except Exception:
        return jsonify({'status': 'error', 'message': 'Fecha u hora no válida'}), 400
    if dt <= datetime.now():
        return jsonify({'status': 'error', 'message': 'La fecha y hora tienen que ser futuras'}), 400
    with _lock:
        lista = _cargar()
        lista.append({'id': uuid.uuid4().hex[:10], 'cuando': cuando, 'texto': texto,
                      'destinatarios': [{'usuario': d.get('usuario', ''), 'telefono': d['telefono']} for d in dest],
                      'creado_por': session.get('usuario', '')})
        _guardar(lista)
    return jsonify({'status': 'ok', 'programado': cuando})


@whatsapp_bp.route('/api/whatsapp/programados')
def api_programados():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    lista = sorted(_cargar(), key=lambda x: x.get('cuando', ''))
    return jsonify({'status': 'ok', 'programados': [
        {'id': p['id'], 'cuando': p['cuando'], 'texto': p['texto'],
         'destinatarios': [d.get('usuario', '') for d in p.get('destinatarios', [])]} for p in lista]})


@whatsapp_bp.route('/api/whatsapp/programados/<pid>', methods=['DELETE'])
def api_borrar_programado(pid):
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    with _lock:
        _guardar([p for p in _cargar() if p.get('id') != pid])
    return jsonify({'status': 'ok'})


def procesar_pendientes():
    """Lo llama push_scheduler cada minuto: envía los programados cuya hora ya ha llegado y los quita."""
    ahora = datetime.now().strftime('%Y-%m-%dT%H:%M')
    with _lock:
        lista = _cargar()
        toca = [p for p in lista if p.get('cuando', '') <= ahora]
        if not toca:
            return
        _guardar([p for p in lista if p.get('cuando', '') > ahora])
    for p in toca:
        res = _enviar_a_todos(p.get('destinatarios', []), p.get('texto', ''))
        ok = sum(1 for r in res if r['ok'])
        print(f"[WHATSAPP PROG] {p.get('cuando')}: enviado a {ok} de {len(res)}", flush=True)
