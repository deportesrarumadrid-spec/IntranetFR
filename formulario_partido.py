from flask import Blueprint, render_template, request, jsonify, current_app
from datetime import datetime
import os, json, re

_JDM_PATH = os.path.join(os.path.dirname(__file__), 'static', 'data', 'jugador_del_mes.json')

_JDM_MESES = [
    ('SEP','09'),('OCT','10'),('NOV','11'),('DIC','12'),
    ('ENE','01'),('FEB','02'),('MAR','03'),('ABR','04'),('MAY','05'),('JUN','06'),
]

def _jdm_default():
    ahora = datetime.now()
    y_sep = ahora.year if ahora.month >= 9 else ahora.year - 1
    out = []
    for label, num in _JDM_MESES:
        y = y_sep if int(num) >= 9 else y_sep + 1
        out.append({
            'mes': label, 'yearmon': f'{y}-{num}',
            'f7':      [{'nombre':'','ganador':False},{'nombre':'','ganador':False}],
            'once':    [{'nombre':'','ganador':False},{'nombre':'','ganador':False}],
            'porteros':[{'nombre':'','ganador':False},{'nombre':'','ganador':False}],
            'entrenador': '', 'gol': ''
        })
    return out

formulario_partido_bp = Blueprint('formulario_partido_bp', __name__)

# ---------- Hoja FORMULARIO_PARTIDOS: títulos y respuestas del antiguo formulario de Google ----------
TITULOS = ['MARCA TEMPORAL', 'EQUIPO', 'RIVAL', 'EVAL_GLOBAL', 'EVAL_INTENSIDAD', 'EVAL_FISICO', 'FALLOS_EQUIPO',
           'MEJORAR_FISICO', 'PORT_NOTA', 'PORT_FALLOS', 'RIVAL_DESTACADO', 'NUESTROS_MEJORES', 'NUESTROS_FLOJOS',
           'AUDIO_ENVIADO']


def _es_fecha(txt):
    return bool(re.match(r'^\s*\d{1,2}/\d{1,2}/\d{2,4}', txt or ''))


def con_titulos(filas):
    """Filas de FORMULARIO_PARTIDOS con fila de títulos (la hoja de 2026/27 se creó sin ella)."""
    if filas and filas[0] and _es_fecha(filas[0][0]):
        return [TITULOS] + filas
    return filas


def asegurar_titulos(ws):
    """Pone la fila de títulos si la hoja está vacía o empieza directamente por un formulario."""
    filas = ws.get_all_values()
    if not filas:
        ws.append_row(TITULOS)
    elif filas[0] and _es_fecha(filas[0][0]):
        ws.insert_row(TITULOS, 1)


def _equipo_google(nombre):
    """'ALE A F7' → 'ALEVIN A F7'; 'ALE FEM F7' → 'ALEVIN FEMENINO' (nombres del antiguo formulario de Google)."""
    n = ' '.join(str(nombre or '').upper().split())
    if re.match(r'^ALE\s+FEM', n):
        return 'ALEVIN FEMENINO'
    return re.sub(r'^ALE\b', 'ALEVIN', n)


def _fecha_dmY(txt):
    p = (txt or '').strip().split()[0].split('/')
    return datetime(int(p[2]) + (2000 if int(p[2]) < 100 else 0), int(p[1]), int(p[0]))


def migrar_desde_google():
    """Copia las respuestas del antiguo formulario de Google a FORMULARIO_PARTIDOS de su temporada (2025/26 → hoja sin
    sufijo; 2026/27 → FORMULARIO_PARTIDOS_2026_2027...). No duplica (misma fecha y equipo). Devuelve el nº copiado."""
    from app import client, NOMBRE_EXCEL, get_ws_name
    from deportivo import ENCUESTA_PARTIDO_KEY
    raw = client.open(NOMBRE_EXCEL)
    raw = getattr(raw, '_ss', raw)  # sin el cambio automático de temporada: se elige la hoja de cada fila
    google = client.open_by_key(ENCUESTA_PARTIDO_KEY).sheet1.get_all_values()[1:]
    hojas, nuevas = {}, {}
    for r in google:
        g = lambda i: r[i].strip() if i < len(r) else ''
        try:
            f = _fecha_dmY(g(0))
        except Exception:
            continue
        ini = f.year if f.month >= 8 else f.year - 1
        nombre = get_ws_name('FORMULARIO_PARTIDOS', f'{ini}/{ini + 1}')
        if nombre not in hojas:
            ws = None
            for t in ([nombre, 'FORMULARIO_PARTIDOS'] if ini == 2025 else [nombre]):  # 2025/26 usa la hoja sin sufijo
                try:
                    ws = raw.worksheet(t)
                    break
                except Exception:
                    pass
            if ws is None:
                hojas[nombre] = None
                continue
            asegurar_titulos(ws)
            hechos = set()
            for x in ws.get_all_values()[1:]:
                try:
                    hechos.add((_fecha_dmY(x[0]).date(), ' '.join(x[1].upper().split())))
                except Exception:
                    pass
            hojas[nombre] = (ws, hechos)
            nuevas[nombre] = []
        if not hojas[nombre]:
            continue
        ws, hechos = hojas[nombre]
        eq = _equipo_google(g(1))
        if (f.date(), eq) in hechos:
            continue
        hechos.add((f.date(), eq))
        nuevas[nombre].append([g(0), eq] + [g(i) for i in range(2, 13)] + ['SI' if g(13) else 'NO'])
    total = 0
    for nombre, filas in nuevas.items():
        if filas:
            hojas[nombre][0].append_rows(filas, value_input_option='RAW')
            total += len(filas)
    print(f'[FORMULARIOS] Copiadas {total} respuestas del formulario de Google', flush=True)
    return total


_MIGRACION = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'datos_privados', 'formulario_google.json')


def tick(ahora):
    """push_scheduler: copia las respuestas de Google en cuanto arranca y luego cada día a las 03:00
    (por si algún entrenador usa todavía el enlace antiguo antes de cerrarlo)."""
    try:
        with open(_MIGRACION, encoding='utf-8') as f:
            ultimo = json.load(f).get('ultimo', '')
    except Exception:
        ultimo = ''
    hoy = ahora.date().isoformat()
    if ultimo == hoy or (ultimo and ahora.hour != 3):
        return
    os.makedirs(os.path.dirname(_MIGRACION), exist_ok=True)
    with open(_MIGRACION, 'w', encoding='utf-8') as f:  # marcar antes: un intento al día
        json.dump({'ultimo': hoy}, f)

    def _job():  # en segundo plano: no retrasa el resto de avisos de ese minuto
        try:
            n = migrar_desde_google()
            with open(_MIGRACION, 'w', encoding='utf-8') as f:
                json.dump({'ultimo': hoy, 'copiadas': n, 'fecha': ahora.isoformat(timespec='minutes')}, f)
        except Exception as e:
            print(f'[FORMULARIOS] Error copiando del formulario de Google: {e}', flush=True)
    import threading
    threading.Thread(target=_job, daemon=True).start()

@formulario_partido_bp.route('/formulario-partido', methods=['GET', 'POST'])
def formulario_partido():
    # En GET, renderiza el formulario
    if request.method == 'GET':
        # Obtenemos la lista de equipos disponibles
        equipos = []
        try:
            from app import app as main_app
            client = main_app.gs_client
            sheet_eq = client.open(main_app.gs_name).worksheet("EQUIPO")
            rows_eq = sheet_eq.get_all_values()
            if rows_eq:
                headers = [str(h).strip().upper() for h in rows_eq[0]]
                if "EQUIPO" in headers:
                    idx_e = headers.index("EQUIPO")
                    from app import ordenar_equipos
                    equipos = ordenar_equipos(list(set(str(r[idx_e]).strip() for r in rows_eq[1:] if len(r) > idx_e and r[idx_e].strip())))
        except Exception as e:
            print(f"Error al cargar equipos para el formulario: {e}")
            equipos = ["PRIMER EQUIPO", "BENJAMIN A", "ALEVIN A", "INFANTIL A"] # Fallback

        equipo_default = request.args.get('equipo', '').strip()
        fecha_default  = request.args.get('fecha', '').strip()

        # Buscar datos existentes para ese equipo + fin de semana
        datos_existentes = {}
        if equipo_default and fecha_default:
            try:
                from datetime import timedelta
                dt_ref = datetime.strptime(fecha_default, "%Y-%m-%d")
                targets = {(dt_ref + timedelta(days=d)).strftime("%d/%m/%Y") for d in (0, 1)}
                sh = client.open(main_app.gs_name)
                ws_fp = sh.worksheet('FORMULARIO_PARTIDOS')
                all_fp = con_titulos(ws_fp.get_all_values())
                if all_fp:
                    hdrs = [str(h).strip() for h in all_fp[0]]
                    col_map = {
                        'RIVAL': 'rival', 'EVAL_GLOBAL': 'eval_global',
                        'EVAL_INTENSIDAD': 'eval_intensidad', 'EVAL_FISICO': 'eval_fisico',
                        'FALLOS_EQUIPO': 'fallos_equipo', 'MEJORAR_FISICO': 'mejorar_fisico',
                        'PORT_NOTA': 'port_nota', 'PORT_FALLOS': 'port_fallos',
                        'RIVAL_DESTACADO': 'rival_destacado', 'NUESTROS_MEJORES': 'nuestros_mejores',
                        'NUESTROS_FLOJOS': 'nuestros_flojos', 'AUDIO_ENVIADO': 'audio_enviado',
                    }
                    idx_f = hdrs.index('MARCA TEMPORAL') if 'MARCA TEMPORAL' in hdrs else -1
                    idx_e = hdrs.index('EQUIPO') if 'EQUIPO' in hdrs else -1
                    for row in reversed(all_fp[1:]):
                        eq_r = row[idx_e].strip() if idx_e >= 0 and idx_e < len(row) else ''
                        if eq_r != equipo_default:
                            continue
                        fecha_r = row[idx_f].strip().split(' ')[0] if idx_f >= 0 and idx_f < len(row) else ''
                        if fecha_r in targets:
                            for col, field in col_map.items():
                                if col in hdrs:
                                    datos_existentes[field] = row[hdrs.index(col)]
                            break
            except Exception as e:
                print(f"Error buscando datos existentes formulario: {e}")

        return render_template('formulario_partido.html', equipos=equipos,
                               equipo_default=equipo_default, fecha_default=fecha_default,
                               datos_existentes=datos_existentes)

    # En POST, guarda en Google Sheets
    if request.method == 'POST':
        try:
            data = request.json
            from app import app as main_app
            client = main_app.gs_client
            sh = client.open(main_app.gs_name)
            try:
                ws = sh.worksheet('FORMULARIO_PARTIDOS')
            except:
                ws = sh.add_worksheet(title='FORMULARIO_PARTIDOS', rows='1000', cols='20')
            asegurar_titulos(ws)
            
            # Use provided date if exists, otherwise current time
            fecha_custom = data.get('fecha_partido', '').strip()
            if fecha_custom:
                # Assuming YYYY-MM-DD from input type date, we format it to match MARCA TEMPORAL (DD/MM/YYYY 12:00:00)
                try:
                    dt = datetime.strptime(fecha_custom, "%Y-%m-%d")
                    marca_temporal = dt.strftime("%d/%m/%Y 12:00:00")
                except:
                    marca_temporal = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            else:
                marca_temporal = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            
            row_data = [
                marca_temporal,
                data.get('equipo', ''),
                data.get('rival', ''),
                data.get('eval_global', ''),
                data.get('eval_intensidad', ''),
                data.get('eval_fisico', ''),
                data.get('fallos_equipo', ''),
                data.get('mejorar_fisico', ''),
                data.get('port_nota', ''),
                data.get('port_fallos', ''),
                data.get('rival_destacado', ''),
                data.get('nuestros_mejores', ''),
                data.get('nuestros_flojos', ''),
                ''  # AUDIO_ENVIADO: ya no se pregunta; la marca sale de los audios enviados por WhatsApp
            ]
            
            ws.append_row(row_data, value_input_option='USER_ENTERED')
            return jsonify({'status': 'success', 'message': 'Guardado correctamente'})
            
        except Exception as e:
            print(f"Error al guardar formulario partido: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

@formulario_partido_bp.route('/api/borrar_partido', methods=['POST'])
def api_borrar_partido():
    try:
        from app import app as main_app
        data = request.json or {}
        equipo = data.get('equipo', '').strip()
        fecha  = data.get('fecha', '').strip()
        if not equipo or not fecha:
            return jsonify({'status': 'error', 'message': 'Faltan datos'}), 400
        from datetime import timedelta
        dt_ref = datetime.strptime(fecha, "%Y-%m-%d")
        targets = {(dt_ref + timedelta(days=d)).strftime("%d/%m/%Y") for d in (0, 1)}
        client = main_app.gs_client
        sh = client.open(main_app.gs_name)
        ws = sh.worksheet('FORMULARIO_PARTIDOS')
        asegurar_titulos(ws)
        all_v = ws.get_all_values()
        if not all_v:
            return jsonify({'status': 'ok'})
        hdrs = [str(h).strip() for h in all_v[0]]
        idx_f = hdrs.index('MARCA TEMPORAL') if 'MARCA TEMPORAL' in hdrs else -1
        idx_e = hdrs.index('EQUIPO') if 'EQUIPO' in hdrs else -1
        # Recorrer de abajo hacia arriba para borrar sin desplazar índices
        for i in range(len(all_v) - 1, 0, -1):
            row = all_v[i]
            eq_r = row[idx_e].strip() if idx_e >= 0 and idx_e < len(row) else ''
            if eq_r != equipo:
                continue
            fecha_r = row[idx_f].strip().split(' ')[0] if idx_f >= 0 and idx_f < len(row) else ''
            if fecha_r in targets:
                ws.delete_rows(i + 1)  # Sheets es 1-indexado, +1 por la cabecera
                return jsonify({'status': 'ok'})
        return jsonify({'status': 'ok', 'message': 'No encontrado'})
    except Exception as e:
        print(f"Error en api_borrar_partido: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@formulario_partido_bp.route('/api/formularios_partido', methods=['GET'])
def api_formularios_partido():
    try:
        from app import app as main_app
        client = main_app.gs_client
        sh = client.open(main_app.gs_name)
        try:
            ws = sh.worksheet('FORMULARIO_PARTIDOS')
            all_v = con_titulos(ws.get_all_values())
            if not all_v:
                return jsonify([])
            headers = [str(h).strip() for h in all_v[0]]
            data = []
            for row in all_v[1:]:
                row_dict = {}
                for i, h in enumerate(headers):
                    if i < len(row):
                        row_dict[h] = row[i]
                    else:
                        row_dict[h] = ""
                data.append(row_dict)
            # AUDIO ENVIADO: según los audios que el entrenador manda por WhatsApp (INFORMES_SEMANALES del finde)
            try:
                from datetime import timedelta
                from deportivo import _get_or_crear_sheet_informes_semanales
                audios = {(r[0].strip(), ' '.join(r[1].upper().split())) for r in _get_or_crear_sheet_informes_semanales().get_all_values()
                          if len(r) >= 3 and r[2].strip().upper() == 'SI'}
                for d in data:
                    try:
                        f = _fecha_dmY(d.get('MARCA TEMPORAL', ''))
                    except Exception:
                        continue
                    sab = (f - timedelta(days=(f.weekday() - 5) % 7)).strftime('%d/%m/%Y')
                    d['AUDIO_ENVIADO'] = 'SI' if (sab, ' '.join(str(d.get('EQUIPO', '')).upper().split())) in audios else 'NO'
            except Exception as e:
                print(f"[FORMULARIOS] audios: {e}", flush=True)
            return jsonify(data)
        except:
            return jsonify([])
    except Exception as e:
        print(f"Error api_formularios_partido: {e}")
        return jsonify({"error": str(e)}), 500


@formulario_partido_bp.route('/api/jugador_del_mes', methods=['GET', 'POST'])
def api_jugador_del_mes():
    if request.method == 'GET':
        try:
            if os.path.exists(_JDM_PATH):
                with open(_JDM_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = _jdm_default()
            return jsonify({'status': 'success', 'data': data})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
    else:
        try:
            data = request.json
            os.makedirs(os.path.dirname(_JDM_PATH), exist_ok=True)
            with open(_JDM_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return jsonify({'status': 'success'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500


@formulario_partido_bp.route('/api/jugador_del_mes/sugerencias', methods=['GET'])
def api_jugador_del_mes_sugerencias():
    try:
        from app import app as main_app
        client = main_app.gs_client
        sh = client.open(main_app.gs_name)
        try:
            ws = sh.worksheet('FORMULARIO_PARTIDOS')
            all_v = ws.get_all_values()
        except Exception:
            return jsonify({'status': 'success', 'data': {}})

        if not all_v:
            return jsonify({'status': 'success', 'data': {}})

        headers = [str(h).strip() for h in all_v[0]]

        def _idx(col):
            return headers.index(col) if col in headers else -1

        idx_fecha   = _idx('MARCA TEMPORAL')
        idx_equipo  = _idx('EQUIPO')
        idx_mejores = _idx('NUESTROS_MEJORES')

        agregado = {}  # {yearmon: {equipo: {nombre_lower: {nombre, count}}}}

        for row in all_v[1:]:
            if idx_fecha < 0 or idx_equipo < 0 or idx_mejores < 0:
                continue
            fecha_str   = row[idx_fecha].strip()   if idx_fecha   < len(row) else ''
            equipo      = row[idx_equipo].strip()  if idx_equipo  < len(row) else ''
            mejores_raw = row[idx_mejores].strip() if idx_mejores < len(row) else ''
            if not fecha_str or not equipo or not mejores_raw:
                continue
            try:
                parts = fecha_str.split(' ')[0].split('/')
                yearmon = f'{parts[2]}-{parts[1].zfill(2)}' if len(parts) == 3 else None
            except Exception:
                yearmon = None
            if not yearmon:
                continue
            nombres = [n.strip() for n in re.split(r'[,\n]+', mejores_raw) if n.strip()]
            bucket = agregado.setdefault(yearmon, {}).setdefault(equipo, {})
            for nombre in nombres:
                key = nombre.lower()
                if key not in bucket:
                    bucket[key] = {'nombre': nombre, 'count': 0}
                bucket[key]['count'] += 1

        resultado = {}
        for ym, equipos in agregado.items():
            resultado[ym] = {}
            for eq, jugs in equipos.items():
                resultado[ym][eq] = sorted(jugs.values(), key=lambda x: x['count'], reverse=True)

        return jsonify({'status': 'success', 'data': resultado})
    except Exception as e:
        print(f"Error api_jugador_del_mes_sugerencias: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
