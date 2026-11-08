"""Faltas seguidas: cuando un jugador tiene NO en 2 entrenamientos seguidos de su equipo, se pregunta por WhatsApp
a los entrenadores del equipo el motivo. La respuesta se reenvía a las personas configuradas (Rubén Pozón,
Gonzalo Figueredo, Raúl Lucas), se apunta en AUSENCIAS SIN JUSTIFICAR y al entrenador se le contesta 👌.

- Se revisa en cuanto se guardan asistencias (y cada 30 min por si acaso). Solo faltas desde DESDE.
- Sin respuesta: se recuerda el siguiente día de entrenamiento del equipo (21:00), hasta que conteste.
- Contestada, si acumula otras 2 faltas seguidas se vuelve a preguntar (seguimiento).
- Si el entrenador tiene varias preguntas y responde sin citar, se le pregunta de qué jugador es, hasta entenderlo.
- Sin Aficionados (Debutantes sí). Estado en datos_privados/faltas_seguidas.json.
"""
import os
import re
import json
import threading
import unicodedata
from datetime import datetime, date, timedelta
from flask import Blueprint, request, jsonify

faltas_bp = Blueprint('faltas_seguidas', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'datos_privados', 'faltas_seguidas.json')
DESDE = date(2026, 11, 1)
HORA_RECORDATORIO = (21, 0)
_DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
_lock = threading.Lock()


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.upper().split())


def _bonito(eq):
    return (' '.join(eq.upper().split()).replace('PREBENJAMIN', 'PREBENJAMÍN')
            .replace('BENJAMIN', 'BENJAMÍN').replace('ALEVIN', 'ALEVÍN'))


def _limpio(txt):
    """Nombre del jugador sin lo que a veces lleva pegado ('CARLOS JIMENEZ 649106138' → 'CARLOS JIMENEZ')."""
    palabras = []
    for w in str(txt or '').split():
        if any(ch.isdigit() for ch in w):
            break
        palabras.append(w)
    return ' '.join(palabras) or str(txt or '').strip()


def _nombre(txt):
    return ' '.join(w.capitalize() for w in _limpio(txt).split())


def _leer():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault('activo', False)  # se activa desde la pantalla tras revisar la vista previa
    d.setdefault('casos', {})
    d.setdefault('esperando_jugador', {})  # tel → texto de una respuesta que aún no sé de qué jugador es
    return d


def _escribir(d):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def _destinatarios(d):
    """Personas a las que se reenvían los motivos (por defecto Rubén Pozón, Gonzalo Figueredo y Raúl Lucas)."""
    if 'reenviar' not in d:
        d['reenviar'] = []
        try:
            from app import client, NOMBRE_EXCEL
            p = client.open(NOMBRE_EXCEL).worksheet('PERFILES').get_all_values()
            h = [x.strip().upper() for x in p[0]]
            i_u, i_t = h.index('USUARIO'), h.index('TELEFONO')
            for buscado in ('RUBEN POZON', 'GONZALO FIGUEREDO', 'RAUL LUCAS'):
                r = next((r for r in p[1:] if len(r) > max(i_u, i_t) and _norm(r[i_u]) == buscado and r[i_t].strip()), None)
                if r:
                    d['reenviar'].append({'usuario': r[i_u].strip(), 'telefono': r[i_t].strip()})
        except Exception as e:
            print(f"[FALTAS] destinatarios: {e}", flush=True)
    return d['reenviar']


def _fecha(txt):
    p = [int(x) for x in str(txt).strip().split()[0].split('/')]
    return date(p[2], p[1], p[0])


def _dias_txt(fechas):
    fs = [_fecha(f) for f in fechas]
    partes = [f'{_DIAS[f.weekday()]} {f:%d/%m}' for f in fs]
    return ' y '.join([', '.join(partes[:-1]), partes[-1]]) if len(partes) > 1 else partes[0]


# ---------- detección ----------

def _rachas(filas, equipo_n):
    """{jugador_norm: (nombre, [fechas 'dd/mm/aaaa' de la racha actual de NO consecutivos])} de un equipo.
    Las sesiones del equipo son los días en que tiene alguna asistencia guardada (desde DESDE)."""
    sesiones, estado = set(), {}
    for r in filas:
        if len(r) < 5 or _norm(r[1]) != equipo_n:
            continue
        try:
            f = _fecha(r[0])
        except Exception:
            continue
        if f < DESDE:
            continue
        sesiones.add(f)
        jug = _norm(r[2])
        if jug:
            est = r[4].strip().upper()
            prev = estado.get((jug, f))
            if prev is None or (prev[1] == 'NO' and est != 'NO'):  # mismo día repetido: cuenta si alguna vez no fue NO
                estado[(jug, f)] = (r[2].strip(), est)
    out = {}
    for jug in {j for j, _ in estado}:
        racha, nombre = [], ''
        for f in sorted(sesiones):
            e = estado.get((jug, f))
            if e is None:
                continue  # ese día no se le pasó lista: no corta ni suma
            nombre = e[0]
            racha = racha + [f'{f:%d/%m/%Y}'] if e[1] == 'NO' else []
        out[jug] = (nombre, racha)
    return out


def revisar_equipos(equipos=None):
    """Busca rachas de 2+ NO seguidos y pregunta a los entrenadores. equipos=None → todos."""
    from app import client, NOMBRE_EXCEL
    d = _leer()
    if not d.get('activo'):
        return
    filas = client.open(NOMBRE_EXCEL).worksheet('ASISTENCIAS').get_all_values()
    nombres_eq = sorted({r[1].strip().upper() for r in filas if len(r) > 1 and r[1].strip()})
    objetivo = [e for e in nombres_eq if not _norm(e).startswith('AFICIONADO')
                and (equipos is None or _norm(e) in {_norm(x) for x in equipos})]
    for eq in objetivo:
        for jug, (nombre, racha) in _rachas(filas, _norm(eq)).items():
            if len(racha) >= 2:
                _gestionar_racha(eq, jug, nombre, racha)


def _gestionar_racha(equipo, jug, nombre, racha):
    """Crea la pregunta si toca: primera vez 2 NO seguidos; tras respuesta, cada 2 NO más en la misma racha."""
    with _lock:
        d = _leer()
        casos = [c for c in d['casos'].values() if c['equipo'] == equipo and c['jugador_n'] == jug
                 and c['fechas'][0] in racha and not c.get('prueba')]
        pendiente = next((c for c in casos if c['estado'] == 'pendiente'), None)
        if pendiente:
            nuevas = [f for f in racha if f not in pendiente['fechas']]
            if nuevas:  # sigue faltando sin contestar: se amplían las fechas (el recordatorio ya irá)
                pendiente['fechas'] += nuevas
                _escribir(d)
            return
        ya = {f for c in casos for f in c['fechas']}
        nuevas = [f for f in racha if f not in ya]
        if len(nuevas) < 2:
            return
        clave = f"{equipo}|{jug}|{nuevas[-1]}"
        d['casos'][clave] = {'equipo': equipo, 'jugador': nombre, 'jugador_n': jug, 'fechas': nuevas,
                             'seguimiento': bool(casos), 'estado': 'pendiente', 'creado': datetime.now().strftime('%Y-%m-%dT%H:%M'),
                             'envios': [], 'msg_ids': []}
        _escribir(d)
    _preguntar(clave)


def _texto_pregunta(caso, nombre_ent, recordatorio=False):
    n = len(caso['fechas'])
    cuantos = 'los 2 últimos entrenamientos' if n == 2 else f'los {n} últimos entrenamientos'
    return ((f'⏰ Recordatorio. ' if recordatorio else '') +
            f"Hola {nombre_ent}, *{_nombre(caso['jugador'])}* ({_bonito(caso['equipo'])}) no ha venido a {cuantos} "
            f"({_dias_txt(caso['fechas'])}).\n¿Sabes el motivo? Responde a este mensaje.\n\nDirección Deportiva")


def _preguntar(clave, recordatorio=False):
    from formulario_finde import _entrenadores
    from whatsapp_envios import enviar_con_id
    d = _leer()
    caso = d['casos'].get(clave)
    if not caso or caso['estado'] != 'pendiente':
        return
    enviados = 0
    destinos = [(caso['prueba_usuario'], caso['prueba_tel'], '')] if caso.get('prueba') else _entrenadores([caso['equipo']])
    for usuario, tel, _ in destinos:
        if not tel:
            continue
        ok, detalle, mid = enviar_con_id(tel, _texto_pregunta(caso, usuario.split()[0].capitalize(), recordatorio))
        with _lock:
            d = _leer()
            c = d['casos'].get(clave)
            c['envios'].append({'en': datetime.now().strftime('%Y-%m-%dT%H:%M'), 'a': usuario, 'telefono': tel, 'ok': ok})
            if mid:
                c['msg_ids'].append(mid)
            _escribir(d)
        enviados += bool(ok)
    print(f"[FALTAS] {'Recordatorio' if recordatorio else 'Pregunta'} {caso['jugador']} ({caso['equipo']}): enviado a {enviados}", flush=True)


def revisar_en_segundo_plano(equipos):
    """Tras guardar asistencias: revisa esos equipos sin hacer esperar al entrenador."""
    def _job():
        try:
            revisar_equipos(equipos)
        except Exception as e:
            print(f"[FALTAS] revisión tras guardar: {e}", flush=True)
    threading.Thread(target=_job, daemon=True).start()


def tick(ahora):
    """Cada 30 min revisa todo; a las 21:00 recuerda las pendientes de los equipos que entrenan hoy."""
    d = _leer()
    if not d.get('activo'):
        return
    if ahora.minute % 30 == 0:
        try:
            revisar_equipos()
        except Exception as e:
            print(f"[FALTAS] revisión: {e}", flush=True)
    if (ahora.hour, ahora.minute) == HORA_RECORDATORIO:
        from aviso_diario import _dias_entreno
        dias = _dias_entreno()
        hoy = ahora.date().isoformat()
        for clave, c in list(_leer()['casos'].items()):
            if c['estado'] != 'pendiente' or ahora.weekday() not in dias.get(_norm(c['equipo']), set()):
                continue
            if any(e['en'][:10] == hoy for e in c['envios']):
                continue  # hoy ya se le ha preguntado
            _preguntar(clave, recordatorio=True)


# ---------- respuestas ----------

def _pendientes_de(tel, d):
    """Casos pendientes en los que se ha preguntado a ese teléfono."""
    corto = lambda t: ''.join(c for c in str(t) if c.isdigit())[-9:]
    return [(k, c) for k, c in d['casos'].items() if c['estado'] == 'pendiente'
            and any(corto(e['telefono']) == corto(tel) for e in c['envios'])]


def jugadores_de_respuesta(texto, casos):
    """Claves de los casos cuyo jugador menciona el texto (por nombre o apellido)."""
    palabras = set(re.sub(r'[^A-Z0-9 ]', ' ', _norm(texto)).split())
    elegidos = []
    for k, c in casos:
        trozos = [w for w in _norm(_limpio(c['jugador'])).split() if len(w) > 2]
        otros = {w for k2, c2 in casos if k2 != k for w in _norm(_limpio(c2['jugador'])).split()}
        propios = [w for w in trozos if w not in otros] or trozos
        if any(w in palabras for w in propios):
            elegidos.append(k)
    return elegidos


def _pregunta_jugador(casos, repetir=False):
    nombres = [_nombre(c['jugador']) for _, c in casos]
    lista = ', '.join(nombres[:-1]) + f' o {nombres[-1]}' if len(nombres) > 1 else nombres[0]
    return ('No te he entendido 🙏 ' if repetir else '') + f'¿De qué jugador es esta respuesta? {lista}'


def procesar_texto(tel, cuerpo, rep_id, usuario):
    """Respuesta de un entrenador. Devuelve True si era sobre una falta (ya gestionada)."""
    from whatsapp_envios import enviar_por_waha
    if not cuerpo:
        return False
    corto = lambda x: str(x).split('_')[-1]
    with _lock:
        d = _leer()
        pend = _pendientes_de(tel, d)
        if not pend:
            return False
        citado = next(((k, c) for k, c in pend if rep_id and corto(rep_id) in [corto(m) for m in c.get('msg_ids', [])]), None)
        esperando = d['esperando_jugador'].get(tel)
    if citado:
        _responder(citado[0], cuerpo, usuario, tel)
        return True
    if esperando:  # me está diciendo de qué jugador era su respuesta anterior
        elegidos = jugadores_de_respuesta(cuerpo, pend)
        if len(elegidos) != 1:
            enviar_por_waha(tel, _pregunta_jugador(pend, repetir=True))
            return True
        with _lock:
            d = _leer()
            d['esperando_jugador'].pop(tel, None)
            _escribir(d)
        _responder(elegidos[0], esperando, usuario, tel)
        return True
    if len(pend) == 1:
        _responder(pend[0][0], cuerpo, usuario, tel)
        return True
    elegidos = jugadores_de_respuesta(cuerpo, pend)
    if len(elegidos) == 1:  # ya dice de quién habla
        _responder(elegidos[0], cuerpo, usuario, tel)
        return True
    with _lock:
        d = _leer()
        d['esperando_jugador'][tel] = cuerpo
        _escribir(d)
    enviar_por_waha(tel, _pregunta_jugador(pend))
    return True


def _responder(clave, respuesta, usuario, tel):
    """Guarda la respuesta, la apunta en AUSENCIAS SIN JUSTIFICAR, la reenvía y contesta 👌."""
    from whatsapp_envios import enviar_por_waha
    from ausencias_monitor import guardar_motivo_dia
    with _lock:
        d = _leer()
        c = d['casos'].get(clave)
        if not c or c['estado'] != 'pendiente':
            return
        c.update(estado='respondido', respuesta=respuesta[:1000], respondido_por=usuario,
                 respondido_en=datetime.now().strftime('%Y-%m-%dT%H:%M'))
        destinatarios = [{'usuario': usuario, 'telefono': tel}] if c.get('prueba') else _destinatarios(d)
        _escribir(d)
    for f in ([] if c.get('prueba') else c['fechas']):
        try:
            guardar_motivo_dia('asistencia', c['equipo'], c['jugador'], f, respuesta[:500], usuario)
        except Exception as e:
            print(f"[FALTAS] guardar motivo: {e}", flush=True)
    fechas_txt = ' y '.join(f[:5] for f in c['fechas'])
    texto = (f"Motivo falta · {_nombre(c['jugador'])} ({_bonito(c['equipo'])}) · {fechas_txt}\n"
             f"{usuario.split()[0].capitalize()}: \"{respuesta.strip()}\"")
    for dest in destinatarios:
        num = ''.join(ch for ch in dest.get('telefono', '') if ch.isdigit())
        enviar_por_waha('34' + num if len(num) == 9 else num, texto)
    enviar_por_waha(tel, '👌')
    print(f"[FALTAS] Respuesta de {usuario} sobre {c['jugador']}: reenviada a {len(destinatarios)}", flush=True)


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@faltas_bp.route('/api/whatsapp/faltas', methods=['GET', 'POST'])
def api_faltas():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    with _lock:
        d = _leer()
        if request.method == 'POST':
            data = request.json or {}
            if 'activo' in data:
                d['activo'] = bool(data['activo'])
            if 'reenviar' in data:
                d['reenviar'] = [{'usuario': (x.get('usuario') or '').strip(), 'telefono': (x.get('telefono') or '').strip()}
                                 for x in (data.get('reenviar') or []) if (x.get('telefono') or '').strip()]
        reenviar = _destinatarios(d)
        _escribir(d)
    casos = sorted(d['casos'].values(), key=lambda c: c.get('creado', ''), reverse=True)[:60]
    return jsonify({'status': 'ok', 'activo': d['activo'], 'reenviar': reenviar, 'desde': DESDE.strftime('%d/%m/%Y'),
                    'casos': [{'equipo': c['equipo'], 'jugador': _nombre(c['jugador']), 'fechas': c['fechas'],
                               'estado': c['estado'], 'respuesta': c.get('respuesta', ''), 'por': c.get('respondido_por', ''),
                               'preguntas': len(c.get('envios', []))} for c in casos]})


@faltas_bp.route('/api/whatsapp/faltas/vista')
def api_vista():
    """Qué jugadores generarían pregunta ahora mismo (no envía nada)."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        from app import client, NOMBRE_EXCEL
        from formulario_finde import _entrenadores
        d = _leer()
        filas = client.open(NOMBRE_EXCEL).worksheet('ASISTENCIAS').get_all_values()
        eqs = sorted({r[1].strip().upper() for r in filas if len(r) > 1 and r[1].strip() and not _norm(r[1]).startswith('AFICIONADO')})
        con_casos = {(c['equipo'], c['jugador_n']) for c in d['casos'].values() if not c.get('prueba')}
        out = []
        for eq in eqs:
            ents = [u for u, t, _ in _entrenadores([eq]) if t]
            for jug, (nombre, racha) in _rachas(filas, _norm(eq)).items():
                if len(racha) >= 2 and (eq, jug) not in con_casos:
                    out.append({'equipo': eq, 'jugador': _nombre(nombre), 'fechas': racha, 'entrenadores': ents})
        return jsonify({'status': 'ok', 'preguntas': out})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]})


@faltas_bp.route('/api/whatsapp/faltas/prueba', methods=['POST'])
def api_prueba():
    """Prueba con un equipo: pregunta por un jugador real (sus 2 últimos entrenamientos) solo a quien prueba.
    La respuesta se reenvía solo a esa persona y no se apunta en ausencias."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    data = request.json or {}
    equipo = (data.get('equipo') or 'INFANTIL B').strip().upper()
    tel, usuario = (data.get('telefono') or '').strip(), (data.get('usuario') or '').strip()
    if not tel:
        return jsonify({'status': 'error', 'message': 'Falta el teléfono'}), 400
    try:
        from app import client, NOMBRE_EXCEL
        filas = [r for r in client.open(NOMBRE_EXCEL).worksheet('ASISTENCIAS').get_all_values()
                 if len(r) > 4 and _norm(r[1]) == _norm(equipo)]
        fechas = sorted({_fecha(r[0]) for r in filas if re.match(r'^\d{1,2}/\d{1,2}/\d{4}', r[0].strip())})[-2:]
        if len(fechas) < 2:
            return jsonify({'status': 'error', 'message': f'{equipo} no tiene 2 entrenamientos con asistencia'})
        en_ambos = [r[2].strip() for r in filas if _fecha(r[0]) == fechas[-1] and r[2].strip()]
        jugador = next((j for j in en_ambos if any(_norm(r[2]) == _norm(j) and r[4].strip().upper() == 'NO' for r in filas)), en_ambos[0])
        clave = f"PRUEBA|{equipo}|{_norm(jugador)}|{datetime.now():%Y%m%d%H%M%S}"
        with _lock:
            d = _leer()
            d['casos'][clave] = {'equipo': equipo, 'jugador': jugador, 'jugador_n': _norm(jugador),
                                 'fechas': [f'{f:%d/%m/%Y}' for f in fechas], 'seguimiento': False, 'estado': 'pendiente',
                                 'creado': datetime.now().strftime('%Y-%m-%dT%H:%M'), 'envios': [], 'msg_ids': [],
                                 'prueba': True, 'prueba_tel': tel, 'prueba_usuario': usuario or 'Prueba'}
            _escribir(d)
        _preguntar(clave)
        return jsonify({'status': 'ok', 'message': f'Pregunta de prueba enviada sobre {_nombre(jugador)} ({equipo})'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]})
