import threading
import time
import json
import os
import unicodedata
from datetime import datetime

SCHEDULE_FILE = os.path.join('static', 'data', 'push_schedule.json')
EQUIPOS_CONFIG_FILE = os.path.join('static', 'data', 'equipos_config.json')

_DIAS_TO_NUM = {
    'lunes': 0, 'martes': 1, 'miercoles': 2, 'miercoles': 2,
    'jueves': 3, 'viernes': 4, 'sabado': 5, 'domingo': 6
}

def _norm(s):
    return ''.join(c for c in unicodedata.normalize('NFD', str(s).lower()) if unicodedata.category(c) != 'Mn')

def _dias_a_numeros(dias_lista):
    result = set()
    for d in (dias_lista or []):
        n = _norm(d)
        if n in _DIAS_TO_NUM:
            result.add(_DIAS_TO_NUM[n])
    return result

def load_schedules():
    if not os.path.exists(SCHEDULE_FILE):
        return []
    try:
        with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f).get('schedules', [])
    except Exception:
        return []

def save_schedules(schedules):
    os.makedirs(os.path.dirname(SCHEDULE_FILE), exist_ok=True)
    with open(SCHEDULE_FILE, 'w', encoding='utf-8') as f:
        json.dump({'schedules': schedules}, f, ensure_ascii=False, indent=2, default=str)

def _get_equipos_cfg():
    try:
        with open(EQUIPOS_CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return {e['nombre']: e for e in data.get('equipos', [])}
    except Exception:
        return {}

def _should_run(schedule, now):
    hora = schedule.get('hora', '17:00')
    try:
        h, m = map(int, hora.split(':'))
    except Exception:
        return False

    if now.hour != h or now.minute != m:
        return False

    ultima_str = schedule.get('ultima_ejecucion')
    ultima = None
    if ultima_str:
        try:
            ultima = datetime.fromisoformat(ultima_str)
        except Exception:
            pass

    modo = schedule.get('modo', 'diario')

    if modo == 'diario':
        if ultima and ultima.date() == now.date():
            return False
        return True

    elif modo == 'cada_2_dias':
        if not ultima:
            return True
        return (now.date() - ultima.date()).days >= 2

    elif modo == 'dia_semana':
        dia_obj = int(schedule.get('dia_semana', 4))
        if now.weekday() != dia_obj:
            return False
        if ultima and ultima.date() == now.date():
            return False
        return True

    elif modo == 'dia_mes':
        dia_obj = int(schedule.get('dia_mes', 1))
        if now.day != dia_obj:
            return False
        if ultima and ultima.date() == now.date():
            return False
        return True

    return False

def _check_asistencias(client, nombre_excel, equipo, dias_numeros):
    hoy = datetime.now()
    try:
        sheet = client.open(nombre_excel).worksheet("ASISTENCIAS")
        rows = sheet.get_all_values()[1:]
        hechos = set(f"{r[0]}-{r[1].strip().upper()}" for r in rows if len(r) >= 2)
        faltas = 0
        for d in range(1, hoy.day):
            fecha = datetime(hoy.year, hoy.month, d)
            if fecha.weekday() in dias_numeros:
                key = f"{fecha.strftime('%d/%m/%Y')}-{equipo.strip().upper()}"
                if key not in hechos:
                    faltas += 1
        return faltas
    except Exception as e:
        print(f"[SCHED] check_asistencias {equipo}: {e}", flush=True)
        return 0

def _check_entrenamientos(data_folder, equipo, dias_numeros):
    hoy = datetime.now()
    equipo_key = equipo.strip().upper().replace(' ', '_')
    carpeta = os.path.join(data_folder, 'sesiones', equipo_key)
    faltas = 0
    for d in range(1, hoy.day):
        fecha = datetime(hoy.year, hoy.month, d)
        if fecha.weekday() in dias_numeros:
            fname = f"Sesion_{fecha.day:02d}-{fecha.month:02d}-{fecha.year}.json"
            if not os.path.exists(os.path.join(carpeta, fname)):
                faltas += 1
    return faltas

def _run_schedule(schedule, flask_app):
    from app import enviar_push
    sid = schedule.get('id')
    tipo = schedule.get('tipo', 'asistencias')
    equipos_obj = schedule.get('equipos', [])
    texto_tpl = schedule.get('texto', '')
    link = schedule.get('link', 'https://intranet.clubfuentelarreyna.com/movil')

    try:
        client = flask_app.gs_client
        nombre_excel = flask_app.gs_name
        data_folder = flask_app.config.get('DATA_FOLDER', os.path.join('static', 'data'))
        equipos_cfg = _get_equipos_cfg()
        hoy = datetime.now()
        mes_nombre = ['enero','febrero','marzo','abril','mayo','junio','julio',
                      'agosto','septiembre','octubre','noviembre','diciembre'][hoy.month - 1]

        # Get coaches from PERFILES (TIPO=ENTRENADOR)
        perfiles_sheet = client.open(nombre_excel).worksheet("PERFILES")
        perfiles_data = perfiles_sheet.get_all_records()

        for equipo in equipos_obj:
            equipo_up = equipo.strip().upper()
            cfg = equipos_cfg.get(equipo, equipos_cfg.get(equipo_up, {}))
            dias_numeros = _dias_a_numeros(cfg.get('dias', []))
            if not dias_numeros:
                print(f"[SCHED] Sin dias para {equipo}, skip", flush=True)
                continue

            coaches = [
                p for p in perfiles_data
                if equipo_up in [e.strip().upper() for e in str(p.get('EQUIPO', '')).split(',')]
                and str(p.get('TIPO', '')).strip().upper() == 'ENTRENADOR'
            ]
            if not coaches:
                print(f"[SCHED] Sin entrenadores para {equipo}", flush=True)
                continue

            for coach in coaches:
                nombre = str(coach.get('USUARIO', '')).strip()
                if not nombre:
                    continue

                if tipo == 'asistencias':
                    n_faltas = _check_asistencias(client, nombre_excel, equipo_up, dias_numeros)
                else:
                    n_faltas = _check_entrenamientos(data_folder, equipo_up, dias_numeros)

                if n_faltas > 0:
                    msg = texto_tpl.replace('{nombre}', nombre).replace('{equipo}', equipo).replace('{mes}', mes_nombre).replace('{faltas}', str(n_faltas))
                    if link:
                        msg += f"\n\n{link}"
                    enviar_push(nombre.lower(), msg)
                    print(f"[SCHED] Push → {nombre} ({equipo}): {n_faltas} faltas de {tipo}", flush=True)
                else:
                    print(f"[SCHED] {nombre} ({equipo}) al día en {tipo}", flush=True)

        # Update ultima_ejecucion
        schedules = load_schedules()
        for s in schedules:
            if s.get('id') == sid:
                s['ultima_ejecucion'] = hoy.isoformat()
                break
        save_schedules(schedules)

    except Exception as e:
        print(f"[SCHED] Error schedule {sid}: {e}", flush=True)

def _loop(flask_app):
    time.sleep(15)
    while True:
        try:
            now = datetime.now()
            lock = f"/tmp/psch_{now.strftime('%Y%m%d%H%M')}.lock"
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
            except FileExistsError:
                time.sleep(60)
                continue

            for s in load_schedules():
                if s.get('activo') and _should_run(s, now):
                    print(f"[SCHED] Lanzando: {s.get('nombre', s.get('id','?'))}", flush=True)
                    _run_schedule(s, flask_app)

        except Exception as e:
            print(f"[SCHED LOOP] {e}", flush=True)

        time.sleep(60)

def start(flask_app):
    t = threading.Thread(target=_loop, args=(flask_app,), daemon=True)
    t.start()
    print("[SCHED] Scheduler iniciado", flush=True)
