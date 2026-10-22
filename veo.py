"""
CONEXIÓN CON VEO (cámaras de grabación de partidos)
- Inicia sesión en Veo con VEO_EMAIL / VEO_PASSWORD (variables del .env del servidor) usando el mismo
  login OpenID que la web de Veo, y usa su API interna con el token (dura ~1 h; se renueva solo).
- Trae las grabaciones de la temporada, el Informe del partido (estadísticas de equipo) y las
  estadísticas por jugador, y los guarda en caché en disco (.veo_cache/).
- Cruza cada análisis postpartido con su grabación por fecha + categoría + letra + rival.
Solo LEE datos de Veo; no cambia nada en la cuenta.
"""
import os
import re
import json
import time
import base64
import hashlib
import secrets
import threading
import unicodedata
from datetime import date
from urllib.parse import urlparse, parse_qs, urljoin

import requests
from flask import Blueprint, jsonify, request, session

veo_bp = Blueprint('veo_bp', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, '.veo_cache')
TOKEN_FILE = os.path.join(CACHE_DIR, 'token.json')
CLUB = 'club-fuentelarreyna'
CLIENT_ID = 'IzRQtXQ07V7n8uBtpTHzi'
REDIRECT = 'https://app.veo.co/signin-redirect/'
API = 'https://app.veo.co/api/app'
TTL_LISTA = 15 * 60          # la lista de grabaciones se refresca cada 15 min
TTL_INFORME_FRESCO = 6 * 3600  # informes de partidos recientes (<3 días) se refrescan cada 6 h (Veo sigue procesando)
_lock = threading.Lock()


# ───────────────────────── sesión y API ─────────────────────────

def configurado():
    return bool(os.environ.get('VEO_EMAIL') and os.environ.get('VEO_PASSWORD'))


def _login():
    """Login OpenID (código + PKCE), igual que la web de Veo. Devuelve (access_token, expira_ts)."""
    s = requests.Session()
    s.headers['User-Agent'] = 'Mozilla/5.0 (IntranetFR)'
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    r = s.get('https://auth.veo.co/oidc/auth', params={
        'client_id': CLIENT_ID, 'redirect_uri': REDIRECT, 'response_type': 'code',
        'scope': 'openid email phone address profile', 'state': secrets.token_hex(16),
        'code_challenge': challenge, 'code_challenge_method': 'S256'}, allow_redirects=False, timeout=20)
    m = re.search(r'/interaction/([^/?#]+)', r.headers.get('location', ''))
    if not m:
        raise RuntimeError('Veo: no se pudo iniciar el login')
    r = s.post(f'https://auth.veo.co/interaction/{m.group(1)}/login', allow_redirects=False, timeout=20,
               data={'clientId': CLIENT_ID, 'username': os.environ['VEO_EMAIL'], 'password': os.environ['VEO_PASSWORD']})
    url = urljoin('https://auth.veo.co', r.headers.get('location', ''))
    for _ in range(6):
        if url.startswith(REDIRECT):
            break
        r = s.get(url, allow_redirects=False, timeout=20)
        url = urljoin('https://auth.veo.co', r.headers.get('location', ''))
    code = parse_qs(urlparse(url).query).get('code', [None])[0]
    if not code:
        raise RuntimeError('Veo: usuario o contraseña incorrectos')
    r = s.post('https://auth.veo.co/oidc/token', timeout=20, data={
        'grant_type': 'authorization_code', 'redirect_uri': REDIRECT, 'code': code,
        'code_verifier': verifier, 'client_id': CLIENT_ID})
    r.raise_for_status()
    d = r.json()
    return d['access_token'], time.time() + int(d.get('expires_in', 3600)) - 120


def _token(forzar=False):
    with _lock:
        if not forzar and os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE) as f:
                    t = json.load(f)
                if t.get('exp', 0) > time.time():
                    return t['tok']
            except Exception:
                pass
        tok, exp = _login()
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(TOKEN_FILE, 'w') as f:
            json.dump({'tok': tok, 'exp': exp}, f)
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except Exception:
            pass
        return tok


def _api(method, path, **kw):
    """Llamada a la API de Veo; si el token ha caducado, vuelve a iniciar sesión una vez."""
    for intento in (0, 1):
        h = {'Authorization': 'Bearer ' + _token(forzar=intento == 1), 'Accept': 'application/json'}
        r = requests.request(method, path if path.startswith('http') else API + path, headers=h, timeout=30, **kw)
        if r.status_code in (401, 403) and intento == 0:
            continue
        r.raise_for_status()
        return r.json()


def _cache(nombre, ttl, fn):
    """Guarda en disco el resultado de fn() durante ttl segundos (ttl=None: para siempre)."""
    path = os.path.join(CACHE_DIR, nombre)
    if os.path.exists(path) and (ttl is None or time.time() - os.path.getmtime(path) < ttl):
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    datos = fn()
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(datos, f, ensure_ascii=False)
    return datos


def _inicio_temporada():
    """La temporada empieza el 1 de agosto."""
    hoy = date.today()
    return f"{hoy.year - 1 if hoy.month < 8 else hoy.year}-08-01"


def grabaciones():
    """Grabaciones del club de la temporada activa (más recientes primero)."""
    desde = _inicio_temporada()

    def traer():
        todas, page = [], 1
        while page < 30:
            items = _api('GET', f'/clubs/{CLUB}/recordings/', params={'filter': 'own', 'page': page})
            if not items:
                break
            todas += items
            if items[-1].get('start', '') < desde:
                break
            page += 1
        return [{
            'id': x['identifier'], 'slug': x.get('slug'), 'titulo': x.get('title') or '',
            'inicio': x.get('start') or '', 'fecha': (x.get('start') or '')[:10], 'equipo_veo': x.get('team') or '',
            'duracion_min': round((x.get('duration') or 0) / 60), 'miniatura': x.get('thumbnail'),
            'url': 'https://app.veo.co' + (x.get('url') or ''), 'tipo': ((x.get('type') or {}).get('value') or ''),
        } for x in todas if (x.get('start') or '') >= desde]
    return _cache('grabaciones.json', TTL_LISTA, traer)


def _reciente(g):
    try:
        return (date.today() - date.fromisoformat(g['fecha'])).days < 3
    except Exception:
        return False


def informe(veo_id):
    g = next((x for x in grabaciones() if x['id'] == veo_id), None)
    ttl = TTL_INFORME_FRESCO if (g and _reciente(g)) else None
    return _cache(f'informe_{veo_id}.json', ttl, lambda: _api('GET', f'/analysis/matches/{veo_id}/match-report/'))


def stats_jugadores(veo_id, team_id):
    g = next((x for x in grabaciones() if x['id'] == veo_id), None)
    ttl = TTL_INFORME_FRESCO if (g and _reciente(g)) else None
    return _cache(f'stats_{veo_id}.json', ttl, lambda: _api(
        'POST', '/analysis/stats/', json={'type': 'match_all_players', 'match_id': veo_id, 'team_id': team_id}))


# ───────────────────────── cruce análisis ↔ grabación ─────────────────────────

def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '').upper())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return ' '.join(re.sub(r'[^A-Z0-9 ]', ' ', s).split())


_CATS = [  # (categoría, prefijos con los que aparece en títulos/equipos de Veo)
    ('PREBENJAMIN', ('PREBENJ', 'PREBEN', 'PRE')), ('BENJAMIN', ('BENJ', 'BEN')), ('ALEVIN', ('ALEV', 'ALE')),
    ('INFANTIL', ('INFANT', 'INF')), ('CADETE', ('CADET', 'CAD')), ('JUVENIL', ('JUVEN', 'JUV')),
    ('AFICIONADO', ('AFICION', 'AFI', 'AMATEUR')), ('DEBUTANTE', ('DEBUT', 'CHUPET')),
]
_GENERICAS = {'CF', 'CD', 'AD', 'SAD', 'UD', 'CDE', 'EF', 'ESCUELA', 'FUTBOL', 'CLUB', 'DE', 'DEL', 'LA', 'EL', 'VS',
              'PARTIDO', 'C', 'F', 'D', 'S', 'U', 'A', 'B', 'E', 'FUNDACION', 'ATLETICO', 'DEPORTIVO', 'REAL'}


def _categoria(texto):
    for tok in _norm(texto).split():
        for cat, prefs in _CATS:
            if any(tok.startswith(p) for p in prefs):
                return cat
    return None


def _cat_y_letra_titulo(titulo):
    """'INFANTIL B - POZUELO C' → ('INFANTIL', 'B'); 'CAD A - X' → ('CADETE', 'A'). Solo mira antes del guion/vs."""
    t = _norm(re.split(r'\s+-\s+|\s+VS\.?\s+|\s+vs\.?\s+', titulo or '', maxsplit=1)[0])
    toks = t.split()
    cat = _categoria(t)
    letra = next((x for x in toks[1:] if len(x) == 1 and x in 'ABCDEF'), None)
    return cat, letra


def _rival_titulo(titulo):
    partes = re.split(r'\s+-\s+|\s+VS\.?\s+|\s+vs\.?\s+', titulo or '', maxsplit=1)
    return partes[1] if len(partes) > 1 else ''


def _palabras(s):
    return {w for w in _norm(s).split() if len(w) > 2 and w not in _GENERICAS}


def candidatos(equipo, fecha, rival=''):
    """Grabaciones posibles para un análisis (equipo de la intranet + fecha YYYY-MM-DD), ordenadas por puntuación."""
    cat_eq = _categoria(equipo)
    toks_eq = _norm(equipo).split()
    letra_eq = next((x for x in toks_eq[1:] if len(x) == 1 and x in 'ABCDEF'), None)
    try:
        f0 = date.fromisoformat(fecha)
    except Exception:
        return []
    out = []
    for g in grabaciones():
        try:
            dias = abs((date.fromisoformat(g['fecha']) - f0).days)
        except Exception:
            continue
        if dias > 2:
            continue
        cat_t, letra_t = _cat_y_letra_titulo(g['titulo'])
        cat_g = cat_t or _categoria(g['equipo_veo'])
        puntos = {0: 4, 1: 2, 2: 0}[dias]
        motivos = [f"fecha {'igual' if dias == 0 else f'±{dias} día(s)'}"]
        if cat_g and cat_eq:
            if cat_g != cat_eq:
                continue
            puntos += 3; motivos.append('categoría')
        if letra_t and letra_eq:
            if letra_t != letra_eq:
                continue
            puntos += 3; motivos.append('letra')
        if rival and _palabras(rival) & _palabras(_rival_titulo(g['titulo'])):
            puntos += 2; motivos.append('rival')
        out.append(dict(g, puntos=puntos, motivos=motivos))
    out.sort(key=lambda x: (-x['puntos'], x['fecha']))
    return out


def sugerencia(equipo, fecha, rival=''):
    """La grabación que se elige sola: solo si está clara (puntuación alta y sin empate)."""
    c = candidatos(equipo, fecha, rival)
    if not c or c[0]['puntos'] < 7:
        return None
    if len(c) > 1 and c[1]['puntos'] == c[0]['puntos']:
        return None
    return c[0]


# ───────────────────────── datos del partido (legibles) ─────────────────────────

_ETIQUETAS = {
    'goals': 'Goles', 'total_attempts': 'Remates', 'total_shots': 'Remates', 'corners': 'Córners', 'fouls': 'Faltas',
    'free_kicks': 'Libres directos/indirectos', 'possession': 'Posesión (%)', 'successful_passes': 'Pases completados',
    'passes': 'Pases', 'pass_accuracy': 'Acierto en el pase (%)', 'assists': 'Asistencias', 'dribbles': 'Regates',
    'shots_inside_box': 'Remates dentro del área', 'shots_outside_box': 'Remates fuera del área',
    'goals_inside_box': 'Goles dentro del área', 'goals_outside_box': 'Goles fuera del área',
    'conversion_rate_overall': 'Conversión total (%)', 'conversion_rate_inside_box': 'Conversión dentro del área (%)',
    'conversion_rate_outside_box': 'Conversión fuera del área (%)', 'duels': 'Duelos', 'tackles': 'Entradas',
    'recoveries': 'Recuperaciones', 'interceptions': 'Intercepciones', 'distance_total_meters': 'Distancia total (m)',
    'high_intensity_runs_total': 'Carreras alta intensidad', 'sprints_total': 'Sprints', 'top_speed_kmh': 'Velocidad máx. (km/h)',
    'forward_passes': 'Pases hacia delante', 'sideways_passes': 'Pases laterales', 'backward_passes': 'Pases hacia atrás',
    'successful_forward_passes': 'Pases adelante completados', 'successful_sideways_passes': 'Pases laterales completados',
    'successful_backward_passes': 'Pases atrás completados', 'forward_accuracy': 'Acierto pase adelante (%)',
    'sideways_accuracy': 'Acierto pase lateral (%)', 'backward_accuracy': 'Acierto pase atrás (%)',
    'final_third_passes': 'Pases al último tercio', 'successful_final_third_passes': 'Pases al último tercio completados',
    'final_third_accuracy': 'Acierto último tercio (%)', 'pass_string_average_length': 'Pases por posesión (media)',
}
_SECCIONES = [('overview', 'Resumen'), ('attacking', 'Ataque'), ('defensive', 'Defensa'),
              ('passing', 'Pase'), ('physical', 'Físico')]
_JUG = [  # (tipo Veo, etiqueta, decimales)
    ('seconds_played_total', 'Min', 'min'), ('distance_total_meters', 'Distancia (m)', 0), ('top_speed_kmh', 'Vel. máx.', 1),
    ('average_speed_kmh', 'Vel. media', 1), ('sprints_total', 'Sprints', 0), ('high_intensity_runs_total', 'Alta int.', 0),
    ('football_goal_total', 'Goles', 0), ('football_attempts_total', 'Remates', 0), ('football_passes_total', 'Pases', 0),
    ('football_passes_completed_total', 'Pases OK', 0), ('football_dribble_total', 'Regates', 0), ('football_duel_total', 'Duelos', 0),
    ('football_tackle_total', 'Entradas', 0), ('football_interception_total', 'Intercep.', 0), ('football_loose_total', 'Pérdidas', 0),
    ('football_foul_total', 'Faltas', 0), ('football_throw_in_total', 'S. banda', 0), ('football_corner_total', 'Córners', 0),
    ('football_free_kick_total', 'Libres', 0), ('football_goal_kick_total', 'S. puerta', 0), ('football_save_total', 'Paradas', 0),
]


def _num(v, dec=1):
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return int(round(v)) if dec == 0 or v == int(v) else round(v, dec)


def resumen_partido(veo_id):
    """Informe + estadísticas por jugador, ya ordenados y con etiquetas en español."""
    g = next((x for x in grabaciones() if x['id'] == veo_id), None)
    rep = informe(veo_id)
    equipos = rep.get('teams') or {}
    lado = equipos.get('own_side') or 'home'
    nuestro = equipos.get(lado) or {}
    rival = equipos.get('away' if lado == 'home' else 'home') or {}
    marcador = ((rep.get('score') or {}).get('full_time')) or {}

    secciones = []
    for clave, titulo in _SECCIONES:
        bloque = rep.get(clave) or {}
        if clave == 'overview':
            bloque = bloque.get('key_stats') or {}
        filas = []
        for k, v in bloque.items():
            if not isinstance(v, dict) or not isinstance(v.get('full_time'), dict):
                continue
            ft = v['full_time']
            if 'own' not in ft:
                continue
            partes = [((p.get('values') or p).get('own') if isinstance(p, dict) else None,
                       (p.get('values') or p).get('opponent') if isinstance(p, dict) else None) for p in (v.get('periods') or [])]
            filas.append({'clave': k, 'etiqueta': _ETIQUETAS.get(k, k.replace('_', ' ').capitalize()),
                          'nuestro': _num(ft.get('own')), 'rival': _num(ft.get('opponent')),
                          'partes': [[_num(a), _num(b)] for a, b in partes]})
        if filas:
            secciones.append({'titulo': titulo, 'filas': filas})

    jugadores = []
    try:
        st = stats_jugadores(veo_id, nuestro.get('team_id')) if nuestro.get('team_id') else {'items': []}
        for it in st.get('items', []):
            vals = {s['type']: s.get('value') for s in it.get('stats', []) if isinstance(s.get('type'), str)}
            fila = {'dorsal': (it.get('player') or {}).get('jersey_number') or '?'}
            for tipo, _, dec in _JUG:
                v = vals.get(tipo)
                fila[tipo] = round((v or 0) / 60) if dec == 'min' else _num(v, dec)
            jugadores.append(fila)
        jugadores.sort(key=lambda j: (-(j.get('seconds_played_total') or 0), int(j['dorsal']) if str(j['dorsal']).isdigit() else 99))
    except Exception as e:
        print(f"Veo stats jugadores {veo_id}: {e}")

    goleadores = []
    for lado_g, quien in ((lado, 'nuestro'), ('away' if lado == 'home' else 'home', 'rival')):
        for s in ((rep.get('scorers') or {}).get(lado_g) or []):
            minuto = round((s.get('period_time_ms') or 0) / 60000)
            goleadores.append({'equipo': quien, 'dorsal': s.get('player_jersey'), 'parte': s.get('period_number'), 'minuto': minuto})

    return {
        'id': veo_id, 'titulo': (g or {}).get('titulo') or (rep.get('match') or {}).get('title', ''),
        'fecha': (g or {}).get('fecha'), 'url': (g or {}).get('url') or f"https://app.veo.co/matches/{(rep.get('match') or {}).get('slug', '')}/",
        'miniatura': (g or {}).get('miniatura'),
        'nuestro': nuestro.get('name') or 'Nosotros', 'rival': rival.get('name') or 'Rival',
        'marcador': [marcador.get(lado), marcador.get('away' if lado == 'home' else 'home')],
        'goleadores': goleadores, 'secciones': secciones,
        'jugadores': jugadores, 'columnas_jugador': [[t, e] for t, e, _ in _JUG],
    }


def texto_para_ia(r):
    """Todas las estadísticas de Veo en texto compacto para el informe de la IA."""
    if not r:
        return ''
    lin = [f"## Datos objetivos de Veo ({r['nuestro']} vs {r['rival']}, marcador {r['marcador'][0]}-{r['marcador'][1]})",
           "Formato: nuestro equipo - rival (1ª parte / 2ª parte)"]
    for s in r['secciones']:
        lin.append(f"### {s['titulo']}")
        for f in s['filas']:
            partes = ' / '.join(f"{a}-{b}" for a, b in f['partes'] if a is not None or b is not None)
            lin.append(f"- {f['etiqueta']}: {f['nuestro']} - {f['rival']}" + (f" ({partes})" if partes else ''))
    if r['goleadores']:
        lin.append("### Goles (minuto de cada parte)")
        lin += [f"- {'Nuestro' if g['equipo'] == 'nuestro' else 'Rival'}: {g['parte']}ª parte min {g['minuto']}"
                + (f", dorsal {g['dorsal']}" if g['dorsal'] else '') for g in r['goleadores']]
    if r['jugadores']:
        lin.append("### Estadísticas por jugador (dorsal)")
        for j in r['jugadores']:
            datos = [f"{e} {j.get(t)}" for t, e in r['columnas_jugador'] if j.get(t)]
            lin.append(f"- Dorsal {j['dorsal']}: " + ', '.join(datos))
    return '\n'.join(lin)


# ───────────────────────────── rutas ─────────────────────────────

def _error(e):
    print(f"Error Veo: {e}")
    return jsonify({"status": "error", "message": f"No se ha podido conectar con Veo: {e}"}), 500


@veo_bp.route('/api/veo/candidatos')
def api_candidatos():
    if not session.get('usuario'):
        return jsonify({"status": "error"}), 401
    if not configurado():
        return jsonify({"status": "sin_configurar", "candidatos": []})
    try:
        c = candidatos(request.args.get('equipo', ''), request.args.get('fecha', ''), request.args.get('rival', ''))
        sug = sugerencia(request.args.get('equipo', ''), request.args.get('fecha', ''), request.args.get('rival', ''))
        return jsonify({"status": "ok", "candidatos": c, "sugerido": sug['id'] if sug else None})
    except Exception as e:
        return _error(e)


@veo_bp.route('/api/veo/grabaciones')
def api_grabaciones():
    """Grabaciones de la temporada alrededor de una fecha (para elegir a mano)."""
    if not session.get('usuario'):
        return jsonify({"status": "error"}), 401
    if not configurado():
        return jsonify({"status": "sin_configurar", "grabaciones": []})
    try:
        gs = grabaciones()
        fecha = request.args.get('fecha')
        if fecha:
            try:
                f0 = date.fromisoformat(fecha)
                gs = [g for g in gs if abs((date.fromisoformat(g['fecha']) - f0).days) <= 7]
            except Exception:
                pass
        return jsonify({"status": "ok", "grabaciones": gs})
    except Exception as e:
        return _error(e)


@veo_bp.route('/api/veo/partido/<veo_id>')
def api_partido(veo_id):
    if not session.get('usuario'):
        return jsonify({"status": "error"}), 401
    if not configurado():
        return jsonify({"status": "sin_configurar"})
    if not re.fullmatch(r'[0-9a-f-]{36}', veo_id or ''):
        return jsonify({"status": "error", "message": "Id no válido"}), 400
    try:
        return jsonify({"status": "ok", "partido": resumen_partido(veo_id)})
    except Exception as e:
        return _error(e)
