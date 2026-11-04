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
