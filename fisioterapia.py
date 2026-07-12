import os, re, json
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, current_app

fisio_bp = Blueprint('fisio', __name__)

CALENDAR_ID   = 'infoclubfuentelarreyna@gmail.com'
FISIO_SHEET   = 'FISIOTERAPIA'
TZ            = 'Europe/Madrid'

_BASE = os.path.dirname(os.path.abspath(__file__))
FISIO_CONFIG_PATH = os.path.join(_BASE, 'static', 'data', 'fisio_config.json')

_CONFIG_DEFAULT = {
    'slot_minutos': 60,
    'dias': {
        '0': {'activo': True,  'hora_ini': 9,  'hora_fin': 17},
        '1': {'activo': True,  'hora_ini': 9,  'hora_fin': 17},
        '2': {'activo': True,  'hora_ini': 9,  'hora_fin': 17},
        '3': {'activo': True,  'hora_ini': 9,  'hora_fin': 17},
        '4': {'activo': True,  'hora_ini': 9,  'hora_fin': 17},
        '5': {'activo': False, 'hora_ini': 9,  'hora_fin': 13},
        '6': {'activo': False, 'hora_ini': 9,  'hora_fin': 13},
    }
}

def _cargar_config():
    try:
        with open(FISIO_CONFIG_PATH, encoding='utf-8') as f:
            cfg = json.load(f)
        # Rellenar días que puedan faltar
        for k, v in _CONFIG_DEFAULT['dias'].items():
            cfg.setdefault('dias', {})[k] = cfg.get('dias', {}).get(k, v)
        cfg.setdefault('slot_minutos', 60)
        return cfg
    except Exception:
        return json.loads(json.dumps(_CONFIG_DEFAULT))

def _guardar_config(cfg):
    os.makedirs(os.path.dirname(FISIO_CONFIG_PATH), exist_ok=True)
    with open(FISIO_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

TIPOS_LESION = [
    'Muscular – Tirón', 'Muscular – Contractura', 'Muscular – Rotura',
    'Ligamentosa – Esguince', 'Tendinitis / Tendinopatía',
    'Ósea – Periostitis', 'Ósea – Fractura', 'Contusión',
    'Sobrecarga', 'Rehabilitación', 'Revisión / Prevención', 'Otro'
]

# ─── Google Calendar ───────────────────────────────────────────────────────────

def _calendar_service():
    from oauth2client.service_account import ServiceAccountCredentials
    from googleapiclient.discovery import build
    BASE = os.path.dirname(os.path.abspath(__file__))
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        os.path.join(BASE, 'secretos.json'),
        ['https://www.googleapis.com/auth/calendar']
    )
    return build('calendar', 'v3', credentials=creds, cache_discovery=False)

# ─── Google Sheets FISIOTERAPIA ────────────────────────────────────────────────

_HEADERS = ['EVENT_ID','FECHA','HORA','NOMBRE_JUGADOR','EQUIPO','CORREO',
            'TIPO_LESION','COMENTARIO_FISIO','SESION_REALIZADA']

def _fisio_sheet():
    client = current_app.gs_client
    wb = client.open(current_app.gs_name)
    try:
        return wb.worksheet(FISIO_SHEET)
    except Exception:
        sh = wb.add_worksheet(title=FISIO_SHEET, rows=1000, cols=len(_HEADERS))
        sh.append_row(_HEADERS)
        return sh

def _load_fisio_dict():
    try:
        rows = _fisio_sheet().get_all_records()
        return {str(r.get('EVENT_ID', '')): r for r in rows if r.get('EVENT_ID')}
    except Exception as e:
        print(f'[fisio] Error sheet: {e}')
        return {}

# ─── Parseo descripción Google Calendar ────────────────────────────────────────

def _parse_desc(desc):
    fields = {}
    if not desc:
        return fields
    for pat, key in [
        (r'(?i)nombre[:\s]+([^\n\r]+)',        'NOMBRE'),
        (r'(?i)apellido[s]?[:\s]+([^\n\r]+)',  'APELLIDO'),
        (r'(?i)equipo[:\s]+([^\n\r]+)',        'EQUIPO'),
        (r'(?i)(correo|email)[:\s]+([^\n\r]+)','CORREO'),
    ]:
        m = re.search(pat, desc)
        if m:
            fields[key] = m.group(m.lastindex).strip()
    return fields

def _parse_event(evt, fisio_data):
    start = evt.get('start', {})
    s = start.get('dateTime', start.get('date', ''))
    if 'T' in s:
        s_clean = s[:19] if len(s) > 19 else s
        try:
            dt = datetime.fromisoformat(s_clean)
        except Exception:
            dt = datetime.now()
        fecha = dt.strftime('%Y-%m-%d')
        hora  = dt.strftime('%H:%M')
    else:
        fecha, hora = s, ''

    attendees = evt.get('attendees', [])
    desc      = evt.get('description', '') or ''
    title     = evt.get('summary', '') or ''
    fields    = _parse_desc(desc)

    if attendees and not fields.get('CORREO'):
        fields['CORREO'] = attendees[0].get('email', '')

    is_booked = bool(attendees) or bool(fields.get('NOMBRE')) or bool(fields.get('CORREO'))

    if not fields.get('NOMBRE') and is_booked:
        for sep in [':', '-', ' con ', ' with ']:
            if sep in title.lower():
                raw = title.split(sep, 1)[-1].strip()
                parts = raw.split(' ', 1)
                fields.setdefault('NOMBRE', parts[0].title())
                if len(parts) > 1:
                    fields.setdefault('APELLIDO', parts[1].title())
                break

    eid = evt.get('id', '')
    fd  = fisio_data.get(eid, {})

    return {
        'id':          eid,
        'fecha':       fecha,
        'hora':        hora,
        'titulo':      title,
        'es_reservado': is_booked,
        'nombre':      fields.get('NOMBRE', ''),
        'apellido':    fields.get('APELLIDO', ''),
        'equipo':      fields.get('EQUIPO', ''),
        'correo':      fields.get('CORREO', ''),
        'descripcion': desc,
        'tipo_lesion': str(fd.get('TIPO_LESION', '')),
        'comentario':  str(fd.get('COMENTARIO_FISIO', '')),
        'realizada':   str(fd.get('SESION_REALIZADA', '')),
    }

def _generar_slots(lunes, dom, eventos_por_clave, cfg=None):
    """Genera todos los slots de la semana usando la configuración dinámica."""
    if cfg is None:
        cfg = _cargar_config()
    slot_min = cfg.get('slot_minutos', 60)
    dias_cfg = cfg.get('dias', {})
    step_h   = slot_min / 60  # puede ser fracción (e.g. 0.5 para 30 min)
    slots = []
    d = lunes
    while d <= dom:
        dow     = str(d.weekday())
        dia_cfg = dias_cfg.get(dow, {})
        if dia_cfg.get('activo', False):
            hora_ini = dia_cfg.get('hora_ini', 9)
            hora_fin = dia_cfg.get('hora_fin', 17)
            mins = hora_ini * 60
            while mins < hora_fin * 60:
                hh = mins // 60
                mm = mins % 60
                hora_str = f'{hh:02d}:{mm:02d}'
                clave    = f'{d.isoformat()}_{hora_str}'
                evento   = eventos_por_clave.get(clave)
                if evento:
                    slots.append(evento)
                else:
                    slots.append({
                        'id': '',
                        'fecha': d.isoformat(),
                        'hora':  hora_str,
                        'es_reservado': False,
                        'nombre': '', 'apellido': '', 'equipo': '',
                        'correo': '', 'titulo': '', 'descripcion': '',
                        'tipo_lesion': '', 'comentario': '', 'realizada': '',
                    })
                mins += slot_min
        d += timedelta(days=1)
    return slots

# ─── Rutas ─────────────────────────────────────────────────────────────────────

@fisio_bp.route('/fisioterapia')
def fisioterapia_page():
    if not session.get('usuario'):
        return redirect(url_for('index'))
    cfg = _cargar_config()
    return render_template('fisioterapia.html',
                           usuario=session.get('usuario'),
                           tipos_lesion=TIPOS_LESION,
                           fisio_config=cfg)


@fisio_bp.route('/api/fisio/semana')
def api_fisio_semana():
    if not session.get('usuario'):
        return jsonify({'error': 'No session'}), 401
    offset = int(request.args.get('offset', 0))
    hoy    = date.today()
    lunes  = hoy - timedelta(days=hoy.weekday()) + timedelta(weeks=offset)
    dom    = lunes + timedelta(days=6)
    try:
        svc   = _calendar_service()
        t_min = datetime.combine(lunes, datetime.min.time()).strftime('%Y-%m-%dT%H:%M:%SZ')
        t_max = datetime.combine(dom,   datetime.max.time()).strftime('%Y-%m-%dT%H:%M:%SZ')
        result = svc.events().list(
            calendarId=CALENDAR_ID, timeMin=t_min, timeMax=t_max,
            singleEvents=True, orderBy='startTime'
        ).execute()
        events    = result.get('items', [])
        fisio_map = _load_fisio_dict()
        parsed    = [_parse_event(e, fisio_map) for e in events]

        # Indexar eventos por fecha_hora para overlay sobre slots generados
        por_clave = {f"{e['fecha']}_{e['hora']}": e for e in parsed if e['hora']}

        cfg        = _cargar_config()
        slots      = _generar_slots(lunes, dom, por_clave, cfg)
        reservadas = sum(1 for s in slots if s['es_reservado'])
        total      = len(slots)

        return jsonify({
            'semana_inicio': lunes.isoformat(),
            'semana_fin':    dom.isoformat(),
            'citas':         slots,
            'capacidad':     total,
            'reservadas':    reservadas,
            'libres':        total - reservadas,
        })
    except Exception as e:
        import traceback
        try:
            msg = f'HTTP {e.resp.status}: {e.content.decode()}'
        except Exception:
            msg = repr(e) or str(e) or 'Error desconocido'
        print(f'[fisio] Semana error: {msg}\n{traceback.format_exc()}')
        return jsonify({'error': msg, 'citas': [],
                        'semana_inicio': lunes.isoformat(), 'semana_fin': dom.isoformat(),
                        'capacidad': 0, 'reservadas': 0, 'libres': 0})


@fisio_bp.route('/api/fisio/crear', methods=['POST'])
def api_fisio_crear():
    if not session.get('usuario'):
        return jsonify({'error': 'No session'}), 401
    d = request.json or {}
    fecha   = d.get('fecha', '')
    hora    = d.get('hora', '')
    nombre  = d.get('nombre', '').strip()
    apellido= d.get('apellido', '').strip()
    equipo  = d.get('equipo', '').strip()
    correo  = d.get('correo', '').strip()
    if not (fecha and hora):
        return jsonify({'error': 'Faltan fecha/hora'}), 400
    try:
        cfg      = _cargar_config()
        slot_min = cfg.get('slot_minutos', 60)
        h, m = map(int, hora.split(':'))
        dt_start = datetime(int(fecha[:4]), int(fecha[5:7]), int(fecha[8:10]), h, m)
        dt_end   = dt_start + timedelta(minutes=slot_min)
        body = {
            'summary': f'Fisio – {nombre} {apellido}'.strip(' –'),
            'description': (
                f'NOMBRE: {nombre}\n'
                f'APELLIDO: {apellido}\n'
                f'EQUIPO: {equipo}\n'
                f'CORREO: {correo}'
            ),
            'start': {'dateTime': dt_start.strftime('%Y-%m-%dT%H:%M:00'), 'timeZone': TZ},
            'end':   {'dateTime': dt_end.strftime('%Y-%m-%dT%H:%M:00'),   'timeZone': TZ},
        }
        svc   = _calendar_service()
        event = svc.events().insert(calendarId=CALENDAR_ID, body=body).execute()
        return jsonify({'status': 'ok', 'event_id': event.get('id', '')})
    except Exception as e:
        import traceback
        try:
            msg = f'HTTP {e.resp.status}: {e.content.decode()}'
        except Exception:
            msg = repr(e) or str(e)
        print(f'[fisio] Crear error: {msg}\n{traceback.format_exc()}')
        return jsonify({'error': msg}), 500


@fisio_bp.route('/api/fisio/cancelar', methods=['POST'])
def api_fisio_cancelar():
    if not session.get('usuario'):
        return jsonify({'error': 'No session'}), 401
    d   = request.json or {}
    eid = d.get('event_id', '')
    if not eid:
        return jsonify({'error': 'Falta event_id'}), 400
    try:
        svc = _calendar_service()
        svc.events().delete(calendarId=CALENDAR_ID, eventId=eid).execute()
        return jsonify({'status': 'ok'})
    except Exception as e:
        import traceback
        try:
            msg = f'HTTP {e.resp.status}: {e.content.decode()}'
        except Exception:
            msg = repr(e) or str(e)
        print(f'[fisio] Cancelar error: {msg}\n{traceback.format_exc()}')
        return jsonify({'error': msg}), 500


@fisio_bp.route('/api/fisio/guardar', methods=['POST'])
def api_fisio_guardar():
    if not session.get('usuario'):
        return jsonify({'error': 'No session'}), 401
    d = request.json or {}
    eid = d.get('event_id', '')
    if not eid:
        return jsonify({'error': 'Falta event_id'}), 400
    try:
        sheet   = _fisio_sheet()
        rows    = sheet.get_all_records()
        row_idx = None
        for i, r in enumerate(rows):
            if str(r.get('EVENT_ID', '')) == eid:
                row_idx = i + 2
                break
        nombre_completo = (d.get('nombre', '') + ' ' + d.get('apellido', '')).strip()
        new_row = [eid, d.get('fecha',''), d.get('hora',''), nombre_completo,
                   d.get('equipo',''), d.get('correo',''),
                   d.get('tipo_lesion',''), d.get('comentario',''), d.get('realizada','SI')]
        if row_idx:
            sheet.update(f'A{row_idx}:I{row_idx}', [new_row])
        else:
            sheet.append_row(new_row)
        return jsonify({'status': 'ok'})
    except Exception as e:
        print(f'[fisio] Save error: {e}')
        return jsonify({'error': str(e)}), 500


@fisio_bp.route('/api/fisio/jugador')
def api_fisio_jugador():
    if not session.get('usuario'):
        return jsonify({'error': 'No session'}), 401
    nombre = request.args.get('nombre', '').strip().upper()
    try:
        rows = _fisio_sheet().get_all_records()
        visitas = [
            {'fecha': str(r.get('FECHA','')), 'hora': str(r.get('HORA','')),
             'equipo': str(r.get('EQUIPO','')), 'tipo_lesion': str(r.get('TIPO_LESION','')),
             'comentario': str(r.get('COMENTARIO_FISIO','')), 'realizada': str(r.get('SESION_REALIZADA',''))}
            for r in rows if nombre and nombre in str(r.get('NOMBRE_JUGADOR','')).upper()
        ]
        visitas.sort(key=lambda x: x.get('fecha',''), reverse=True)
        return jsonify({'visitas': visitas, 'total': len(visitas)})
    except Exception as e:
        return jsonify({'error': str(e), 'visitas': [], 'total': 0})


@fisio_bp.route('/api/fisio/kpis')
def api_fisio_kpis():
    if not session.get('usuario'):
        return jsonify({'error': 'No session'}), 401
    try:
        import calendar as cal_mod
        cfg      = _cargar_config()
        slot_min = cfg.get('slot_minutos', 60)
        dias_cfg = cfg.get('dias', {})

        # ── Todos los equipos del club ─────────────────────────────────────
        todos_equipos = set()
        try:
            wb = current_app.gs_client.open(current_app.gs_name)
            jug_vals = wb.worksheet('JUGADORES').get_all_values()
            if jug_vals:
                hdrs = [h.upper().strip() for h in jug_vals[0]]
                idx  = next((hdrs.index(c) for c in ['EQUIPO','CATEGORIA','GRUPO'] if c in hdrs), -1)
                if idx >= 0:
                    for row in jug_vals[1:]:
                        if len(row) > idx and row[idx].strip():
                            todos_equipos.add(row[idx].strip().upper())
        except Exception as ex:
            print(f'[fisio kpis] No se leyó JUGADORES: {ex}')

        # ── Leer hoja FISIOTERAPIA ─────────────────────────────────────────
        rows = _fisio_sheet().get_all_records()

        por_equipo  = {eq: {'total': 0, 'realizadas': 0} for eq in todos_equipos}
        por_mes     = {}
        lesiones    = {}
        jugadores   = {}
        por_franja  = {}  # hora_str -> nº reservas
        fechas_data = []

        for r in rows:
            if not r.get('NOMBRE_JUGADOR'):
                continue
            eq   = str(r.get('EQUIPO','Sin equipo')).strip().upper() or 'Sin equipo'
            jug  = str(r.get('NOMBRE_JUGADOR','')).strip()
            les  = str(r.get('TIPO_LESION','')).strip()
            fec  = str(r.get('FECHA','')).strip()
            rea  = str(r.get('SESION_REALIZADA','SI')).strip().upper()
            hora = str(r.get('HORA','')).strip()
            mes  = fec[:7] if len(fec) >= 7 else 'Sin fecha'

            por_equipo.setdefault(eq, {'total': 0, 'realizadas': 0})
            por_equipo[eq]['total'] += 1
            if rea == 'SI': por_equipo[eq]['realizadas'] += 1

            por_mes.setdefault(mes, {'reservadas': 0, 'realizadas': 0})
            por_mes[mes]['reservadas'] += 1
            if rea == 'SI': por_mes[mes]['realizadas'] += 1

            if les: lesiones[les] = lesiones.get(les, 0) + 1
            if jug: jugadores[jug] = jugadores.get(jug, 0) + 1
            if hora: por_franja[hora] = por_franja.get(hora, 0) + 1
            if fec and len(fec) >= 10:
                try: fechas_data.append(date.fromisoformat(fec[:10]))
                except: pass

        # ── Capacidad por mes ──────────────────────────────────────────────
        def _cap_mes(anio, mes_num):
            _, dias_mes = cal_mod.monthrange(anio, mes_num)
            total = 0
            for d in range(1, dias_mes + 1):
                dow = str(date(anio, mes_num, d).weekday())
                dc  = dias_cfg.get(dow, {})
                if dc.get('activo', False):
                    ini = dc.get('hora_ini', 9); fin = dc.get('hora_fin', 17)
                    total += max(0, (fin * 60 - ini * 60) // slot_min)
            return total

        for mes_key in list(por_mes.keys()):
            if mes_key == 'Sin fecha':
                por_mes[mes_key].update({'capacidad': 0, 'no_usadas': 0, 'pct_uso': 0})
                continue
            try:
                anio_m, mes_m = int(mes_key[:4]), int(mes_key[5:7])
                cap = _cap_mes(anio_m, mes_m)
                res = por_mes[mes_key]['reservadas']
                por_mes[mes_key]['capacidad']  = cap
                por_mes[mes_key]['no_usadas']  = max(0, cap - res)
                por_mes[mes_key]['pct_uso']    = round(res / cap * 100, 1) if cap > 0 else 0
            except:
                por_mes[mes_key].update({'capacidad': 0, 'no_usadas': 0, 'pct_uso': 0})

        # ── Acumulado ──────────────────────────────────────────────────────
        total_res = sum(v['reservadas'] for v in por_mes.values())
        total_rea = sum(v.get('realizadas', 0) for v in por_mes.values())
        hoy = date.today()
        total_cap = 0
        if fechas_data:
            ini_iter = date(min(fechas_data).year, min(fechas_data).month, 1)
            cur = ini_iter
            while cur <= hoy:
                total_cap += _cap_mes(cur.year, cur.month)
                cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
        pct_uso_total = round(total_res / total_cap * 100, 1) if total_cap > 0 else 0

        acumulado = {
            'total_reservadas': total_res,
            'total_realizadas': total_rea,
            'total_capacidad':  total_cap,
            'pct_uso':          pct_uso_total,
            'pct_no_uso':       round(100 - pct_uso_total, 1),
        }

        # ── Disponibilidad real por franja ─────────────────────────────────
        franja_disp = {}
        if fechas_data:
            d_iter = min(fechas_data)
            while d_iter <= hoy:
                dow = str(d_iter.weekday())
                dc  = dias_cfg.get(dow, {})
                if dc.get('activo', False):
                    ini = dc.get('hora_ini', 9); fin = dc.get('hora_fin', 17)
                    mins = ini * 60
                    while mins < fin * 60:
                        hs = f'{mins//60:02d}:{mins%60:02d}'
                        franja_disp[hs] = franja_disp.get(hs, 0) + 1
                        mins += slot_min
                d_iter += timedelta(days=1)

        franjas = []
        for hs in sorted(set(list(por_franja) + list(franja_disp))):
            res_f  = por_franja.get(hs, 0)
            disp_f = franja_disp.get(hs, 0)
            pct_f  = round(res_f / disp_f * 100, 1) if disp_f > 0 else 0
            franjas.append({'hora': hs, 'reservadas': res_f, 'disponibles': disp_f, 'pct': pct_f})

        return jsonify({
            'por_equipo':    por_equipo,
            'por_mes':       {m: por_mes[m] for m in sorted(por_mes)},
            'acumulado':     acumulado,
            'franjas':       franjas,
            'lesiones':      sorted(lesiones.items(), key=lambda x: -x[1])[:8],
            'top_jugadores': sorted(jugadores.items(), key=lambda x: -x[1])[:10],
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)})


@fisio_bp.route('/api/fisio/config', methods=['GET'])
def api_fisio_config_get():
    if not session.get('usuario'):
        return jsonify({'error': 'No session'}), 401
    return jsonify(_cargar_config())


@fisio_bp.route('/api/fisio/config', methods=['POST'])
def api_fisio_config_post():
    if not session.get('usuario'):
        return jsonify({'error': 'No session'}), 401
    data = request.json or {}
    try:
        cfg = _cargar_config()
        if 'slot_minutos' in data:
            cfg['slot_minutos'] = int(data['slot_minutos'])
        if 'dias' in data:
            for k, v in data['dias'].items():
                cfg['dias'][str(k)] = {
                    'activo':   bool(v.get('activo', False)),
                    'hora_ini': int(v.get('hora_ini', 9)),
                    'hora_fin': int(v.get('hora_fin', 17)),
                }
        _guardar_config(cfg)
        return jsonify({'status': 'ok', 'config': cfg})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
