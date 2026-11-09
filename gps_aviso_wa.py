"""Aviso de GPS: los días que toca (planificación del club, lunes a jueves), a las 17:00 se avisa por WhatsApp a los
entrenadores de los equipos que usan los GPS ese día y al grupo "Dirección".

La planificación sigue un turno de 6 combinaciones que se repite de lunes a jueves, por tramos (sin vacaciones):
26/10-17/12, 11/01-18/03 y 29/03-03/06. Un entrenador con dos equipos ese día recibe un solo mensaje.
Estado en datos_privados/gps_aviso_wa.json.
"""
import os
import json
import unicodedata
from datetime import datetime, date, timedelta
from flask import Blueprint, request, jsonify

gps_aviso_bp = Blueprint('gps_aviso_wa', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'datos_privados', 'gps_aviso_wa.json')
HORA_ENVIO = (17, 0)
_DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']

# Turno de la planificación (INF = Infantil, CDT = Cadete, JUV = Juvenil, SA = Senior A = Aficionado A)
TURNO = [
    ['INFANTIL C', 'CADETE B'],
    ['INFANTIL B', 'JUVENIL A', 'AFICIONADO A'],
    ['INFANTIL D', 'CADETE A'],
    ['INFANTIL A', 'JUVENIL B'],
    ['CADETE D', 'JUVENIL C'],
    ['CADETE C', 'AFICIONADO A'],
]
# (desde, hasta, posición del turno con la que empieza el tramo)
TRAMOS = [
    (date(2026, 10, 26), date(2026, 12, 17), 0),
    (date(2027, 1, 11), date(2027, 3, 18), 2),
    (date(2027, 3, 29), date(2027, 6, 3), 0),
]


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.upper().split())


def _bonito(eq):
    return (' '.join(eq.upper().split()).replace('PREBENJAMIN', 'PREBENJAMÍN')
            .replace('BENJAMIN', 'BENJAMÍN').replace('ALEVIN', 'ALEVÍN'))


def plan():
    """{fecha: [equipos]} de toda la temporada."""
    out = {}
    for desde, hasta, i in TRAMOS:
        d = desde
        while d <= hasta:
            if d.weekday() <= 3:
                out[d] = TURNO[i % len(TURNO)]
                i += 1
            d += timedelta(days=1)
    return out


def _leer():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault('activo', False)
    return d


def _escribir(d):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def _grupo_direccion():
    """Id del grupo de WhatsApp 'Dirección' (se busca por nombre y se guarda)."""
    from whatsapp_envios import _waha, WAHA_SESION
    d = _leer()
    if d.get('grupo_id'):
        return d['grupo_id']
    r = _waha('GET', f'/api/{WAHA_SESION}/groups', timeout=40)
    datos = r.json() if r.status_code < 300 else []
    for g in (datos.values() if isinstance(datos, dict) else datos or []):
        if not isinstance(g, dict):
            continue
        gid = g.get('id') or g.get('JID') or g.get('jid') or ''
        if isinstance(gid, dict):
            gid = gid.get('_serialized') or ''
        nombre = _norm(g.get('subject') or g.get('name') or g.get('Name') or '')
        if nombre == 'DIRECCION' and str(gid).endswith('@g.us'):
            d['grupo_id'] = gid
            _escribir(d)
            return gid
    return ''


def _lista(eqs):
    eqs = [f'*{_bonito(e)}*' for e in eqs]
    return ' y '.join([', '.join(eqs[:-1]), eqs[-1]]) if len(eqs) > 1 else eqs[0]


def mensajes(dia):
    """([(usuario, telefono, texto)] para los entrenadores, texto para el grupo Dirección) de ese día."""
    from formulario_finde import _entrenadores
    equipos = plan().get(dia)
    if not equipos:
        return [], ''
    por_ent = {}
    for usuario, tel, eq in _entrenadores(equipos):
        if tel:
            por_ent.setdefault((usuario, tel), []).append(eq)
    out = []
    for (usuario, tel), eqs in por_ent.items():
        eqs = [e for e in equipos if e in eqs]
        if not eqs:
            continue
        verbo = 'usa' if len(eqs) == 1 else 'usan'
        equipos_txt = 'el ' + _lista(eqs).replace(' y *', ' y el *').replace(', *', ', el *')
        out.append((usuario, tel, f"Hola {usuario.split()[0].capitalize()}, hoy {equipos_txt} {verbo} los *GPS* 📡. "
                                  f"Acordaos de ponérselos a los jugadores.\n\nDirección Deportiva"))
    grupo = f"📡 GPS hoy ({_DIAS[dia.weekday()]} {dia:%d/%m}): {', '.join(_bonito(e) for e in equipos)}"
    return out, grupo


def enviar(dia, prueba=False):
    """Envía los avisos de ese día. En prueba, todo a Rubén Pozón indicando a quién iría."""
    from app import enviar_whatsapp
    from whatsapp_envios import enviar_por_waha
    ents, grupo = mensajes(dia)
    if not grupo:
        return []
    hechos = []
    if prueba:
        from informe_veo import _telefono_ruben
        tel = _telefono_ruben()
        for usuario, _, texto in ents:
            enviar_whatsapp(tel, f'🧪 PRUEBA (iría a {usuario.title()}):\n{texto}')
            hechos.append(usuario)
        enviar_whatsapp(tel, f'🧪 PRUEBA (iría al grupo Dirección):\n{grupo}')
        return hechos + ['grupo Dirección']
    for usuario, tel, texto in ents:
        ok, _ = enviar_whatsapp(tel, texto)
        hechos.append(f"{usuario}{'' if ok else ' — ERROR'}")
    gid = _grupo_direccion()
    if gid:
        enviar_por_waha(gid, grupo)
        hechos.append('grupo Dirección')
    else:
        print('[GPS] No encuentro el grupo "Dirección"', flush=True)
    print(f"[GPS] {dia}: {hechos}", flush=True)
    return hechos


def tick(ahora):
    """Lo llama push_scheduler cada minuto: los días con GPS, una vez, en la media hora siguiente a las 17:00."""
    if ahora.weekday() > 3:
        return
    d = _leer()
    if not d.get('activo'):
        return
    inicio = ahora.replace(hour=HORA_ENVIO[0], minute=HORA_ENVIO[1], second=0, microsecond=0)
    hoy = ahora.date().isoformat()
    if d.get('ultimo') == hoy or not (inicio <= ahora < inicio + timedelta(minutes=30)):
        return
    if ahora.date() not in plan():
        return
    d['ultimo'] = hoy  # marcar antes de enviar: nunca dos veces el mismo día
    _escribir(d)
    enviar(ahora.date())


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


def _proximo_dia(desde):
    p = plan()
    return next((d for d in sorted(p) if d >= desde), None)


@gps_aviso_bp.route('/api/whatsapp/gps', methods=['GET', 'POST'])
def api_cfg():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    d = _leer()
    if request.method == 'POST' and 'activo' in (request.json or {}):
        d['activo'] = bool(request.json['activo'])
        _escribir(d)
    p = plan()
    hoy = date.today()
    proximos = [{'fecha': f.isoformat(), 'dia': _DIAS[f.weekday()], 'equipos': [_bonito(e) for e in p[f]]}
                for f in sorted(p) if f >= hoy][:8]
    return jsonify({'status': 'ok', 'activo': d['activo'], 'ultimo': d.get('ultimo', ''), 'proximos': proximos})


@gps_aviso_bp.route('/api/whatsapp/gps/prueba', methods=['POST'])
def api_prueba():
    """Manda a Rubén Pozón los avisos del próximo día con GPS (hoy si toca), indicando a quién irían."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    dia = _proximo_dia(date.today())
    if not dia:
        return jsonify({'status': 'error', 'message': 'No quedan días con GPS en la planificación'})
    try:
        hechos = enviar(dia, prueba=True)
        return jsonify({'status': 'ok', 'message': f"Prueba del {_DIAS[dia.weekday()]} {dia:%d/%m} enviada a Rubén Pozón "
                                                   f"({len(hechos) - 1} entrenadores + grupo Dirección)"})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]})
