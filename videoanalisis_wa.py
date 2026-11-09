"""Videoanálisis: cuando Joaquín Fenellos escribe en FR Entrenadores "Videoanálisis subido al drive..." con los equipos
y un ✅, se pregunta por privado a los entrenadores de cada equipo con ✅ qué día y a qué hora lo harán con el equipo.

- Respuesta por privado (o un mensaje suelto en el grupo con día y hora) → se publica en el grupo el mensaje con todo
  actualizado. Si un entrenador cambia la hora, se actualiza y se vuelve a publicar.
- Si otro entrenador del mismo equipo dice otra hora, se le pregunta si se cambia (SÍ/NO).
- Joaquín puede mandar primero equipos sin ✅ y repetir el mensaje con ✅ más tarde: solo se pregunta por los ✅
  y nunca dos veces por el mismo equipo y finde.
- Sin respuesta: recordatorio a las 12:00 los dos días siguientes.
- Modo prueba: todo (preguntas y lo que se publicaría) solo a Rubén Pozón.
Estado en datos_privados/videoanalisis.json.
"""
import os
import re
import json
import unicodedata
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from flask import Blueprint, request, jsonify

video_bp = Blueprint('videoanalisis_wa', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'datos_privados', 'videoanalisis_wa.json')
LOCK_FILE = os.path.join(BASE_DIR, 'datos_privados', '.videoanalisis_wa.lock')
TEL_JOAQUIN = '626286447'
HORA_RECORDATORIO = (12, 0)
_DIAS = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']
_DIAS_CAP = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
_DIAS_CORTOS = {'lun': 0, 'mie': 2, 'jue': 3, 'vie': 4, 'sab': 5, 'dom': 6}


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.upper().split())


def _bonito(eq):
    return (' '.join(eq.upper().split()).replace('PREBENJAMIN', 'PREBENJAMÍN')
            .replace('BENJAMIN', 'BENJAMÍN').replace('ALEVIN', 'ALEVÍN'))


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
    d.setdefault('activo', False)
    d.setdefault('findes', {})
    d.setdefault('esperando', {})  # tel → {'tipo': 'equipo'|'conflicto', ...}
    d.setdefault('mensajes', {})   # id de mensaje enviado → [finde, equipo]
    return d


def _escribir(d):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def _tel9(t):
    return ''.join(c for c in str(t or '') if c.isdigit())[-9:]


def _sabado(d):
    """Sábado del finde al que se refiere un mensaje de ese día (sáb/dom → ese finde; lun-vie → el anterior)."""
    return d - timedelta(days=(d.weekday() - 5) % 7)


# ---------- entender el mensaje de Joaquín ----------

def _equipos_config():
    from informe_veo import _equipos_config as ec
    return ec()


def equipos_del_mensaje(texto, equipos=None):
    """[(equipo, tiene_tick)] en el orden del mensaje. 'Juvenil "C": ✅' → ('JUVENIL C', True)."""
    equipos = equipos if equipos is not None else _equipos_config()
    out = []
    for linea in str(texto or '').splitlines():
        palabras = re.sub(r'[^A-Z0-9 ]', ' ', _norm(linea)).split()
        if not palabras:
            continue
        candidatos = []
        for e in equipos:
            pe = _norm(e).split()
            base = [w for w in pe if w not in ('F7', 'F11')]
            if not all(w in palabras for w in base):
                continue
            formato = [w for w in pe if w in ('F7', 'F11')]
            if formato and any(w in ('F7', 'F11') for w in palabras) and formato[0] not in palabras:
                continue
            candidatos.append(e)
        # Si encajan varios (p. ej. ALEVIN A y ALEVIN A F7), el de más palabras en común
        if candidatos:
            mejor = max(candidatos, key=lambda e: sum(w in palabras for w in _norm(e).split()))
            tick = any(c in linea for c in ('✅', '✔', '☑'))
            if mejor not in [x[0] for x in out]:
                out.append((mejor, tick))
    return out


def finde_del_mensaje(texto):
    """'Videoanalisis subido al drive. Finde del 31 octubre y 1 noviembre' → 'finde del 31 octubre y 1 noviembre'."""
    for linea in str(texto or '').splitlines():
        m = re.search(r'(finde[^\n]*)', linea, re.I)
        if m:
            return m.group(1).strip(' .:').lower()
    return ''


def _finde_txt(f):
    if f.get('titulo'):
        return f['titulo']
    sab = date.fromisoformat(f['sabado'])
    return f"finde {sab - timedelta(days=1):%d/%m}-{sab + timedelta(days=1):%d/%m}"


def es_mensaje_videoanalisis(texto):
    t = _norm(texto).replace(' ', '')
    return 'VIDEOANALISIS' in t


# ---------- día y hora ----------

def entender_cuando(texto, hoy=None):
    """(fecha o None, (hora, minuto) o None) de textos como 'martes 18:00', 'el jueves a las 7 y media', 'mañana 19h'."""
    hoy = hoy or date.today()
    t = ' ' + ' '.join(re.sub(r'[^a-z0-9:/. ]', ' ', _norm(texto).lower()).split()) + ' '
    fecha = None
    m = re.search(r'\b(\d{1,2})/(\d{1,2})\b', t)
    if m:
        try:
            fecha = date(hoy.year, int(m.group(2)), int(m.group(1)))
            if fecha < hoy - timedelta(days=60):
                fecha = date(hoy.year + 1, fecha.month, fecha.day)
        except ValueError:
            fecha = None
        t = t.replace(m.group(0), ' ')
    if not fecha:
        dia = next((i for i, n in enumerate(_DIAS) if f' {n} ' in t), None)
        if dia is None:
            dia = next((i for n, i in _DIAS_CORTOS.items() if f' {n} ' in t), None)
        if dia is not None:
            fecha = hoy + timedelta(days=(dia - hoy.weekday()) % 7)
        elif ' pasado manana ' in t:
            fecha = hoy + timedelta(days=2)
        elif ' hoy ' in t:
            fecha = hoy
        elif re.search(r' manana (a las|sobre|por la tarde|\d)', t) or t.strip().startswith('manana'):
            fecha = hoy + timedelta(days=1)
    hora = None
    m = re.search(r'\b(\d{1,2})[:.](\d{2})\b', t)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        hora = (int(m.group(1)), int(m.group(2)))
    else:
        m = (re.search(r'\ba las (\d{1,2})(?: y (media|cuarto))?\b', t) or re.search(r'\bsobre las (\d{1,2})(?: y (media|cuarto))?\b', t)
             or re.search(r'\b(\d{1,2}) ?(?:h|hrs|horas)\b', t))
        if m and int(m.group(1)) < 24:
            extra = m.group(2) if m.re.groups > 1 else None
            hora = (int(m.group(1)), 30 if extra == 'media' else 15 if extra == 'cuarto' else 0)
    if hora and hora[0] <= 9 and ' de la manana ' not in t and ' por la manana ' not in t:
        hora = (hora[0] + 12, hora[1])  # "a las 7" en un club de fútbol base es por la tarde
    return fecha, hora


def _texto_cuando(fecha, hora):
    return f'{_DIAS_CAP[fecha.weekday()]} {fecha:%d/%m} {hora[0]:02d}:{hora[1]:02d}'


# ---------- envío ----------

def _tel_ruben():
    from informe_veo import _telefono_ruben
    return _telefono_ruben()


def _publicar(d, clave):
    """Publica en el grupo (o a Rubén en prueba) el mensaje con todo actualizado."""
    from whatsapp_envios import enviar_por_waha
    f = d['findes'][clave]
    lineas = []
    for eq in f['orden']:
        e = f['equipos'][eq]
        if not e['tick']:
            lineas.append(f"• {_bonito(eq)} → videoanálisis aún no subido")
        elif e.get('cuando'):
            lineas.append(f"• {_bonito(eq)} ✅ → *{e['cuando']}*")
        else:
            lineas.append(f"• {_bonito(eq)} ✅ → pendiente de día y hora")
    texto = (('🧪 PRUEBA (no se ha publicado en el grupo)\n' if f.get('prueba') else '') +
             f"🎥 *Videoanálisis – {_finde_txt(f)[:1].upper() + _finde_txt(f)[1:]}*\n" +
             '\n'.join(lineas) + '\n\nDirección Deportiva')
    if f.get('prueba'):
        destino = ''.join(c for c in _tel_ruben() if c.isdigit())
        destino = '34' + destino if len(destino) == 9 else destino
    else:
        from objetivos_finde import _grupo_fr
        destino = _grupo_fr()
    if destino:
        enviar_por_waha(destino, texto)
    print(f"[VIDEO] Publicado ({clave})", flush=True)


def _destinos(f, eq):
    """[(usuario, telefono)] a los que preguntar por ese equipo (en prueba, solo Rubén)."""
    if f.get('prueba'):
        return [('RUBEN POZON', _tel_ruben())]
    from formulario_finde import _entrenadores
    return [(u, t) for u, t, _ in _entrenadores([eq]) if t and _tel9(t) != TEL_JOAQUIN]


def _preguntar(clave, eq, recordatorio=False):
    from whatsapp_envios import enviar_con_id
    with _bloqueo():
        d = _leer()
        f = d['findes'].get(clave)
        if not f:
            return
        destinos = _destinos(f, eq)
    for usuario, tel in destinos:
        texto = (('🧪 PRUEBA. ' if f.get('prueba') else '') + ('⏰ Recordatorio. ' if recordatorio else '') +
                 f"Hola {usuario.split()[0].capitalize()}, Joaquín ha subido al Drive el videoanálisis del *{_bonito(eq)}* "
                 f"({_finde_txt(f)}).\n"
                 f"¿Qué día y a qué hora lo haréis con el equipo? Responde a este mensaje (por ejemplo: martes 18:00).\n\n"
                 f"Dirección Deportiva")
        ok, _, mid = enviar_con_id(tel, texto)
        with _bloqueo():
            d = _leer()
            e = d['findes'][clave]['equipos'][eq]
            e.setdefault('preguntado', []).append({'en': datetime.now().isoformat(timespec='minutes'), 'a': usuario,
                                                   'telefono': tel, 'ok': ok})
            if mid:
                d['mensajes'][mid] = [clave, eq]
            _escribir(d)
    print(f"[VIDEO] {'Recordatorio' if recordatorio else 'Pregunta'} {eq} ({clave}) a {len(destinos)}", flush=True)


def registrar_mensaje_joaquin(texto, prueba=False):
    """Mensaje de Joaquín: guarda los equipos (y sus ✅) y pregunta por los ✅ nuevos. Devuelve [(equipo, tick)]."""
    hoy = date.today()
    lista = equipos_del_mensaje(texto)
    if not lista:
        print(f"[VIDEO] Mensaje de videoanálisis sin equipos reconocibles: {texto[:80]!r}", flush=True)
        return []
    clave = 'PRUEBA' if prueba else _sabado(hoy).isoformat()
    nuevos = []
    with _bloqueo():
        d = _leer()
        if prueba:
            d['findes'].pop('PRUEBA', None)
            d['mensajes'] = {k: v for k, v in d['mensajes'].items() if v[0] != 'PRUEBA'}
            d['esperando'] = {k: v for k, v in d['esperando'].items() if v.get('finde') != 'PRUEBA'}
        f = d['findes'].setdefault(clave, {'sabado': _sabado(hoy).isoformat(), 'orden': [], 'equipos': {},
                                          'creado': datetime.now().isoformat(timespec='minutes'), 'prueba': prueba})
        f['titulo'] = finde_del_mensaje(texto) or f.get('titulo', '')
        for eq, tick in lista:
            if eq not in f['equipos']:
                f['orden'].append(eq)
                f['equipos'][eq] = {'tick': False, 'cuando': '', 'por': '', 'recordatorios': 0}
            e = f['equipos'][eq]
            if tick and not e['tick']:
                e['tick'] = True
                if not e.get('cuando') and not e.get('preguntado'):
                    nuevos.append(eq)
        _escribir(d)
    for eq in nuevos:
        _preguntar(clave, eq)
    return lista


# ---------- respuestas de los entrenadores ----------

def _abiertos(d, tel, usuario, solo_pendientes=True):
    """[(finde, equipo)] de los últimos días en los que ese entrenador tiene que (o puede) decir día y hora."""
    from formulario_finde import _entrenador_por_tel
    hoy = date.today()
    out = []
    equipos_ent = None
    for clave, f in d['findes'].items():
        if f.get('prueba'):
            # Las pruebas caducan a las 2 horas (para no confundir mensajes posteriores de Rubén)
            if datetime.now() - datetime.fromisoformat(f['creado']) > timedelta(hours=2) or _tel9(tel) != _tel9(_tel_ruben()):
                continue
        elif date.fromisoformat(f['creado'][:10]) < hoy - timedelta(days=7):
            continue
        for eq in f['orden']:
            e = f['equipos'][eq]
            if not e['tick'] or (solo_pendientes and e.get('cuando')):
                continue
            if not f.get('prueba'):
                if equipos_ent is None:
                    ent = _entrenador_por_tel(tel)
                    equipos_ent = {_norm(x) for x in (ent[1] if ent else [])}
                if _norm(eq) not in equipos_ent:
                    continue
            out.append((clave, eq))
    return out


def _fijar(clave, eq, cuando, usuario, tel, privado):
    """Apunta día y hora (o pregunta al 2º entrenador si ya había otra). Devuelve el texto a contestar o None."""
    with _bloqueo():
        d = _leer()
        e = d['findes'][clave]['equipos'][eq]
        if e.get('cuando') and e['cuando'] != cuando and _norm(e.get('por')) != _norm(usuario):
            d['esperando'][tel] = {'tipo': 'conflicto', 'finde': clave, 'equipo': eq, 'nuevo': cuando}
            _escribir(d)
            return (f"Tu compañero {e['por'].title()} ya ha dicho *{e['cuando']}* para el videoanálisis del *{_bonito(eq)}*. "
                    f"¿Lo cambiamos a *{cuando}*? Responde SÍ o NO.")
        if e.get('cuando') == cuando:
            return '👌' if privado else None
        e.update({'cuando': cuando, 'por': usuario, 'cuando_en': datetime.now().isoformat(timespec='minutes')})
        _publicar(d, clave)
        _escribir(d)
    return '👌' if privado else None


def procesar_texto(tel, cuerpo, rep_id, usuario):
    """Mensaje privado de un entrenador. True si era para el videoanálisis (y ya se ha contestado)."""
    from whatsapp_envios import enviar_por_waha
    if not cuerpo:
        return False
    with _bloqueo():
        d = _leer()
    if not d['activo'] and 'PRUEBA' not in d['findes']:
        return False
    contestar = lambda txt: txt and enviar_por_waha(tel if len(tel) > 9 else '34' + tel, txt)
    esperando = d['esperando'].get(tel)
    if esperando and esperando['finde'] in d['findes']:
        if esperando['tipo'] == 'conflicto':
            r = _norm(cuerpo).lower()
            si = r.startswith(('si', 'vale', 'ok', 'cambia', 'de acuerdo')) or r in ('s', 'yes')
            no = r.startswith(('no', 'deja', 'mantener', 'se queda'))
            if si or no:
                with _bloqueo():
                    d = _leer()
                    d['esperando'].pop(tel, None)
                    if si:
                        e = d['findes'][esperando['finde']]['equipos'][esperando['equipo']]
                        e.update({'cuando': esperando['nuevo'], 'por': usuario,
                                  'cuando_en': datetime.now().isoformat(timespec='minutes')})
                        _publicar(d, esperando['finde'])
                    _escribir(d)
                actual = d['findes'][esperando['finde']]['equipos'][esperando['equipo']]['cuando']
                contestar('👌' if si else f"👌 Se queda {actual}.")
                return True
            # Si en vez de SÍ/NO manda otro día y hora, se trata como nueva respuesta
            fecha, hora = entender_cuando(cuerpo)
            if not (fecha and hora):
                contestar('Responde SÍ (se cambia) o NO (se queda la hora de tu compañero).')
                return True
            with _bloqueo():
                d = _leer()
                d['esperando'].pop(tel, None)
                _escribir(d)
            contestar(_fijar(esperando['finde'], esperando['equipo'], _texto_cuando(fecha, hora), usuario, tel, True))
            return True
        if esperando['tipo'] == 'equipo':
            from formulario_finde import equipos_de_respuesta
            elegidos = equipos_de_respuesta(cuerpo, [eq for _, eq in esperando['opciones']])
            if len(elegidos) != 1:
                contestar('¿De qué equipo es? ' + ' o '.join(_bonito(eq) for _, eq in esperando['opciones']) + '.')
                return True
            clave = next(c for c, eq in esperando['opciones'] if eq == elegidos[0])
            with _bloqueo():
                d = _leer()
                d['esperando'].pop(tel, None)
                _escribir(d)
            contestar(_fijar(clave, elegidos[0], esperando['cuando'], usuario, tel, True))
            return True

    citado = d['mensajes'].get(rep_id) if rep_id else None
    fecha, hora = entender_cuando(cuerpo)
    pendientes = _abiertos(d, tel, usuario)
    if citado:
        opciones = [tuple(citado)]
    elif pendientes and (fecha or hora):
        opciones = pendientes
    elif fecha and hora:
        opciones = _abiertos(d, tel, usuario, solo_pendientes=False)  # cambio de una hora ya dada
        if not opciones:
            return False
    else:
        return False
    if not (fecha and hora):
        falta = 'el día' if hora else 'la hora' if fecha else 'el día y la hora'
        contestar(f"No he entendido {falta}. Dímelo así, por ejemplo: *martes 18:00*.")
        return True
    cuando = _texto_cuando(fecha, hora)
    if len(opciones) > 1:
        from formulario_finde import equipos_de_respuesta
        elegidos = equipos_de_respuesta(cuerpo, [eq for _, eq in opciones])
        if len(elegidos) != 1:
            with _bloqueo():
                d = _leer()
                d['esperando'][tel] = {'tipo': 'equipo', 'finde': opciones[0][0], 'opciones': opciones, 'cuando': cuando}
                _escribir(d)
            contestar(f"¿De qué equipo es el {cuando}? " + ' o '.join(_bonito(eq) for _, eq in opciones) + '.')
            return True
        opciones = [o for o in opciones if o[1] == elegidos[0]]
    clave, eq = opciones[0]
    contestar(_fijar(clave, eq, cuando, usuario, tel, True))
    return True


def procesar_grupo(payload, origen):
    """Mensaje en un grupo: el de Joaquín en FR Entrenadores, o un entrenador diciendo día y hora."""
    if payload.get('fromMe'):
        return
    d = _leer()
    if not d['activo']:
        return
    from objetivos_finde import _grupo_fr
    if origen != _grupo_fr():
        return
    from formulario_finde import _telefono_de, _usuario_por_tel
    cuerpo = (payload.get('body') or '').strip()
    participante = str(payload.get('participant') or (payload.get('_data') or {}).get('Info', {}).get('Sender') or '')
    tel = _telefono_de(payload, participante)
    if not cuerpo or not tel:
        return
    if _tel9(tel) == TEL_JOAQUIN and es_mensaje_videoanalisis(cuerpo):
        lista = registrar_mensaje_joaquin(cuerpo)
        print(f"[VIDEO] Mensaje de Joaquín: {lista}", flush=True)
        return
    usuario = _usuario_por_tel(tel)
    fecha, hora = entender_cuando(cuerpo)
    if not (usuario and fecha and hora):
        return
    opciones = _abiertos(d, tel, usuario) or _abiertos(d, tel, usuario, solo_pendientes=False)
    if not opciones:
        return
    cuando = _texto_cuando(fecha, hora)
    if len(opciones) > 1:
        from formulario_finde import equipos_de_respuesta
        elegidos = equipos_de_respuesta(cuerpo, [eq for _, eq in opciones])
        if len(elegidos) != 1:
            from whatsapp_envios import enviar_por_waha
            with _bloqueo():
                d = _leer()
                d['esperando'][tel] = {'tipo': 'equipo', 'finde': opciones[0][0], 'opciones': opciones, 'cuando': cuando}
                _escribir(d)
            enviar_por_waha(tel if len(tel) > 9 else '34' + tel,
                            f"Has dicho {cuando} en el grupo. ¿Para qué equipo es el videoanálisis? "
                            + ' o '.join(_bonito(eq) for _, eq in opciones) + '.')
            return
        opciones = [o for o in opciones if o[1] == elegidos[0]]
    respuesta = _fijar(opciones[0][0], opciones[0][1], cuando, usuario, tel, False)
    if respuesta:  # conflicto con otro entrenador: se le pregunta por privado
        from whatsapp_envios import enviar_por_waha
        enviar_por_waha(tel if len(tel) > 9 else '34' + tel, respuesta)


def tick(ahora):
    """A las 12:00, recordatorio a los que no han contestado (máximo 2, en días distintos al de la pregunta)."""
    if (ahora.hour, ahora.minute) != HORA_RECORDATORIO:
        return
    d = _leer()
    if not d['activo']:
        return
    hoy = ahora.date().isoformat()
    for clave, f in d['findes'].items():
        if f.get('prueba') or date.fromisoformat(f['creado'][:10]) < ahora.date() - timedelta(days=7):
            continue
        for eq in f['orden']:
            e = f['equipos'][eq]
            if not e['tick'] or e.get('cuando') or e.get('recordatorios', 0) >= 2 or not e.get('preguntado'):
                continue
            if any(p['en'][:10] == hoy for p in e['preguntado']):
                continue
            with _bloqueo():
                d2 = _leer()
                d2['findes'][clave]['equipos'][eq]['recordatorios'] = e.get('recordatorios', 0) + 1
                _escribir(d2)
            _preguntar(clave, eq, recordatorio=True)


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@video_bp.route('/api/whatsapp/videoanalisis', methods=['GET', 'POST'])
def api_cfg():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    with _bloqueo():
        d = _leer()
        if request.method == 'POST' and 'activo' in (request.json or {}):
            d['activo'] = bool(request.json['activo'])
            _escribir(d)
    findes = []
    for clave, f in sorted(d['findes'].items(), key=lambda x: x[1].get('creado', ''), reverse=True)[:6]:
        findes.append({'clave': clave, 'prueba': bool(f.get('prueba')), 'sabado': f['sabado'],
                       'equipos': [{'equipo': eq, **{k: f['equipos'][eq].get(k) for k in ('tick', 'cuando', 'por')},
                                    'preguntas': len(f['equipos'][eq].get('preguntado', []))} for eq in f['orden']]})
    return jsonify({'status': 'ok', 'activo': d['activo'], 'findes': findes})


@video_bp.route('/api/whatsapp/videoanalisis/prueba', methods=['POST'])
def api_prueba():
    """Hace como si Joaquín hubiera escrito ese mensaje: preguntas y lo publicado van solo a Rubén."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    texto = (request.json or {}).get('texto', '')
    if not es_mensaje_videoanalisis(texto):
        return jsonify({'status': 'error', 'message': 'El mensaje tiene que llevar la palabra "videoanálisis"'})
    try:
        lista = registrar_mensaje_joaquin(texto, prueba=True)
        if not lista:
            return jsonify({'status': 'error', 'message': 'No reconozco ningún equipo en el mensaje'})
        return jsonify({'status': 'ok', 'equipos': [{'equipo': e, 'tick': t} for e, t in lista]})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]})
