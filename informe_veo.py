"""Informe del partido de Veo por WhatsApp a los entrenadores de cada equipo (martes 20:00).

Vídeos del último fin de semana (viernes a domingo, por la fecha del vídeo). El equipo se saca del título, que se
pone a mano con el nombre del equipo de la intranet: "ALEVIN A F11 - RIVAL". Los datos salen del Informe del Partido
y de Analytics de Veo (veo.resumen_partido) y la IA añade los puntos clave. Si el martes a las 20:00 un vídeo aún
no está procesado, se envía en cuanto lo esté (se mira cada hora) hasta el viernes. Si un título no encaja con
ningún equipo, se avisa a Rubén Pozón. Estado en datos_privados/informe_veo.json.
"""
import os
import json
import unicodedata
from datetime import datetime, date, timedelta
from flask import Blueprint, request, jsonify

informe_veo_bp = Blueprint('informe_veo', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'datos_privados', 'informe_veo.json')
HORA_ENVIO = (20, 0)
_DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']

# Datos clave (claves del informe de Veo) en el orden en que salen en el mensaje
_CLAVE = ['possession', 'goals', 'total_attempts', 'total_shots', 'shots_inside_box', 'passes', 'successful_passes',
          'pass_accuracy', 'final_third_passes', 'duels', 'recoveries', 'interceptions', 'tackles', 'corners', 'fouls',
          'distance_total_meters', 'sprints_total', 'high_intensity_runs_total', 'top_speed_kmh']
# Destacados por jugador: (clave, texto, unidad)
_DESTACADOS = [('distance_total_meters', 'Más distancia', ' m'), ('top_speed_kmh', 'Más rápido', ' km/h'),
               ('sprints_total', 'Más sprints', ''), ('football_passes_completed_total', 'Más pases completados', ''),
               ('football_duel_total', 'Más duelos', ''), ('football_interception_total', 'Más intercepciones', ''),
               ('football_dribble_total', 'Más regates', ''), ('football_attempts_total', 'Más remates', '')]


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.replace('"', ' ').upper().split())


def _bonito(eq):
    return (' '.join(eq.upper().split()).replace('PREBENJAMIN', 'PREBENJAMÍN')
            .replace('BENJAMIN', 'BENJAMÍN').replace('ALEVIN', 'ALEVÍN'))


def _leer():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault('activo', False)  # se activa tras la prueba
    d.setdefault('enviados', {})   # veo_id → {equipo, en}
    d.setdefault('avisados', {})   # veo_id de títulos que no encajan (ya avisado)
    return d


def _escribir(d):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def _equipos_config():
    from deportivo import _get_equipos_config_path
    try:
        with open(_get_equipos_config_path(), encoding='utf-8') as f:
            return [e['nombre'] for e in json.load(f).get('equipos', []) if e.get('nombre')]
    except Exception:
        return []


def equipo_del_titulo(titulo, equipos=None):
    """'ALEVIN A F11 - ADARVE B' → 'ALEVIN A F11' (nombre del equipo en la intranet), o None si no encaja."""
    equipos = equipos if equipos is not None else _equipos_config()
    parte = _norm(str(titulo or '').split(' - ')[0])
    return next((e for e in equipos if _norm(e) == parte), None)


def finde_de_referencia(d):
    """Viernes del último fin de semana ya jugado (de martes a viernes, el anterior)."""
    return d - timedelta(days=(d.weekday() - 4) % 7 or 7)


def videos_finde(vie):
    """[(grabación, equipo o None)] de los vídeos de Veo de viernes a domingo."""
    import veo
    dias = {(vie + timedelta(days=i)).isoformat() for i in range(3)}
    equipos = _equipos_config()
    return [(g, equipo_del_titulo(g['titulo'], equipos)) for g in veo.grabaciones() if g.get('fecha') in dias]


# ---------- mensaje ----------

def _fmt(v, unidad=''):
    if v is None:
        return '-'
    if isinstance(v, float):
        v = f'{v:.1f}'.replace('.', ',')
    elif isinstance(v, int) and v >= 1000:
        v = f'{v:,}'.replace(',', '.')
    return f'{v}{unidad}'


def _puntos_ia(r, equipo):
    """4-5 puntos clave del partido escritos por la IA a partir de los datos de Veo."""
    import veo
    from analisis_postpartido import _generar_ia
    prompt = (f"Eres analista de fútbol de cantera. Con estos datos objetivos de Veo del partido de {equipo}, escribe "
              "entre 4 y 5 puntos clave para el entrenador, en español, cada uno en una línea que empiece por '• ' y de "
              "como mucho 25 palabras. Di qué funcionó, qué hay que mejorar y en qué momentos o zonas se sufrió o se "
              "dominó. Usa solo los datos que te doy (puedes citar dorsales y cifras), sin inventar nada y sin títulos.\n\n"
              + veo.texto_para_ia(r))
    texto = _generar_ia(prompt)
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    return '\n'.join(l if l.startswith('•') else '• ' + l.lstrip('-* ') for l in lineas[:6])


def texto_informe(r, equipo, nombre_ent='', con_ia=True):
    """Mensaje de WhatsApp con el informe del partido de Veo."""
    gf, gc = r['marcador'][0], r['marcador'][1]
    try:
        f = date.fromisoformat(r.get('fecha') or '')
        fecha = f'{_DIAS[f.weekday()]} {f:%d/%m}'
    except Exception:
        fecha = r.get('fecha') or ''
    rival = str(r.get('titulo', '')).split(' - ', 1)[1].strip() if ' - ' in str(r.get('titulo', '')) else r.get('rival', 'Rival')
    t = [f"Hola {nombre_ent}, informe del partido (Veo):" if nombre_ent else 'Informe del partido (Veo):',
         f"*{_bonito(equipo)} {gf if gf is not None else '-'}-{gc if gc is not None else '-'} {rival}* · {fecha}"]

    goles = sorted(r.get('goleadores') or [], key=lambda g: (g.get('parte') or 0, g.get('minuto') or 0))
    if goles:
        t.append('⚽ ' + ' · '.join(f"{'FR' if g['equipo'] == 'nuestro' else 'Rival'} {g['parte']}ª {g['minuto']}'"
                                    + (f" (dorsal {g['dorsal']})" if g.get('dorsal') else '') for g in goles))

    filas = {f['clave']: f for s in r.get('secciones', []) for f in s['filas']}
    datos = []
    for k in _CLAVE:
        fila = filas.get(k)
        if fila and (fila['nuestro'] is not None or fila['rival'] is not None):
            u = '%' if '(%)' in fila['etiqueta'] else ''
            datos.append(f"• {fila['etiqueta'].replace(' (%)', '')}: {_fmt(fila['nuestro'], u)} - {_fmt(fila['rival'], u)}")
    if datos:
        t += ['', '📊 *Datos clave* (FR - Rival)'] + datos

    jug = [j for j in r.get('jugadores') or [] if (j.get('seconds_played_total') or 0) > 0]
    dest = []
    for clave, texto, unidad in _DESTACADOS:
        con_valor = [j for j in jug if j.get(clave)]
        if con_valor:
            mejor = max(con_valor, key=lambda j: j[clave])
            dest.append(f"• {texto}: dorsal {mejor['dorsal']} ({_fmt(mejor[clave], unidad)})")
    if dest:
        t += ['', '🏃 *Destacados*'] + dest

    if con_ia:
        try:
            puntos = _puntos_ia(r, equipo)
            if puntos:
                t += ['', '🧠 *Puntos clave*', puntos]
        except Exception as e:
            print(f"[INFORME VEO] IA: {e}", flush=True)
    if r.get('url'):
        t += ['', f"🎥 {r['url']}"]
    return '\n'.join(t) + '\n\nDirección Deportiva'


def _listo(r):
    return bool(r and r.get('secciones') and r['marcador'][0] is not None)


# ---------- envío ----------

def _telefono_ruben():
    from app import client, NOMBRE_EXCEL
    p = client.open(NOMBRE_EXCEL).worksheet('PERFILES').get_all_values()
    h = [x.strip().upper() for x in p[0]]
    i_u, i_t = h.index('USUARIO'), h.index('TELEFONO')
    return next((r[i_t].strip() for r in p[1:] if len(r) > max(i_u, i_t) and _norm(r[i_u]) == 'RUBEN POZON'), '')


def procesar_finde(vie, solo_a=None, solo_equipo=None):
    """Envía los informes de los vídeos del finde que estén listos y no se hayan enviado.
    solo_a=(usuario, telefono) → prueba: se envía solo a esa persona (y no cuenta como enviado)."""
    import veo
    from formulario_finde import _entrenadores
    from app import enviar_whatsapp
    d = _leer()
    resultado = []
    for g, eq in videos_finde(vie):
        if solo_equipo and _norm(eq or '') != _norm(solo_equipo):
            continue
        if not eq:
            if not solo_a and g['id'] not in d['avisados']:
                tel = _telefono_ruben()
                if tel:
                    enviar_whatsapp(tel, f"⚠️ Informe Veo: el vídeo «{g['titulo']}» ({g['fecha']}) no tiene un título que encaje "
                                         f"con ningún equipo de la intranet. Ponle el nombre del equipo, por ejemplo "
                                         f"'INFANTIL A - RIVAL', para que se envíe el informe.\n{g['url']}")
                d['avisados'][g['id']] = datetime.now().strftime('%Y-%m-%dT%H:%M')
                _escribir(d)
            resultado.append({'titulo': g['titulo'], 'estado': 'título sin equipo'})
            continue
        if not solo_a and g['id'] in d['enviados']:
            continue
        try:
            r = veo.resumen_partido(g['id'])
        except Exception as e:
            resultado.append({'titulo': g['titulo'], 'estado': f'aún no disponible ({str(e)[:60]})'})
            continue
        if not _listo(r):
            resultado.append({'titulo': g['titulo'], 'estado': 'Veo aún no lo ha procesado'})
            continue
        destinos = [solo_a] if solo_a else [(u, t) for u, t, _ in _entrenadores([eq]) if t]
        base = texto_informe(r, eq, '', con_ia=True)  # la IA se llama una vez por vídeo
        ok = 0
        for usuario, tel in destinos:
            texto = base.replace('Informe del partido (Veo):', f"Hola {usuario.split()[0].capitalize()}, informe del partido (Veo):", 1)
            bien, _ = enviar_whatsapp(tel, texto)
            ok += bool(bien)
        if not solo_a:
            d = _leer()
            d['enviados'][g['id']] = {'equipo': eq, 'titulo': g['titulo'], 'en': datetime.now().strftime('%Y-%m-%dT%H:%M'), 'a': ok}
            _escribir(d)
        resultado.append({'titulo': g['titulo'], 'equipo': eq, 'estado': f'enviado a {ok} de {len(destinos)}'})
        print(f"[INFORME VEO] {g['titulo']} → {eq}: enviado a {ok} de {len(destinos)}", flush=True)
    return resultado


def tick(ahora):
    """Desde el martes a las 20:00 y hasta el viernes, cada hora, envía los informes que ya estén listos."""
    d = _leer()
    if not d.get('activo') or ahora.minute != 0 or ahora.weekday() not in (1, 2, 3, 4):
        return
    if ahora.weekday() == 1 and (ahora.hour, ahora.minute) < HORA_ENVIO:
        return
    if not 8 <= ahora.hour <= 22 and ahora.weekday() != 1:
        return  # de noche no se envía
    try:
        procesar_finde(finde_de_referencia(ahora.date()))
    except Exception as e:
        print(f"[INFORME VEO] {e}", flush=True)


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@informe_veo_bp.route('/api/whatsapp/informe_veo', methods=['GET', 'POST'])
def api_cfg():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    d = _leer()
    if request.method == 'POST':
        d['activo'] = bool((request.json or {}).get('activo'))
        _escribir(d)
    vie = finde_de_referencia(date.today())
    return jsonify({'status': 'ok', 'activo': d['activo'], 'viernes': vie.isoformat(),
                    'enviados': list(d['enviados'].values())[-30:]})


@informe_veo_bp.route('/api/whatsapp/informe_veo/videos')
def api_videos():
    """Vídeos del último finde y a qué equipo corresponden (no envía nada)."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        vie = finde_de_referencia(date.today())
        d = _leer()
        return jsonify({'status': 'ok', 'viernes': vie.isoformat(), 'videos': [
            {'titulo': g['titulo'], 'fecha': g['fecha'], 'equipo': eq, 'enviado': g['id'] in d['enviados'], 'url': g['url']}
            for g, eq in videos_finde(vie)]})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error con Veo: {e}'[:250]})


@informe_veo_bp.route('/api/whatsapp/informe_veo/prueba', methods=['POST'])
def api_prueba():
    """Envía a una persona el informe de un equipo del último finde (prueba: no cuenta como enviado)."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    data = request.json or {}
    equipo = (data.get('equipo') or 'INFANTIL A').strip()
    tel, usuario = _telefono_ruben(), 'RUBEN POZON'  # las pruebas van siempre a Rubén Pozón
    if not tel:
        return jsonify({'status': 'error', 'message': 'RUBEN POZON no tiene teléfono en su usuario'})
    try:
        res = procesar_finde(finde_de_referencia(date.today()), solo_a=(usuario or 'Prueba', tel), solo_equipo=equipo)
        if not res:
            return jsonify({'status': 'error', 'message': f'No hay vídeo de {equipo} en el último fin de semana'})
        return jsonify({'status': 'ok', 'message': ' · '.join(f"{x['titulo']}: {x['estado']}" for x in res)})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error: {e}'[:250]})
