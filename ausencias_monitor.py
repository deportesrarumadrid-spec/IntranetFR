"""
ausencias_monitor.py
Detecta jugadores con 2+ faltas/no-convocatorias CONSECUTIVAS sin motivo explicado.
"""
import os
import json
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
def _get_conv_report_file():
    try:
        from competicion_scraper import _conv_report_file
        return _conv_report_file()
    except Exception:
        return os.path.join(BASE_DIR, 'static', 'data', 'convocatorias_report.json')
MOTIVOS_FILE = os.path.join(BASE_DIR, 'static', 'data', 'ausencias_motivos.json')

MIN_INTERVAL_MINUTES = 30

_state = {'running': False, 'error': None, 'finished_at': None, 'resultado': []}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Persistencia de motivos
# ---------------------------------------------------------------------------

def _load_motivos():
    if os.path.exists(MOTIVOS_FILE):
        try:
            with open(MOTIVOS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_motivos(motivos):
    os.makedirs(os.path.dirname(MOTIVOS_FILE), exist_ok=True)
    with open(MOTIVOS_FILE, 'w', encoding='utf-8') as f:
        json.dump(motivos, f, ensure_ascii=False, indent=2)


def guardar_motivo(tipo, equipo, nombre, fechas, motivo, usuario):
    """Persiste el motivo de una alerta de ausencia."""
    key = _alert_key(tipo, equipo, nombre, fechas)
    motivos = _load_motivos()
    motivos[key] = {
        'motivo': motivo,
        'usuario': usuario,
        'fecha_registro': datetime.now().strftime('%d/%m/%Y %H:%M'),
    }
    _save_motivos(motivos)
    return motivos[key]


def _alert_key(tipo, equipo, nombre, fechas):
    fechas_str = '_'.join(sorted(fechas)) if fechas else ''
    n = nombre.strip().upper().replace(' ', '_')
    e = equipo.strip().upper().replace(' ', '_')
    return f"{tipo}|{e}|{n}|{fechas_str}"


def _alert_key_dia(tipo, equipo, nombre, fecha):
    """Key para motivo de un día individual."""
    n = nombre.strip().upper().replace(' ', '_')
    e = equipo.strip().upper().replace(' ', '_')
    f = str(fecha).strip().replace('/', '_')
    return f"{tipo}|{e}|{n}|{f}"


def guardar_motivo_dia(tipo, equipo, nombre, fecha, motivo, usuario):
    """Persiste el motivo de una ausencia para un día concreto."""
    key = _alert_key_dia(tipo, equipo, nombre, fecha)
    motivos = _load_motivos()
    motivos[key] = {
        'motivo': motivo,
        'usuario': usuario,
        'fecha_registro': datetime.now().strftime('%d/%m/%Y %H:%M'),
    }
    _save_motivos(motivos)
    return motivos[key]


# ---------------------------------------------------------------------------
# Helpers fecha
# ---------------------------------------------------------------------------

def _parse_fecha(d):
    if not d:
        return None
    s = str(d).strip().replace('-', '/')
    for fmt in ('%d/%m/%Y', '%Y/%m/%d', '%d/%m/%y'):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Detección de ausencias en ASISTENCIAS (Google Sheets)
# ---------------------------------------------------------------------------

def _detectar_asistencias(client, nombre_excel):
    alertas = []
    motivos = _load_motivos()
    try:
        sheet = client.open(nombre_excel).worksheet("ASISTENCIAS")
        rows = sheet.get_all_values()
    except Exception as e:
        print(f"[ausencias_monitor] Error leyendo ASISTENCIAS: {e}")
        return alertas

    # col: 0=FECHA, 1=EQUIPO, 2=JUGADOR, 4=ESTADO("NO"/"SI"), 6=MOTIVO
    jugador_map = {}  # (equipo, nombre) -> [{fecha_dt, fecha_str, motivo}]
    for row in rows[1:]:
        if len(row) < 3:
            continue
        fecha_str = row[0].strip()
        equipo = row[1].strip().upper()
        nombre = row[2].strip()
        estado = row[4].strip().upper() if len(row) > 4 else ''
        motivo_celda = row[6].strip() if len(row) > 6 else ''
        if not fecha_str or not equipo or not nombre or estado != 'NO':
            continue
        fd = _parse_fecha(fecha_str)
        if not fd:
            continue
        key = (equipo, nombre)
        jugador_map.setdefault(key, []).append({
            'fecha_dt': fd,
            'fecha_str': fecha_str,
            'motivo': motivo_celda,
        })

    for (equipo, nombre), registros in jugador_map.items():
        registros.sort(key=lambda r: r['fecha_dt'])
        # Deduplicate by date: multiple sessions same day → prefer row with motivo
        date_map = {}
        for r in registros:
            fs = r['fecha_str']
            if fs not in date_map or (r['motivo'] and not date_map[fs]['motivo']):
                date_map[fs] = r
        registros = sorted(date_map.values(), key=lambda r: r['fecha_dt'])
        # Filtrar días ya justificados individualmente
        registros_sin_motivo = [
            r for r in registros
            if not r['motivo'] and _alert_key_dia('asistencia', equipo, nombre, r['fecha_str']) not in motivos
        ]
        # Detectar rachas consecutivas sin motivo
        i = 0
        while i < len(registros_sin_motivo):
            racha = [registros_sin_motivo[i]]
            j = i + 1
            while j < len(registros_sin_motivo):
                diff = (registros_sin_motivo[j]['fecha_dt'] - registros_sin_motivo[j - 1]['fecha_dt']).days
                if diff > 7:
                    break
                racha.append(registros_sin_motivo[j])
                j += 1
            if len(racha) >= 2:
                fechas = [r['fecha_str'] for r in racha]
                alert_key = _alert_key('asistencia', equipo, nombre, fechas)
                if alert_key not in motivos:
                    alertas.append({
                        'tipo': 'asistencia',
                        'equipo': equipo,
                        'nombre': nombre,
                        'fechas': fechas,
                        'key': alert_key,
                    })
            i = j if j > i else i + 1

    return alertas


# ---------------------------------------------------------------------------
# Detección de no-convocatorias (convocatorias_report.json)
# ---------------------------------------------------------------------------

def _detectar_convocatorias():
    alertas = []
    motivos = _load_motivos()
    try:
        with open(_get_conv_report_file(), 'r', encoding='utf-8') as f:
            report = json.load(f)
    except Exception as e:
        print(f"[ausencias_monitor] Error leyendo convocatorias_report.json: {e}")
        return alertas

    entries = [e for e in report.get('entries', []) if not e.get('sin_datos', True)]

    # Agrupar por equipo (categoria + letra)
    equipos_map = {}
    for entry in entries:
        cat = entry.get('categoria', '').upper()
        letra = entry.get('letra', '').upper()
        eq_key = f"{cat} {letra}".strip()
        equipos_map.setdefault(eq_key, []).append(entry)

    for eq_key, eq_entries in equipos_map.items():
        eq_entries.sort(key=lambda e: _parse_fecha(e.get('fecha', '')) or datetime.min)

        # Jugadores que han aparecido al menos UNA VEZ → candidatos a seguimiento
        todos_jugadores = set()
        for entry in eq_entries:
            for j in entry.get('convocados', []):
                todos_jugadores.add(j['nombre'])
        if not todos_jugadores:
            continue

        # Por cada jugador, rastrear ausencias consecutivas
        for nombre in todos_jugadores:
            racha = []
            for entry in eq_entries:
                estaba = any(j['nombre'] == nombre for j in entry.get('convocados', []))
                fecha_str = entry.get('fecha', '')
                if estaba:
                    racha = []  # rompe la racha
                else:
                    # Skip if this matchday was already justified per-day
                    day_key = _alert_key_dia('convocatoria', eq_key, nombre, fecha_str)
                    if day_key not in motivos:
                        racha.append({'fecha_str': fecha_str, 'match_key': entry.get('match_key', '')})

            if len(racha) >= 2:
                fechas = [r['fecha_str'] for r in racha]
                match_keys = [r['match_key'] for r in racha]
                alert_key = _alert_key('convocatoria', eq_key, nombre, fechas)
                if alert_key not in motivos:
                    alertas.append({
                        'tipo': 'convocatoria',
                        'equipo': eq_key,
                        'nombre': nombre,
                        'fechas': fechas,
                        'match_keys': match_keys,
                        'key': alert_key,
                    })

    return alertas


# ---------------------------------------------------------------------------
# Cálculo en background
# ---------------------------------------------------------------------------

def calcular_ausencias(client, nombre_excel):
    alertas = []
    alertas += _detectar_asistencias(client, nombre_excel)
    alertas += _detectar_convocatorias()
    return alertas


def intentar_recalcular_ausencias_background(client, nombre_excel):
    with _lock:
        if _state['running']:
            return
        if _state['finished_at']:
            elapsed = (datetime.now() - _state['finished_at']).total_seconds() / 60
            if elapsed < MIN_INTERVAL_MINUTES:
                return
        _state['running'] = True
        _state['error'] = None

    def _run():
        try:
            resultado = calcular_ausencias(client, nombre_excel)
            with _lock:
                _state['resultado'] = resultado
                _state['finished_at'] = datetime.now()
        except Exception as e:
            with _lock:
                _state['error'] = str(e)
            print(f"[ausencias_monitor] Error en background: {e}")
        finally:
            with _lock:
                _state['running'] = False

    threading.Thread(target=_run, daemon=True).start()


def ausencias_status():
    with _lock:
        return dict(_state)
