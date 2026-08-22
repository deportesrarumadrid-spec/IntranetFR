import os
import json
import threading
from datetime import datetime, timedelta

from deportivo import limpiar_texto_robusto as _norm

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DISCREPANCIAS_REVISADAS_FILE = os.path.join(BASE_DIR, 'static', 'data', 'discrepancias_jugadores_revisadas.json')
def _get_conv_report_file():
    try:
        from competicion_scraper import _conv_report_file
        return _conv_report_file()
    except Exception:
        return os.path.join(BASE_DIR, 'static', 'data', 'convocatorias_report.json')

DISCREPANCIAS_MIN_INTERVAL_MINUTES = 60

LISTADOS_INFO = {
    'DATOS JUGADORES': {'label': 'Ficha ampliada', 'accion_confirmar': True},
    'GPS_POSICIONES':  {'label': 'GPS',             'accion_confirmar': True},
    'PAGOS JUGADORES': {'label': 'Pagos',            'accion_confirmar': True},
    'COORDINACION':    {'label': 'Coordinación',     'accion_confirmar': True},
    'ASISTENCIAS':     {'label': 'Asistencias',      'accion_confirmar': False},
    'CONVOCATORIAS':   {'label': 'Convocatorias RFFM', 'accion_confirmar': False},
}

_discrepancias_state = {'running': False, 'error': None, 'started_at': None, 'finished_at': None, 'resultado': []}
_discrepancias_lock = threading.Lock()


def _token_set(nombre):
    n = _norm(nombre).replace(',', ' ')
    return frozenset(t for t in n.split() if t)


def _clave_tokens(tokens):
    return ' '.join(sorted(tokens))


def _parse_fecha_simple(d_str):
    if not d_str:
        return None
    s = str(d_str).strip().replace('-', '/')
    for fmt in ("%d/%m/%Y", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Lectura de listados
# ---------------------------------------------------------------------------

def cargar_roster_jugadores(client, nombre_excel):
    """Devuelve {equipo_norm: {'equipo': nombre_real, 'jugadores': [{'nombre','tokens'}]}} solo activos."""
    roster = {}
    sheet = client.open(nombre_excel).worksheet("JUGADORES")
    all_jug = sheet.get_all_values()
    if not all_jug:
        return roster
    headers = [h.strip().upper() for h in all_jug[0]]
    idx_n = headers.index("NOMBRE") if "NOMBRE" in headers else 0
    idx_b = next((headers.index(c) for c in ("BAJADESDE", "BAJA", "BAJA_DESDE") if c in headers), -1)
    idx_al = next((headers.index(c) for c in ("ALTADESDE", "ALTA", "ALTA_DESDE") if c in headers), -1)
    idx_e = next((headers.index(c) for c in ("EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS") if c in headers), -1)
    if idx_e == -1:
        return roster

    hoy = datetime.now()
    for row in all_jug[1:]:
        if len(row) <= max(idx_n, idx_e):
            continue
        nombre = row[idx_n].strip()
        equipo = row[idx_e].strip()
        if not nombre or not equipo:
            continue
        baja_dt = _parse_fecha_simple(row[idx_b].strip()) if idx_b != -1 and len(row) > idx_b else None
        alta_dt = _parse_fecha_simple(row[idx_al].strip()) if idx_al != -1 and len(row) > idx_al else None
        if baja_dt and hoy >= baja_dt and (not alta_dt or hoy < alta_dt):
            continue
        eq_norm = _norm(equipo)
        bucket = roster.setdefault(eq_norm, {'equipo': equipo, 'jugadores': []})
        bucket['jugadores'].append({"nombre": nombre, "tokens": _token_set(nombre)})
    return roster


def _cargar_listado_por_equipo(client, nombre_excel, hoja, col_nombre, col_equipo, col_nombre2=None):
    """Devuelve {equipo_norm: set(tokens)} leyendo columnas exactas de una hoja."""
    try:
        sheet = client.open(nombre_excel).worksheet(hoja)
    except Exception:
        return None
    all_v = sheet.get_all_values()
    if not all_v:
        return {}
    headers = [h.strip().upper() for h in all_v[0]]
    if col_nombre not in headers or col_equipo not in headers:
        return {}
    idx_n = headers.index(col_nombre)
    idx_e = headers.index(col_equipo)
    idx_n2 = headers.index(col_nombre2) if (col_nombre2 and col_nombre2 in headers) else -1
    out = {}
    for row in all_v[1:]:
        if len(row) <= max(idx_n, idx_e):
            continue
        equipo = row[idx_e].strip()
        nombre = row[idx_n].strip()
        if idx_n2 != -1 and len(row) > idx_n2 and row[idx_n2].strip():
            nombre = (nombre + ' ' + row[idx_n2].strip()).strip()
        if not equipo or not nombre:
            continue
        out.setdefault(_norm(equipo), set()).add(_token_set(nombre))
    return out


def cargar_nombres_gps(client, nombre_excel):
    return _cargar_listado_por_equipo(client, nombre_excel, "GPS_POSICIONES", "JUGADOR", "EQUIPO")


def cargar_nombres_pagos(client, nombre_excel):
    return _cargar_listado_por_equipo(client, nombre_excel, "PAGOS JUGADORES", "NOMBRE", "EQUIPO")


def cargar_nombres_asistencias(client, nombre_excel):
    return _cargar_listado_por_equipo(client, nombre_excel, "ASISTENCIAS", "NOMBRE", "EQUIPO", col_nombre2="APELLIDO")


def cargar_tokens_globales(client, nombre_excel, hoja, col_nombre):
    """Para hojas sin EQUIPO fiable (DATOS JUGADORES, COORDINACION): set(tokens) global, sin filtrar por equipo."""
    try:
        sheet = client.open(nombre_excel).worksheet(hoja)
    except Exception:
        return None
    all_v = sheet.get_all_values()
    if not all_v:
        return set()
    headers = [h.strip().upper() for h in all_v[0]]
    if col_nombre not in headers:
        return set()
    idx_n = headers.index(col_nombre)
    out = set()
    for row in all_v[1:]:
        if len(row) <= idx_n:
            continue
        nombre = row[idx_n].strip()
        if nombre:
            out.add(_token_set(nombre))
    return out


def cargar_tokens_convocatorias():
    """{equipo_norm: set(tokens)} de jugadores convocados, leído del reporte ya scrapeado de la RFFM."""
    _rpt = _get_conv_report_file()
    if not os.path.exists(_rpt):
        return None
    try:
        with open(_rpt, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return None
    out = {}
    for entry in data.get('entries', []):
        equipo = (str(entry.get('categoria', '')).strip() + ' ' + str(entry.get('letra', '')).strip()).strip()
        if not equipo:
            continue
        eq_norm = _norm(equipo)
        for c in entry.get('convocados', []):
            nombre = str(c.get('nombre', '')).strip()
            if nombre:
                out.setdefault(eq_norm, set()).add(_token_set(nombre))
    return out


# ---------------------------------------------------------------------------
# Persistencia de decisiones (confirmar / declinar)
# ---------------------------------------------------------------------------

def _cargar_revisadas():
    if not os.path.exists(DISCREPANCIAS_REVISADAS_FILE):
        return {}
    try:
        with open(DISCREPANCIAS_REVISADAS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _guardar_revisadas(data):
    os.makedirs(os.path.dirname(DISCREPANCIAS_REVISADAS_FILE), exist_ok=True)
    with open(DISCREPANCIAS_REVISADAS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _clave_revision(equipo_norm, listado, tokens):
    return f"{equipo_norm}|{listado}|{_clave_tokens(tokens)}"


# ---------------------------------------------------------------------------
# Cálculo principal
# ---------------------------------------------------------------------------

def calcular_discrepancias(client, nombre_excel):
    roster = cargar_roster_jugadores(client, nombre_excel)
    revisadas = _cargar_revisadas()

    listados_por_equipo = {
        'GPS_POSICIONES': cargar_nombres_gps(client, nombre_excel),
        'PAGOS JUGADORES': cargar_nombres_pagos(client, nombre_excel),
        'ASISTENCIAS': cargar_nombres_asistencias(client, nombre_excel),
        'CONVOCATORIAS': cargar_tokens_convocatorias(),
    }
    listados_globales = {
        'DATOS JUGADORES': cargar_tokens_globales(client, nombre_excel, "DATOS JUGADORES", "JUGADOR_NOMBRE"),
        'COORDINACION': cargar_tokens_globales(client, nombre_excel, "COORDINACION", "JUGADOR"),
    }

    avisos = []
    for eq_norm, bucket in roster.items():
        equipo_real = bucket['equipo']
        for jugador in bucket['jugadores']:
            for listado, datos_equipo in listados_por_equipo.items():
                if datos_equipo is None:
                    continue  # hoja no disponible, no se puede comprobar
                tokens_equipo = datos_equipo.get(eq_norm, set())
                if jugador['tokens'] in tokens_equipo:
                    continue
                clave = _clave_revision(eq_norm, listado, jugador['tokens'])
                if clave in revisadas:
                    continue
                avisos.append({
                    "equipo": equipo_real,
                    "equipo_norm": eq_norm,
                    "nombre": jugador['nombre'],
                    "listado": listado,
                    "listado_label": LISTADOS_INFO[listado]['label'],
                    "accion_confirmar": LISTADOS_INFO[listado]['accion_confirmar'],
                    "informativo": not LISTADOS_INFO[listado]['accion_confirmar'],
                })
            for listado, tokens_global in listados_globales.items():
                if tokens_global is None:
                    continue
                if jugador['tokens'] in tokens_global:
                    continue
                clave = _clave_revision(eq_norm, listado, jugador['tokens'])
                if clave in revisadas:
                    continue
                avisos.append({
                    "equipo": equipo_real,
                    "equipo_norm": eq_norm,
                    "nombre": jugador['nombre'],
                    "listado": listado,
                    "listado_label": LISTADOS_INFO[listado]['label'],
                    "accion_confirmar": LISTADOS_INFO[listado]['accion_confirmar'],
                    "informativo": not LISTADOS_INFO[listado]['accion_confirmar'],
                })

    avisos.sort(key=lambda a: (a['equipo'], a['nombre'], a['listado']))
    return avisos


# ---------------------------------------------------------------------------
# Acciones del admin: confirmar (añade fila) / declinar (silencia)
# ---------------------------------------------------------------------------

def _append_fila_generica(sheet, campos_valor):
    headers_raw = sheet.row_values(1)
    headers_norm = [h.strip().upper() for h in headers_raw]
    fila = [campos_valor.get(h, '') for h in headers_norm]
    sheet.append_row(fila, value_input_option='USER_ENTERED')


def aplicar_confirmar(client, nombre_excel, equipo, listado, nombre):
    eq_norm = _norm(equipo)
    tokens = _token_set(nombre)

    if listado == 'GPS_POSICIONES':
        sheet = client.open(nombre_excel).worksheet("GPS_POSICIONES")
        _append_fila_generica(sheet, {"EQUIPO": equipo, "JUGADOR": nombre})
    elif listado == 'PAGOS JUGADORES':
        sheet = client.open(nombre_excel).worksheet("PAGOS JUGADORES")
        _append_fila_generica(sheet, {"EQUIPO": equipo, "NOMBRE": nombre})
    elif listado == 'COORDINACION':
        sheet = client.open(nombre_excel).worksheet("COORDINACION")
        _append_fila_generica(sheet, {"JUGADOR": nombre, "EQUIPO": equipo})
    elif listado == 'DATOS JUGADORES':
        sheet = client.open(nombre_excel).worksheet("DATOS JUGADORES")
        letra = equipo.split()[-1] if equipo else ''
        _append_fila_generica(sheet, {"JUGADOR_NOMBRE": nombre, "JUGADOR_EQUIPO_LETRA": letra})
    else:
        raise ValueError(f"El listado '{listado}' no admite confirmación automática")

    revisadas = _cargar_revisadas()
    revisadas[_clave_revision(eq_norm, listado, tokens)] = {
        "decision": "confirmado", "equipo": equipo, "nombre": nombre,
        "fecha": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    }
    _guardar_revisadas(revisadas)


def aplicar_declinar(equipo, listado, nombre):
    eq_norm = _norm(equipo)
    tokens = _token_set(nombre)
    revisadas = _cargar_revisadas()
    revisadas[_clave_revision(eq_norm, listado, tokens)] = {
        "decision": "declinado", "equipo": equipo, "nombre": nombre,
        "fecha": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    }
    _guardar_revisadas(revisadas)


# ---------------------------------------------------------------------------
# Recálculo en segundo plano (mismo patrón que el auto-sync de la RFFM)
# ---------------------------------------------------------------------------

def discrepancias_status():
    estado = dict(_discrepancias_state)
    return estado


def intentar_recalcular_discrepancias_background(client, nombre_excel):
    with _discrepancias_lock:
        if _discrepancias_state['running']:
            return False
        finished_at = _discrepancias_state['finished_at']
        if finished_at:
            try:
                last = datetime.strptime(finished_at, '%d/%m/%Y %H:%M:%S')
                if (datetime.now() - last) < timedelta(minutes=DISCREPANCIAS_MIN_INTERVAL_MINUTES):
                    return False
            except Exception:
                pass
        _discrepancias_state['running'] = True
        _discrepancias_state['error'] = None
        _discrepancias_state['started_at'] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    def _job():
        try:
            resultado = calcular_discrepancias(client, nombre_excel)
            _discrepancias_state['resultado'] = resultado
        except Exception as e:
            _discrepancias_state['error'] = str(e)
        finally:
            _discrepancias_state['running'] = False
            _discrepancias_state['finished_at'] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    threading.Thread(target=_job, daemon=True).start()
    return True
