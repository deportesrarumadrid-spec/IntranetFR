import os
import json
import calendar
import unicodedata
import re
from datetime import datetime, timedelta
from flask import Blueprint, render_template, session, request, jsonify, redirect, current_app

# Creamos el Blueprint para Dirección Deportiva
deportivo_bp = Blueprint('deportivo_bp', __name__)

def normalizar_cabeceras_dep(headers):
    """Limpia las cabeceras para que la búsqueda de columnas sea robusta."""
    return [str(h).strip().upper().replace('Ó','O').replace('Í','I').replace('É','E').replace('Á','A').replace('Ú','U').replace(' ', '').replace('_', '') for h in headers]

def normalizar_fecha_sheet(fecha_str):
    """Normaliza fechas tipo DD/MM/YYYY eliminando ceros iniciales para comparaciones seguras."""
    if not fecha_str or '/' not in fecha_str: return str(fecha_str).strip()
    partes = fecha_str.split('/')
    return "/".join([str(int(p)) if p.strip().isdigit() else p.strip() for p in partes])

def limpiar_texto_robusto(t):
    """Quita tildes, espacios y pasa a mayúsculas para comparaciones infalibles."""
    if not t: return ""
    s = "".join(c for c in unicodedata.normalize('NFD', str(t)) if unicodedata.category(c) != 'Mn')
    return s.strip().upper()

def sanitizar_equipo_archivo(equipo):
    """Normaliza el nombre de un equipo para usarlo de forma segura en un nombre de archivo
    (debe coincidir exactamente con la misma función en app.py)."""
    s = re.sub(r'[^A-Za-z0-9]+', '_', (equipo or '').strip().upper())
    return s.strip('_') or 'SINEQUIPO'

CATEGORIAS_METODOLOGIA = ['ALEVIN F7', 'ALEVIN F11', 'PREBENJAMIN', 'BENJAMIN', 'INFANTIL', 'CHUPETIN']
MES_ABREVIATURA_METODOLOGIA = {
    '01': 'ENE', '02': 'FEB', '03': 'MAR', '04': 'ABR', '05': 'MAY', '06': 'JUN',
    '09': 'SEP', '10': 'OCT', '11': 'NOV', '12': 'DIC'
}

def obtener_tactico_tecnico_metodologia(client, NOMBRE_EXCEL, equipo, mes_actual):
    """Deriva las cadenas de objetivos TÁCTICO/TÉCNICO del mes a partir de la Metodología
    por Equipo (Coordinación > Metodología), según la categoría del equipo. Devuelve
    (tactico_str, tecnico_str), vacíos si no hay metodología para esa categoría/mes."""
    tactico_str, tecnico_str = "", ""
    try:
        nombre_eq_norm = limpiar_texto_robusto(equipo).replace('-', '').replace(' ', '')
        categoria_eq = None
        for cat in sorted(CATEGORIAS_METODOLOGIA, key=len, reverse=True):
            if cat.replace(' ', '') in nombre_eq_norm:
                categoria_eq = cat
                break
        mes_abrev = MES_ABREVIATURA_METODOLOGIA.get(mes_actual.split('-')[1])
        if categoria_eq and mes_abrev:
            sheet_met = client.open(NOMBRE_EXCEL).worksheet("METODOLOGIA_CATEGORIA")
            all_met = sheet_met.get_all_values()
            for row in all_met[1:]:
                if len(row) > 0 and row[0].strip().upper() == categoria_eq:
                    tecnico_rows = json.loads(row[2]) if len(row) > 2 and row[2] else []
                    tactico_rows = json.loads(row[3]) if len(row) > 3 and row[3] else []
                    tec_items = [r.get(mes_abrev, '').strip() for r in tecnico_rows if r.get(mes_abrev, '').strip()]
                    tac_items = [r.get(mes_abrev, '').strip() for r in tactico_rows if r.get(mes_abrev, '').strip()]
                    if tec_items: tecnico_str = ", ".join(tec_items)
                    if tac_items: tactico_str = ", ".join(tac_items)
                    break
    except Exception as e:
        print(f"Error vinculando metodología con los objetivos del mes: {e}")
    return tactico_str, tecnico_str

@deportivo_bp.route('/deportivo')
def deportivo():
    # Importamos las rutas de carpetas desde la app principal
    DATA_FOLDER = current_app.config.get('DATA_FOLDER', os.path.join(os.getcwd(), 'static', 'data'))
    UPLOAD_FOLDER = current_app.config.get('UPLOAD_FOLDER', os.path.join(os.getcwd(), 'static', 'uploads'))
    client = getattr(current_app, 'gs_client', None)
    NOMBRE_EXCEL = getattr(current_app, 'gs_name', "Control Asistencia Club")

    usuario = session.get('usuario')
    if not usuario:
        return redirect('/')

    if session.get('permisos', {}).get('ENTRENAMIENTOS') != 'SI':
        return "Acceso denegado", 403

    # 1. Obtener lista de TODOS los equipos para el desplegable de selección
    equipos = []
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("EQUIPO")
        all_v = sheet.get_all_values()
        if all_v:
            headers = normalizar_cabeceras_dep(all_v[0])
            idx_eq = -1
            # Búsqueda robusta por prioridad de nombres comunes, incluyendo "EQUIPO"
            for col_name in ["EQUIPO", "NOMBRE", "CATEGORIA", "GRUPO", "EQUIPOS"]:
                if col_name in headers:
                    idx_eq = headers.index(col_name)
                    break
            
            # Si no se encuentra una cabecera específica, asumimos que la columna de equipos es la primera (índice 0)
            # y que los datos pueden empezar desde la fila 0 (si no hay cabeceras explícitas)
            if idx_eq == -1: 
                idx_eq = 0
                start_row = 0 # Si no hay cabecera, los datos empiezan en la primera fila
            else:
                start_row = 1 # Si hay cabecera, los datos empiezan en la segunda fila

            # Filtramos valores vacíos y la propia cabecera si está en la lista
            equipos = sorted(list(set(str(row[idx_eq]).strip() for row in all_v[start_row:] if len(row) > idx_eq and str(row[idx_eq]).strip() and str(row[idx_eq]).strip().upper() != (headers[idx_eq] if start_row == 1 else ''))))
    except Exception as e:
        print(f"Error al cargar equipos en deportivo: {e}")

    # 2. Determinar equipo activo y actualizar sesión para que la selección sea persistente
    # Prioridad: 1. Parámetro URL | 2. Sesión | 3. Primer equipo disponible en la lista
    equipo_param = request.args.get('equipo')
    if equipo_param:
        equipo_activo = equipo_param.strip()
        session['equipo_defecto'] = equipo_activo
    else:
        equipo_activo = session.get('equipo_defecto', '')
        if not equipo_activo and equipos:
            equipo_activo = equipos[0]
            session['equipo_defecto'] = equipo_activo

    mes_actual = request.args.get('mes', '2026-05')
    hoy = datetime.now()

    # 3. Cargar objetivos desde Google Sheets (en lugar de JSON)
    objetivos = {"tactico": "", "tecnico": "", "completados": []}
    try:
        sheet_obj = client.open(NOMBRE_EXCEL).worksheet("OBJ TACTEC")
        all_objs = sheet_obj.get_all_values()
        target_y, target_m = mes_actual.split('-')
        
        for row in all_objs[1:]:
            if len(row) < 2: continue
            fecha_row = row[0].strip() # "DD/MM/YYYY"
            if '/' not in fecha_row: continue
            # Normalizamos partes de la fecha (ej: '5' -> '05') para asegurar match con target_m
            d, m, y = [p.zfill(2) if p.isdigit() else p for p in fecha_row.split('/')]
            if m == target_m and y == target_y and row[1].strip().upper() == equipo_activo.upper():
                # La planificación mensual suele estar en el día 01
                if d == "01" or not objetivos["tactico"]:
                    objetivos["tactico"] = row[3].strip() if len(row) > 3 else objetivos["tactico"]
                    objetivos["tecnico"] = row[4].strip() if len(row) > 4 else objetivos["tecnico"]
                
                # Agregar los completados del día a la lista con prefijo "dia-" para el frontend
                if len(row) > 2 and row[2].strip():
                    for o in row[2].split(','):
                        if o.strip():
                            objetivos["completados"].append(f"{int(d)}-{o.strip()}")
    except Exception as e:
        print(f"Error cargando objetivos desde Sheets para vista: {e}")

    # 3b. La planificación TÁCTICO/TÉCNICO del mes se nutre de la Metodología por Equipo
    # (Coordinación > Metodología), para que el calendario cuadre siempre con lo planificado allí.
    tactico_met, tecnico_met = obtener_tactico_tecnico_metodologia(client, NOMBRE_EXCEL, equipo_activo, mes_actual)
    if tecnico_met: objetivos["tecnico"] = tecnico_met
    if tactico_met: objetivos["tactico"] = tactico_met

    # 5. Obtener ejercicios semanales para el equipo activo
    ejercicios_semanales = {}
    try:
        sheet_eje = client.open(NOMBRE_EXCEL).worksheet("EJERCICIOS")
        all_eje = sheet_eje.get_all_values()
        if all_eje:
            # Columnas: SEMANA(0), EQUIPOS(1), CATEGORIA(2), TITULO(3), DESCRIPCION(4), URL(5)
            for row in all_eje[1:]:
                if len(row) >= 6 and row[0] and row[1]:
                    # Normalización robusta de la lista de equipos guardada
                    equipos_eje = [limpiar_texto_robusto(e) for e in str(row[1]).split(',') if e.strip()]
                    target_eq = limpiar_texto_robusto(equipo_activo)
                    
                    if target_eq in equipos_eje:
                        sem_key = normalizar_fecha_sheet(row[0])
                        if sem_key not in ejercicios_semanales:
                            ejercicios_semanales[sem_key] = []
                        ejercicios_semanales[sem_key].append({
                            "categoria": str(row[2]),
                            "titulo": row[3],
                            "descripcion": row[4],
                            "url": row[5]
                        })
            # Ordenar ejercicios por categoría (0, 1, 2, 3)
            for k in ejercicios_semanales:
                ejercicios_semanales[k].sort(key=lambda x: x['categoria'])
    except Exception as e:
        print(f"Error cargando ejercicios en deportivo: {e}")

    # 4. Lógica del Calendario
    try:
        anio, mes = map(int, mes_actual.split('-'))
    except:
        anio, mes = hoy.year, hoy.month

    cal = calendar.Calendar(firstweekday=0)
    semanas_raw = cal.monthdayscalendar(anio, mes)
    
    semanas = []
    es_mes_actual = (anio == hoy.year and mes == hoy.month)
    wd_hoy = hoy.weekday()
    dias_a_resaltar = []
    if es_mes_actual:
        dias_a_resaltar.append(hoy.day)
        if wd_hoy == 5: dias_a_resaltar.append(hoy.day + 1)
        elif wd_hoy == 6: dias_a_resaltar.append(hoy.day - 1)

    for s in semanas_raw:
        semana_formateada = []
        # Determinamos el lunes de esta fila para vincular con la tabla de Ejercicios
        anchor_day = 0
        anchor_idx = 0
        for idx, d in enumerate(s):
            if d != 0:
                anchor_day = d
                anchor_idx = idx
                break
        
        if anchor_day == 0: continue # Fila vacía
        
        dt_anchor = datetime(anio, mes, anchor_day)
        monday_dt = dt_anchor - timedelta(days=anchor_idx)
        semana_key = normalizar_fecha_sheet(monday_dt.strftime("%d/%m/%Y"))

        es_fin_de_semana_hoy = any(d in dias_a_resaltar for d in s if d != 0) if es_mes_actual else False
        for d in s:
            if d == 0: semana_formateada.append(None)
            else:
                es_hoy_exacto = (es_mes_actual and d == hoy.day)
                semana_formateada.append({
                    'numero': d, 
                    'es_hoy': es_hoy_exacto,
                    'resaltar_finde': es_fin_de_semana_hoy
                })
        
        # Mapeamos a 4 slots fijos según la categoría (0, 1, 2, 3) para mantener el orden solicitado
        ejes_fijos = [None, None, None, None]
        lista_ejes = ejercicios_semanales.get(semana_key, [])
        for e in lista_ejes:
            idx_cat = str(e['categoria']).strip()
            # Si la categoría es numérica (0-3), la ponemos en su sitio
            if idx_cat.isdigit() and 0 <= int(idx_cat) < 4:
                ejes_fijos[int(idx_cat)] = e

        semanas.append({
            'dias': semana_formateada,
            'ejercicios': ejes_fijos
        })

    # 3. Fotos subidas (vinculadas al equipo y mes/año exactos)
    clave_equipo_fotos = sanitizar_equipo_archivo(equipo_activo)
    archivos_reales = []
    fotos_subidas = []
    mes_str2  = f"{mes:02d}"
    anio_str2 = str(anio)
    prefijo_eq = f"entreno_{clave_equipo_fotos}_"
    if os.path.exists(UPLOAD_FOLDER):
        for _f in os.listdir(UPLOAD_FOLDER):
            if not _f.startswith(prefijo_eq): continue
            rest = _f[len(prefijo_eq):].rsplit('.', 1)[0]  # "DD-MM-YYYY"
            p = rest.split('-')
            if len(p) == 3 and p[1] == mes_str2 and p[2] == anio_str2:
                archivos_reales.append(_f)
                fotos_subidas.append(str(int(p[0])))

    # Cargar jugadores para el equipo activo
    jugadores_equipo = []
    try:
        sheet_jug = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        all_jug = sheet_jug.get_all_values()
        if all_jug:
            h_j = normalizar_cabeceras_dep(all_jug[0])
            i_n = h_j.index("NOMBRE") if "NOMBRE" in h_j else 0
            i_b = h_j.index("BAJADESDE") if "BAJADESDE" in h_j else (h_j.index("BAJA") if "BAJA" in h_j else (h_j.index("BAJA_DESDE") if "BAJA_DESDE" in h_j else -1))
            i_al = h_j.index("ALTADESDE") if "ALTADESDE" in h_j else (h_j.index("ALTA") if "ALTA" in h_j else (h_j.index("ALTA_DESDE") if "ALTA_DESDE" in h_j else -1))
            
            i_e = -1
            for col_name in ["EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS"]:
                if col_name in h_j:
                    i_e = h_j.index(col_name)
                    break
            
            hoy_dt = datetime.now()
            def parse_date_simple(d_str):
                if not d_str: return None
                s = str(d_str).strip().replace('-', '/')
                for fmt in ("%d/%m/%Y", "%Y/%m/%d", "%d/%m/%y"):
                    try: return datetime.strptime(s, fmt)
                    except: continue
                return None

            for row in all_jug[1:]:
                if i_e != -1 and len(row) > max(i_n, i_e):
                    if limpiar_texto_robusto(row[i_e]) == limpiar_texto_robusto(equipo_activo):
                        nombre_jugador = row[i_n].strip()
                        if nombre_jugador:
                            es_baja = False
                            baja_str = row[i_b].strip() if i_b != -1 and len(row) > i_b else ""
                            alta_str = row[i_al].strip() if i_al != -1 and len(row) > i_al else ""
                            baja_dt = parse_date_simple(baja_str)
                            alta_dt = parse_date_simple(alta_str)
                            if baja_dt and hoy_dt >= baja_dt:
                                if not alta_dt or hoy_dt < alta_dt:
                                    es_baja = True
                            jugadores_equipo.append({"nombre": nombre_jugador, "es_baja": es_baja})
    except Exception as e:
        print(f"Error al cargar jugadores para equipo activo: {e}")

    return render_template('deportivo/calendario.html',
                           usuario=usuario,
                           mes_actual=mes_actual,
                           objetivos=objetivos,
                           semanas=semanas,
                           archivos_subidos=len(archivos_reales),
                           fotos_subidas=fotos_subidas,
                           dias_transcurridos=hoy.day if es_mes_actual else (32 if (anio < hoy.year or (anio == hoy.year and mes < hoy.month)) else 0),
                           equipos=equipos,
                           equipo_defecto=equipo_activo,
                           jugadores_equipo=jugadores_equipo,
                           now=hoy
                           )

@deportivo_bp.route('/api/seguimiento_coordinacion', methods=['GET', 'POST'])
def api_seguimiento_coordinacion():
    client = current_app.gs_client
    NOMBRE_EXCEL = current_app.gs_name
    
    try:
        sheet_name = "COORDINACION"
        try:
            sheet = client.open(NOMBRE_EXCEL).worksheet(sheet_name)
        except:
            sheet = client.open(NOMBRE_EXCEL).add_worksheet(title=sheet_name, rows=1000, cols=6)
            sheet.append_row(["FECHA", "EQUIPO", "JUGADOR", "REUNION", "TIPO", "OBSERVACIONES"])

        if request.method == 'GET':
            fecha = request.args.get('fecha')
            equipo = request.args.get('equipo')
            all_v = sheet.get_all_values()
            
            seguimientos = []
            historial = {} # Guardaremos los últimos comentarios por jugador

            if len(all_v) > 1:
                # Recorremos de final a principio para el historial
                for row in reversed(all_v[1:]):
                    if len(row) >= 6 and row[1] == equipo:
                        jug = row[2]
                        if jug not in historial:
                            historial[jug] = {"obs": row[5], "tipo": row[4]} # Guardamos tipo y obs más reciente
                        
                        if row[0] == fecha:
                            seguimientos.append({
                                "jugador": jug,
                                "reunion": row[3] == "SI",
                                "tipo": row[4],
                                "obs": row[5]
                            })
            return jsonify({"actual": seguimientos, "historial": historial})

        # Metodo POST: Guardar seguimiento
        datos = request.json
        fecha = datos.get('fecha')
        equipo = datos.get('equipo')
        registros = datos.get('registros', []) # Listado de {jugador, reunion, obs}

        all_v = sheet.get_all_values()
        updates = []
        
        for reg in registros:
            fila_idx = -1
            # Buscar si ya existe el registro para este jugador, fecha y equipo
            for i, row in enumerate(all_v):
                if i == 0: continue # Saltar cabeceras
                if len(row) >= 3 and row[0].strip() == fecha.strip() and row[1].strip() == equipo.strip() and row[2].strip() == reg['jugador'].strip():
                    fila_idx = i + 1
                    break
            
            reunion_val = "SI" if reg['reunion'] else "NO"
            if fila_idx != -1:
                rango = f'D{fila_idx}:F{fila_idx}'
                sheet.update(range_name=rango, values=[[reunion_val, reg['tipo'], reg['obs']]], value_input_option='USER_ENTERED')
            else:
                sheet.append_row([fecha, equipo, reg['jugador'], reunion_val, reg['tipo'], reg['obs']])
        
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error en seguimiento_coordinacion: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@deportivo_bp.route('/api/tecnificaciones', methods=['GET', 'POST'])
def api_tecnificaciones():
    client = current_app.gs_client
    NOMBRE_EXCEL = current_app.gs_name
    try:
        sheet_name = "TECNIFICACIONES"
        try:
            sheet = client.open(NOMBRE_EXCEL).worksheet(sheet_name)
        except:
            sheet = client.open(NOMBRE_EXCEL).add_worksheet(title=sheet_name, rows=1000, cols=3)
            sheet.append_row(["FECHA", "EQUIPO", "GRUPO"])

        if request.method == 'GET':
            mes_anio = request.args.get('mes_anio') # e.g. "05/2026"
            all_v = sheet.get_all_values()
            data = {}
            if len(all_v) > 1:
                for row in all_v[1:]:
                    if len(row) >= 3 and mes_anio in row[0]:
                        data[row[0]] = {"equipo": row[1], "grupo": row[2]}
            return jsonify(data)

        datos = request.json
        all_v = sheet.get_all_values()
        fila_idx = -1
        for i, row in enumerate(all_v):
            if len(row) >= 1 and row[0] == datos['fecha']:
                fila_idx = i + 1
                break
        
        if fila_idx != -1:
            sheet.update(f'B{fila_idx}:C{fila_idx}', [[datos['equipo'], datos['grupo']]], value_input_option='USER_ENTERED')
        else:
            sheet.append_row([datos['fecha'], datos['equipo'], datos['grupo']])
            
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@deportivo_bp.route('/api/metodologia', methods=['GET', 'POST'])
def api_metodologia():
    import json
    client = current_app.gs_client
    NOMBRE_EXCEL = current_app.gs_name
    sheet_name = "METODOLOGIA_CATEGORIA"
    try:
        try:
            sheet = client.open(NOMBRE_EXCEL).worksheet(sheet_name)
        except Exception:
            sheet = client.open(NOMBRE_EXCEL).add_worksheet(title=sheet_name, rows=200, cols=4)
            sheet.append_row(["CATEGORIA", "BANNER", "TECNICO_JSON", "TACTICO_JSON"])

        if request.method == 'GET':
            categoria = (request.args.get('categoria') or '').strip().upper()
            if not categoria:
                return jsonify({"status": "error", "message": "Falta la categoría."}), 400
            all_v = sheet.get_all_values()
            for row in all_v[1:]:
                if len(row) > 0 and row[0].strip().upper() == categoria:
                    try:
                        tecnico = json.loads(row[2]) if len(row) > 2 and row[2] else []
                    except (json.JSONDecodeError, IndexError):
                        tecnico = []
                    try:
                        tactico = json.loads(row[3]) if len(row) > 3 and row[3] else []
                    except (json.JSONDecodeError, IndexError):
                        tactico = []
                    return jsonify({
                        "categoria": categoria,
                        "banner": row[1] if len(row) > 1 and row[1] else categoria,
                        "tecnico": tecnico,
                        "tactico": tactico
                    })
            return jsonify({"categoria": categoria, "banner": categoria, "tecnico": [{}], "tactico": [{}]})

        datos = request.json or {}
        categoria = (datos.get('categoria') or '').strip().upper()
        if not categoria:
            return jsonify({"status": "error", "message": "Falta la categoría."}), 400
        banner = datos.get('banner') or categoria
        tecnico_json = json.dumps(datos.get('tecnico') or [])
        tactico_json = json.dumps(datos.get('tactico') or [])

        all_v = sheet.get_all_values()
        for i, row in enumerate(all_v[1:], start=2):
            if len(row) > 0 and row[0].strip().upper() == categoria:
                sheet.update(f'A{i}:D{i}', [[categoria, banner, tecnico_json, tactico_json]], value_input_option='USER_ENTERED')
                return jsonify({"status": "success"})
        sheet.append_row([categoria, banner, tecnico_json, tactico_json])
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error api_metodologia: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@deportivo_bp.route('/direccion_deportiva')
def direccion_deportiva():
    client = current_app.gs_client
    NOMBRE_EXCEL = current_app.gs_name
    
    usuario = session.get('usuario')
    if not usuario:
        return redirect('/')

    perms = session.get('permisos', {})
    if perms.get('D.DEPORTIVA') != 'SI' and perms.get('ENTRENAMIENTOS') != 'SI' and usuario.lower() != 'admin':
        return "Acceso denegado", 403
    tiene_ddep = perms.get('D.DEPORTIVA') == 'SI' or usuario.lower() == 'admin'

    try:
        # 1. Obtener lista de TODOS los equipos desde la pestaña EQUIPO (fuente de verdad)
        equipos = []
        try:
            sheet_eq = client.open(NOMBRE_EXCEL).worksheet("EQUIPO")
            rows_eq = sheet_eq.get_all_values()
            if rows_eq:
                h_eq = normalizar_cabeceras_dep(rows_eq[0])
                idx_e = h_eq.index("EQUIPO") if "EQUIPO" in h_eq else 0
                equipos = sorted(list(set(str(r[idx_e]).strip() for r in rows_eq[1:] if len(r) > idx_e and r[idx_e].strip())))
        except: pass

        # 2. Cargar jugadores para la gestión de datos
        sheet_jug = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        all_jug = sheet_jug.get_all_values()
        jugadores = []
        if all_jug:
            h_j = normalizar_cabeceras_dep(all_jug[0])
            i_n = h_j.index("NOMBRE") if "NOMBRE" in h_j else 0
            i_e = h_j.index("EQUIPO") if "EQUIPO" in h_j else 1
            for row in all_jug[1:]:
                if len(row) > max(i_n, i_e):
                    jugadores.append({"NOMBRE": row[i_n].strip(), "EQUIPO": row[i_e].strip()})
        
        equipo_param = request.args.get('equipo')
        if equipo_param:
            session['equipo_defecto'] = equipo_param.strip()
        equipo_activo = session.get('equipo_defecto', '')
        if not equipo_activo and equipos:
            equipo_activo = equipos[0]
            session['equipo_defecto'] = equipo_activo

        return render_template('direccion.html', usuario=usuario, equipos=equipos, jugadores_raw=jugadores, equipo_defecto=equipo_activo, tiene_ddep=tiene_ddep)
    except Exception as e:
        print(f"Error en direccion_deportiva: {e}")
        return str(e), 500

@deportivo_bp.route('/api/objetivos_mensuales', methods=['GET', 'POST'])
def api_objetivos_mensuales():
    client = current_app.gs_client
    NOMBRE_EXCEL = current_app.gs_name
    SHEET_NAME = "OBJ TACTEC"
    
    if request.method == 'GET':
        mes = request.args.get('mes') or request.args.get('fecha')
        res = {}
        try:
            sheet = client.open(NOMBRE_EXCEL).worksheet(SHEET_NAME)
            all_v = sheet.get_all_values()
            for row in all_v[1:]:
                if len(row) >= 2 and str(row[0]).strip() == str(mes).strip(): # mes_final es 01/MM/YYYY
                    eq = row[1].strip()
                    comps_str = row[2].strip() if len(row) > 2 else "" # Columna C: Objetivos Completados
                    tactico = row[3].strip() if len(row) > 3 else "" # Columna D: Objetivos Tácticos Planificados
                    tecnico = row[4].strip() if len(row) > 4 else "" # Columna E: Objetivos Técnicos Planificados
                    comps = [c.strip() for c in comps_str.split(',') if c.strip()]
                    res[eq] = {
                        "tactico": tactico,
                        "tecnico": tecnico,
                        "completados": [str(c).strip() for c in comps if str(c).strip()]
                    }
        except: pass
        return jsonify(res)

    # POST: Guardar objetivos de múltiples equipos para un mes determinado
    data = request.json
    mes = data.get('mes') or data.get('fecha')
    
    # Solo el admin puede modificar la planificación o progreso
    if session.get('usuario') != 'admin':
        return jsonify({"status": "error", "message": "Permiso denegado: Se requieren privilegios de administrador"}), 403

    objetivos_por_equipo = data.get('objetivos', {})

    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet(SHEET_NAME)
    except:
        spreadsheet = client.open(NOMBRE_EXCEL)
        sheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows="1000", cols="5")
        headers = ["FECHA", "EQUIPO", "OBJETIVOS COMPLETADOS", "OBJETIVOS TACTICOS PLANIFICADOS", "OBJETIVOS TECNICOS PLANIFICADOS"]
        sheet.append_row(headers)

    all_v = sheet.get_all_values()

    for equipo, objs in objetivos_por_equipo.items():
        tactico_str = objs.get("tactico", "").strip()
        tecnico_str = objs.get("tecnico", "").strip()
        # La lista de 'completados' no se guarda aquí, ya que se gestiona en /save_all para la marcación diaria.
        # Si se envía en el POST, se ignora para la escritura en esta ruta.
        
        # Asegurar formato de fecha DD/MM/YYYY
        mes_final = str(mes).strip()
        if '-' in mes_final and len(mes_final) <= 7:
            y, m = mes_final.split('-')
            mes_final = f"01/{m}/{y}"

        idx = -1
        for i, row in enumerate(all_v):
            if i > 0 and len(row) >= 2 and str(row[0]).strip() == mes_final and str(row[1]).strip().upper() == str(equipo).strip().upper():
                idx = i + 1
                break

        if idx != -1:
            # Actualizamos Columnas D y E (índices 4, 5) para los objetivos planificados
            rango = f"D{idx}:E{idx}"
            sheet.update(values=[[tactico_str, tecnico_str]], range_name=rango, value_input_option='USER_ENTERED')
        else:
            # Nueva fila: Fecha completa, Equipo, (vacío para Completados), Tácticos, Técnicos
            sheet.append_row([mes_final, equipo, "", tactico_str, tecnico_str])

    return jsonify({"status": "success"})

@deportivo_bp.route('/api/objetivos_movil', methods=['GET'])
def api_objetivos_movil():
    """Objetivos del mes para la vista móvil: metodología primero (misma fuente que web), luego OBJ TACTEC."""
    client = current_app.gs_client
    NOMBRE_EXCEL = current_app.gs_name
    equipo = request.args.get('equipo', '').strip()
    mes = request.args.get('mes', '').strip()  # YYYY-MM
    if not equipo or not mes:
        return jsonify({"tactico": "", "tecnico": ""})
    # Misma lógica que la vista web: metodología tiene prioridad
    tactico, tecnico = obtener_tactico_tecnico_metodologia(client, NOMBRE_EXCEL, equipo, mes)
    # Fallback a OBJ TACTEC si no hay nada en metodología
    if not tactico and not tecnico:
        try:
            target_y, target_m = mes.split('-')
            sheet = client.open(NOMBRE_EXCEL).worksheet("OBJ TACTEC")
            for row in sheet.get_all_values()[1:]:
                if len(row) < 2: continue
                f = row[0].strip()
                if '/' not in f: continue
                d_r, m_r, y_r = [p.zfill(2) if p.isdigit() else p for p in f.split('/')]
                if m_r == target_m and y_r == target_y and row[1].strip().upper() == equipo.upper():
                    tactico = tactico or (row[3].strip() if len(row) > 3 else "")
                    tecnico = tecnico or (row[4].strip() if len(row) > 4 else "")
        except:
            pass
    return jsonify({"tactico": tactico, "tecnico": tecnico})

@deportivo_bp.route('/api/revision_objetivos')
def api_revision_objetivos():
    """Para el popup de REVISIÓN OBJETIVOS en Dirección Deportiva: devuelve, para un
    equipo y mes dados, los objetivos TÁCTICO/TÉCNICO (desde Metodología) día a día
    junto con qué días ya están marcados como completados."""
    client = current_app.gs_client
    NOMBRE_EXCEL = current_app.gs_name

    equipo = (request.args.get('equipo') or '').strip()
    mes_actual = (request.args.get('mes') or '').strip()
    if not equipo or not mes_actual:
        return jsonify({"status": "error", "message": "Falta equipo o mes."}), 400

    try:
        anio, mes = map(int, mes_actual.split('-'))
        ultimo_dia = calendar.monthrange(anio, mes)[1]
    except Exception:
        return jsonify({"status": "error", "message": "Mes inválido."}), 400

    tactico_str, tecnico_str = obtener_tactico_tecnico_metodologia(client, NOMBRE_EXCEL, equipo, mes_actual)

    completados = []
    try:
        sheet_obj = client.open(NOMBRE_EXCEL).worksheet("OBJ TACTEC")
        all_objs = sheet_obj.get_all_values()
        target_y, target_m = mes_actual.split('-')
        for row in all_objs[1:]:
            if len(row) < 3: continue
            fecha_row = row[0].strip()
            if '/' not in fecha_row: continue
            d, m, y = [p.zfill(2) if p.isdigit() else p for p in fecha_row.split('/')]
            if m == target_m and y == target_y and row[1].strip().upper() == equipo.upper():
                if row[2].strip():
                    for o in row[2].split(','):
                        if o.strip():
                            completados.append(f"{int(d)}-{o.strip()}")
    except Exception as e:
        print(f"Error cargando completados para revisión de objetivos: {e}")

    fotos_subidas = []
    try:
        UPLOAD_FOLDER = current_app.config.get('UPLOAD_FOLDER', os.path.join(os.getcwd(), 'static', 'uploads'))
        if os.path.exists(UPLOAD_FOLDER):
            clave_equipo = sanitizar_equipo_archivo(equipo)
            prefijo = f"entreno_{clave_equipo}_"
            mes_s  = f"{mes:02d}"
            anio_s = str(anio)
            for _f in os.listdir(UPLOAD_FOLDER):
                if not _f.startswith(prefijo): continue
                rest = _f[len(prefijo):].rsplit('.', 1)[0]
                p = rest.split('-')
                if len(p) == 3 and p[1] == mes_s and p[2] == anio_s:
                    fotos_subidas.append(str(int(p[0])))
    except Exception as e:
        print(f"Error listando fotos para revisión de objetivos: {e}")

    return jsonify({
        "tactico": tactico_str,
        "tecnico": tecnico_str,
        "completados": completados,
        "dias": ultimo_dia,
        "fotos_subidas": fotos_subidas
    })

@deportivo_bp.route('/api/ejercicios_semanales', methods=['GET', 'POST'])
def api_ejercicios_semanales():
    client = current_app.gs_client
    NOMBRE_EXCEL = current_app.gs_name
    SHEET_NAME = "EJERCICIOS"
    try:
        try:
            sheet = client.open(NOMBRE_EXCEL).worksheet(SHEET_NAME)
        except:
            sheet = client.open(NOMBRE_EXCEL).add_worksheet(title=SHEET_NAME, rows=1000, cols=6)
            sheet.append_row(["SEMANA", "EQUIPOS", "CATEGORIA", "TITULO", "DESCRIPCION", "URL"])

        if request.method == 'GET':
            semana = request.args.get('semana') # Format DD/MM/YYYY
            semana_norm = normalizar_fecha_sheet(semana)
            all_v = sheet.get_all_values()
            res = []
            for row in all_v[1:]:
                # Comparamos fechas normalizadas
                if len(row) >= 6 and normalizar_fecha_sheet(row[0]) == semana_norm:
                    res.append({
                        "equipos": row[1],
                        "categoria": row[2],
                        "titulo": row[3],
                        "descripcion": row[4],
                        "url": row[5]
                    })
            return jsonify(res)

        if request.method == 'DELETE':
            data = request.json
            semana = data.get('semana')
            categoria = str(data.get('categoria'))
            semana_norm = normalizar_fecha_sheet(semana)
            all_v = sheet.get_all_values()
            fila_idx = -1
            for i, row in enumerate(all_v):
                if i == 0: continue
                if len(row) >= 3 and normalizar_fecha_sheet(row[0]) == semana_norm and str(row[2]).strip() == str(categoria).strip():
                    fila_idx = i + 1
                    break
            if fila_idx != -1:
                sheet.delete_rows(fila_idx)
                return jsonify({"status": "success"})
            return jsonify({"status": "error", "message": "Ejercicio no encontrado"}), 404

        # POST: Guardar ejercicio
        data = request.json
        semana = data.get('semana')
        equipos_str = ",".join(data.get('equipos', []))
        categoria = str(data.get('categoria'))
        titulo = data.get('titulo')
        descripcion = data.get('descripcion')
        url = data.get('url', '')

        semana_norm = normalizar_fecha_sheet(semana)
        all_v = sheet.get_all_values()
        fila_idx = -1
        # Buscamos si ya existe ese ejercicio para esa semana y categoría
        for i, row in enumerate(all_v):
            if i == 0: continue
            if len(row) >= 3 and normalizar_fecha_sheet(row[0]) == semana_norm and str(row[2]).strip() == str(categoria).strip():
                fila_idx = i + 1
                break
        
        # Aseguramos que los datos se guarden como texto para evitar que Sheets cambie formatos
        nueva_fila = [str(semana), str(equipos_str), str(categoria), str(titulo), str(descripcion), str(url)]
        if fila_idx != -1:
            # Sintaxis universal para gspread (rango, valores)
            sheet.update(f'A{fila_idx}:F{fila_idx}', [nueva_fila], value_input_option='USER_ENTERED')
        else:
            sheet.append_row(nueva_fila)
        
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@deportivo_bp.route('/api/upload_ejercicio', methods=['POST'])
def api_upload_ejercicio():
    try:
        # Usamos la carpeta de subidas configurada en app.py
        upload_folder = current_app.config.get('UPLOAD_FOLDER', os.path.join(os.getcwd(), 'static', 'uploads'))
        if not upload_folder: 
            return jsonify({"status": "error", "message": "Configuración UPLOAD_FOLDER no encontrada"}), 500
        
        os.makedirs(upload_folder, exist_ok=True)

        file = request.files.get('file')
        if not file: return jsonify({"status": "error", "message": "No se recibió ningún archivo"}), 400
        
        filename = f"ejercicio_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
        file.save(os.path.join(upload_folder, filename))
        return jsonify({"status": "success", "url": f"/static/uploads/{filename}"})
    except Exception as e:
        print(f"DEBUG UPLOAD ERROR: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@deportivo_bp.route('/api/kpis_deportivos')
def api_kpis_deportivos():
    try:
        from app import client, NOMBRE_EXCEL, normalizar_cabecera_universal
        from collections import defaultdict
        from datetime import datetime
        from flask import request, session
        import os
        import calendar
        import json

        # Spanish month names mapping
        MONTH_NAMES = {
            1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
            7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
        }
        MONTH_NAMES_SHORT = {
            1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
            7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"
        }

        # 1. Parse parameters
        equipo_filtro = request.args.get('equipo', 'TODOS').strip().upper()
        mes_filtro = request.args.get('mes', 'TODOS').strip()

        all_teams = set()

        # Load teams from EQUIPO worksheet
        try:
            sheet_eq = client.open(NOMBRE_EXCEL).worksheet("EQUIPO")
            eq_vals = sheet_eq.get_all_values()
            if eq_vals:
                headers_eq = [normalizar_cabecera_universal(h) for h in eq_vals[0]]
                idx_eq = -1
                for col_name in ["EQUIPO", "NOMBRE", "EQUIPOS", "CATEGORIA", "GRUPO"]:
                    if col_name in headers_eq:
                        idx_eq = headers_eq.index(col_name)
                        break
                actual_idx = idx_eq if idx_eq != -1 else 0
                start_row = 1 if idx_eq != -1 else 0
                for row in eq_vals[start_row:]:
                    if len(row) > actual_idx and row[actual_idx].strip():
                        all_teams.add(row[actual_idx].strip().upper())
        except Exception as e:
            print("Error loading teams from EQUIPO worksheet:", e)

        # Fallback to JUGADORES sheet for teams
        try:
            sheet_jug_tmp = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
            jug_vals_tmp = sheet_jug_tmp.get_all_values()
            if jug_vals_tmp:
                headers_jug_tmp = [normalizar_cabecera_universal(h) for h in jug_vals_tmp[0]]
                idx_eq_jug_tmp = -1
                for col_name in ["EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS"]:
                    if col_name in headers_jug_tmp:
                        idx_eq_jug_tmp = headers_jug_tmp.index(col_name)
                        break
                if idx_eq_jug_tmp != -1:
                    for row in jug_vals_tmp[1:]:
                        if len(row) > idx_eq_jug_tmp and row[idx_eq_jug_tmp].strip():
                            all_teams.add(row[idx_eq_jug_tmp].strip().upper())
        except Exception as e:
            print("Error loading teams from JUGADORES worksheet fallback:", e)

        # Get data from ASISTENCIAS
        sheet_asis = client.open(NOMBRE_EXCEL).worksheet("ASISTENCIAS")
        asis_data = sheet_asis.get_all_values()

        all_months = set()
        
        # Populate all_teams and all_months from the entire sheet
        if asis_data:
            for row in asis_data[1:]:
                if len(row) > 1 and row[1].strip():
                    all_teams.add(row[1].strip().upper())
                if len(row) > 0 and row[0].strip():
                    partes = row[0].split('/')
                    if len(partes) == 3:
                        try:
                            m = int(partes[1])
                            a = int(partes[2])
                            all_months.add(f"{a}-{m:02d}")
                        except ValueError:
                            pass

        # Guarantee Enero 2026 to Diciembre 2026 are in the dropdown
        for m in range(1, 13):
            all_months.add(f"2026-{m:02d}")

        # Ensure current month is in all_months
        current_m_str = f"{datetime.now().strftime('%Y')}-{datetime.now().strftime('%m')}"
        all_months.add(current_m_str)

        # Filter the team list for KPIs based on equipo_filtro
        if equipo_filtro and equipo_filtro != 'TODOS':
            kpi_teams = [t for t in all_teams if t == equipo_filtro]
        else:
            kpi_teams = sorted(list(all_teams))

        # Target months list
        target_months = []
        if mes_filtro == 'TODOS':
            cur_year = int(datetime.now().strftime('%Y'))
            cur_month = int(datetime.now().strftime('%m'))
            for m in range(1, cur_month + 1):
                target_months.append((cur_year, m))
        elif mes_filtro == 'ACTUAL':
            cur_year = int(datetime.now().strftime('%Y'))
            cur_month = int(datetime.now().strftime('%m'))
            target_months.append((cur_year, cur_month))
        else:
            try:
                parts = mes_filtro.split('-')
                target_months.append((int(parts[0]), int(parts[1])))
            except Exception:
                cur_year = int(datetime.now().strftime('%Y'))
                cur_month = int(datetime.now().strftime('%m'))
                target_months.append((cur_year, cur_month))

        # Parse training days from STAFF worksheet
        def parse_dias_entreno_robust(texto):
            if not texto: return []
            texto_upper = texto.upper()
            res = []
            import re
            match = re.search(r'\(([^)]+)\)', texto_upper)
            if match:
                content = match.group(1)
                chars = content.replace(' ', '').split(',')
                if len(chars) == 1 and len(chars[0]) > 1:
                    chars = list(chars[0])
                for c in chars:
                    c = c.strip()
                    if 'L' in c: res.append(0)
                    if 'M' in c and 'MI' not in c: res.append(1)
                    if 'X' in c: res.append(2)
                    if 'J' in c: res.append(3)
                    if 'V' in c: res.append(4)
                    if 'S' in c: res.append(5)
                    if 'D' in c: res.append(6)
                if res:
                    return sorted(list(set(res)))
            
            words = texto_upper.replace('-', ' ').replace(',', ' ').split()
            mapping = {
                'L':0, 'LU':0, 'LUN':0, 'LUNES':0,
                'M':1, 'MA':1, 'MAR':1, 'MARTES':1,
                'X':2, 'MI':2, 'MIE':2, 'MIER':2, 'MIERCOLES':2, 'MIÉRCOLES':2,
                'J':3, 'JU':3, 'JUE':3, 'JUEVES':3,
                'V':4, 'VI':4, 'VIE':4, 'VIERNES':4,
                'S':5, 'SA':5, 'SAB':5, 'SABADO':5, 'SÁBADO':5,
                'D':6, 'DO':6, 'DOM':6, 'DOMINGO':6
            }
            for w in words:
                w_clean = w.strip()
                if w_clean in mapping:
                    res.append(mapping[w_clean])
            return sorted(list(set(res)))

        staff_days = {}
        try:
            sheet_staff = client.open(NOMBRE_EXCEL).worksheet("STAFF")
            staff_data = sheet_staff.get_all_values()
            if staff_data:
                headers_staff = [normalizar_cabecera_universal(h) for h in staff_data[0]]
                idx_staff_eq = -1
                idx_staff_dias = -1
                for col_name in ["EQUIPO", "CATEGORIA", "GRUPO"]:
                    if col_name in headers_staff:
                        idx_staff_eq = headers_staff.index(col_name)
                        break
                for col_name in ["DIASENTRENAMIENTO", "DIAS_ENTRENAMIENTO", "DIAS"]:
                    if col_name in headers_staff:
                        idx_staff_dias = headers_staff.index(col_name)
                        break
                if idx_staff_eq == -1: idx_staff_eq = 1
                if idx_staff_dias == -1: idx_staff_dias = 3
                
                for row in staff_data[1:]:
                    if len(row) > max(idx_staff_eq, idx_staff_dias):
                        eq_name = row[idx_staff_eq].strip().upper()
                        dias_str = row[idx_staff_dias].strip()
                        if eq_name and dias_str:
                            staff_days[eq_name] = parse_dias_entreno_robust(dias_str)
        except Exception as e:
            print("Error loading STAFF training days:", e)

        # Load manual KPIs (Balones)
        kpi_vals = defaultdict(lambda: defaultdict(lambda: defaultdict(str)))
        try:
            sheet_kpis = client.open(NOMBRE_EXCEL).worksheet("KPIS")
            kpi_data = sheet_kpis.get_all_values()
            for row in kpi_data[1:]:
                if len(row) >= 4:
                    tipo = row[0].strip()
                    ident = row[1].strip().upper()
                    m_str = row[2].strip()
                    val = row[3].strip()
                    kpi_vals[tipo][ident][m_str] = val
        except Exception as e:
            print("Error loading KPIS worksheet:", e)

        # Settle the history of Bajas & Altas from JUGADORES sheet
        sheet_jug = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        jug_data = sheet_jug.get_all_values()
        
        bajas_por_equipo = defaultdict(lambda: defaultdict(list))
        altas_por_equipo = defaultdict(lambda: defaultdict(list))
        
        if jug_data:
            headers_jug = [normalizar_cabecera_universal(h) for h in jug_data[0]]
            idx_jug_nom = headers_jug.index("NOMBRE") if "NOMBRE" in headers_jug else 0
            idx_jug_ape = headers_jug.index("APELLIDO") if "APELLIDO" in headers_jug else -1
            idx_jug_eq = -1
            for col_name in ["EQUIPO", "CATEGORIA", "GRUPO"]:
                if col_name in headers_jug:
                    idx_jug_eq = headers_jug.index(col_name)
                    break
            if idx_jug_eq == -1: idx_jug_eq = 2
            idx_jug_obs = headers_jug.index("OBSERVACIONES") if "OBSERVACIONES" in headers_jug else -1
            
            import re
            for row in jug_data[1:]:
                if len(row) > idx_jug_nom:
                    nombre = row[idx_jug_nom].strip()
                    apellido = row[idx_jug_ape].strip() if (idx_jug_ape != -1 and idx_jug_ape < len(row)) else ""
                    full_name = f"{nombre} {apellido}".strip()
                    
                    eq = row[idx_jug_eq].strip().upper() if (idx_jug_eq != -1 and idx_jug_eq < len(row)) else ""
                    if not eq:
                        continue
                        
                    obs_val = row[idx_jug_obs].strip() if (idx_jug_obs != -1 and idx_jug_obs < len(row)) else ""
                    if obs_val:
                        try:
                            obs_list = json.loads(obs_val)
                            if not isinstance(obs_list, list):
                                obs_list = [obs_val]
                        except Exception:
                            obs_list = [obs_val]
                            
                        for item in obs_list:
                            item_str = str(item).strip()
                            if not item_str:
                                continue
                            
                            dt_event = None
                            match_ef = re.search(r'Efectiva:\s*(\d{4}-\d{2}-\d{2})', item_str)
                            if match_ef:
                                try:
                                    dt_event = datetime.strptime(match_ef.group(1), "%Y-%m-%d")
                                except ValueError:
                                    pass
                            
                            if not dt_event:
                                match_pref = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})', item_str)
                                if match_pref:
                                    try:
                                        d, m, y = int(match_pref.group(1)), int(match_pref.group(2)), int(match_pref.group(3))
                                        dt_event = datetime(y, m, d)
                                    except ValueError:
                                        pass
                                        
                            if not dt_event:
                                continue
                                
                            item_upper = item_str.upper()
                            for (ano_target, mes_target) in target_months:
                                last_d = calendar.monthrange(ano_target, mes_target)[1]
                                limit_date = datetime(ano_target, mes_target, last_d)
                                
                                if dt_event <= limit_date:
                                    motive = item_str
                                    if "):" in item_str:
                                        motive = item_str.split("):", 1)[1].strip()
                                    elif "BAJA:" in item_upper:
                                        motive = item_str.split("BAJA:", 1)[1].strip()
                                    elif "ALTA:" in item_upper:
                                        motive = item_str.split("ALTA:", 1)[1].strip()
                                    elif ":" in item_str:
                                        motive = item_str.split(":", 1)[1].strip()
                                        
                                    if "BAJA" in item_upper:
                                        bajas_por_equipo[(eq, ano_target, mes_target)][full_name].append(f"- {full_name}: {motive}")
                                    elif "ALTA" in item_upper:
                                        altas_por_equipo[(eq, ano_target, mes_target)][full_name].append(f"- {full_name}: {motive}")

        # Initialize KPIs dict
        kpis = {}
        for (ano, mes) in target_months:
            m_str = f"{MONTH_NAMES_SHORT[mes]} {ano}"
            for eq in kpi_teams:
                kpis[(eq, ano, mes)] = {
                    "equipo": eq, 
                    "mes": m_str, 
                    "bajas": "", 
                    "altas": "", 
                    "faltas": 0,
                    "sin_completar_count": 0,
                    "faltas_detalles": {"no": [], "sin_completar": []},
                    "entrenos_hechos": 0, 
                    "entrenos_mes": 0, 
                    "forms_hechos": 0, 
                    "forms_mes": 0, 
                    "balones_inicio": "",
                    "balones_final": "",
                    "balones_dif": ""
                }

        # Calculate training days in this month based on STAFF
        cal_obj = calendar.Calendar()
        for (eq, ano, mes) in kpis:
            days_prog = staff_days.get(eq)
            if not days_prog:
                # Fallback: Mon-Fri
                days_prog = [0, 1, 2, 3, 4]
            
            count = 0
            for d in cal_obj.itermonthdays2(ano, mes):
                if d[0] != 0 and d[1] in days_prog:
                    count += 1
            kpis[(eq, ano, mes)]["entrenos_mes"] = count

        # Count training sessions done (JSON evidence files)
        entrenos_hechos_dict = defaultdict(int)
        data_folder = current_app.config.get('DATA_FOLDER', os.path.join(os.getcwd(), 'static', 'data'))
        sesiones_dir = os.path.join(data_folder, 'sesiones')
        if os.path.exists(sesiones_dir):
            for eq_dir_name in os.listdir(sesiones_dir):
                eq_path = os.path.join(sesiones_dir, eq_dir_name)
                if os.path.isdir(eq_path):
                    eq_norm = eq_dir_name.replace('_', ' ').upper()
                    for fname in os.listdir(eq_path):
                        if fname.startswith("Sesion_") and fname.endswith(".json"):
                            date_part = fname[7:-5]
                            partes = date_part.split('-')
                            if len(partes) == 3:
                                try:
                                    d = int(partes[0])
                                    m = int(partes[1])
                                    y = int(partes[2])
                                    entrenos_hechos_dict[(eq_norm, y, m)] += 1
                                except ValueError:
                                    pass

        for (eq, ano, mes) in kpis:
            kpis[(eq, ano, mes)]["entrenos_hechos"] = entrenos_hechos_dict[(eq, ano, mes)]

        # Fallback for current month from UPLOAD_FOLDER
        cur_year = int(datetime.now().strftime('%Y'))
        cur_month = int(datetime.now().strftime('%m'))
        cur_mes_s  = f"{cur_month:02d}"
        cur_anio_s = str(cur_year)
        equipo_kpi = sanitizar_equipo_archivo(session.get('equipo_defecto', ''))
        dias_con_archivo = set()
        upload_folder = current_app.config.get('UPLOAD_FOLDER', os.path.join(os.getcwd(), 'static', 'uploads'))
        if os.path.exists(upload_folder) and equipo_kpi:
            prefijo_kpi = f"entreno_{equipo_kpi}_"
            for filename in os.listdir(upload_folder):
                if not filename.startswith(prefijo_kpi): continue
                rest = filename[len(prefijo_kpi):].rsplit('.', 1)[0]
                p = rest.split('-')
                if len(p) == 3 and p[1] == cur_mes_s and p[2] == cur_anio_s:
                    try: dias_con_archivo.add(int(p[0]))
                    except ValueError: pass
        uploads_count = len(dias_con_archivo)
        
        for (eq, ano, mes) in kpis:
            if kpis[(eq, ano, mes)]["entrenos_hechos"] == 0 and ano == cur_year and mes == cur_month:
                kpis[(eq, ano, mes)]["entrenos_hechos"] = uploads_count

        # Faltas & Sin Completar
        faltas_no_list = defaultdict(list)
        sin_completar_list = defaultdict(list)
        if asis_data:
            for row in asis_data[1:]:
                if len(row) >= 3:
                    fecha_str = row[0].strip()
                    eq = row[1].strip().upper()
                    nombre = row[2].strip()
                    estado = row[4].strip().upper() if len(row) > 4 else ""
                    
                    partes_fecha = fecha_str.split('/')
                    if len(partes_fecha) == 3:
                        try:
                            m = int(partes_fecha[1])
                            a = int(partes_fecha[2])
                            key = (eq, a, m)
                            if key in kpis:
                                if estado == "NO":
                                    faltas_no_list[key].append({"nombre": nombre, "fecha": fecha_str})
                                elif estado in ["-", ""] or not estado:
                                    sin_completar_list[key].append({"nombre": nombre, "fecha": fecha_str})
                        except ValueError:
                            pass
                            
        for (eq, ano, mes) in kpis:
            key = (eq, ano, mes)
            kpis[key]["faltas"] = len(faltas_no_list[key])
            kpis[key]["sin_completar_count"] = len(sin_completar_list[key])
            kpis[key]["faltas_detalles"] = {
                "no": faltas_no_list[key],
                "sin_completar": sin_completar_list[key]
            }

        # Forms
        for (eq, ano, mes) in kpis:
            saturdays_count = sum(1 for d in cal_obj.itermonthdays2(ano, mes) if d[0] != 0 and d[1] == 5)
            kpis[(eq, ano, mes)]["forms_mes"] = saturdays_count

        try:
            sheet_form = client.open(NOMBRE_EXCEL).worksheet("FORMULARIO_PARTIDOS")
            form_data = sheet_form.get_all_values()
            if form_data:
                headers = [normalizar_cabecera_universal(h) for h in form_data[0]]
                idx_mt = headers.index("MARCATEMPORAL") if "MARCATEMPORAL" in headers else 0
                idx_eq = headers.index("EQUIPO") if "EQUIPO" in headers else 1

                for row in form_data[1:]:
                    if len(row) > max(idx_mt, idx_eq):
                        eq = row[idx_eq].strip().upper()
                        fecha_str = row[idx_mt].strip()
                        date_part = fecha_str.split()[0] if fecha_str else ""
                        partes_fecha = date_part.split('/')
                        if len(partes_fecha) == 3:
                            try:
                                m = int(partes_fecha[1])
                                a = int(partes_fecha[2])
                                key = (eq, a, m)
                                if key in kpis:
                                    kpis[key]["forms_hechos"] += 1
                            except ValueError:
                                pass
        except Exception as e:
            print("Error FORMULARIOS KPI:", e)

        # Balones
        for (eq, ano, mes) in kpis:
            key = (eq, ano, mes)
            cur_mes_str = f"{MONTH_NAMES_SHORT[mes]} {ano}"
            
            if mes == 1:
                prev_m = 12
                prev_a = ano - 1
            else:
                prev_m = mes - 1
                prev_a = ano
            prev_mes_str = f"{MONTH_NAMES_SHORT[prev_m]} {prev_a}"
            
            inicio = kpi_vals["BALONES_INICIO"][eq][cur_mes_str]
            final = kpi_vals["BALONES_FINAL"][eq][cur_mes_str]
            if not inicio:
                inicio = kpi_vals["BALONES_FINAL"][eq][prev_mes_str]
            
            kpis[key]["balones_inicio"] = inicio
            kpis[key]["balones_final"] = final
            
            try:
                ini_val = int(inicio)
                fin_val = int(final)
                diff = fin_val - ini_val
                kpis[key]["balones_dif"] = f"+{diff}" if diff > 0 else str(diff)
            except (ValueError, TypeError):
                kpis[key]["balones_dif"] = ""

        # Set Bajas & Altas text
        for (eq, ano, mes) in kpis:
            key = (eq, ano, mes)
            
            player_bajas = []
            if key in bajas_por_equipo:
                for player, msgs in bajas_por_equipo[key].items():
                    player_bajas.extend(sorted(list(set(msgs))))
            kpis[key]["bajas"] = "\n".join(player_bajas)
            
            player_altas = []
            if key in altas_por_equipo:
                for player, msgs in altas_por_equipo[key].items():
                    player_altas.extend(sorted(list(set(msgs))))
            kpis[key]["altas"] = "\n".join(player_altas)

        # Convert to list and sort by (team, newer month first)
        sorted_keys = sorted(kpis.keys(), key=lambda x: (x[0], -x[1], -x[2]))
        res_list = [kpis[k] for k in sorted_keys]

        # Format months for dropdown
        sorted_months = sorted(list(all_months), reverse=True)
        dropdown_months = []
        for m in sorted_months:
            parts = m.split('-')
            dropdown_months.append({
                "value": m,
                "label": f"{MONTH_NAMES[int(parts[1])]} {parts[0]}"
            })

        return jsonify({
            "kpis": res_list,
            "equipos": sorted(list(all_teams)),
            "meses": dropdown_months
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"kpis": [], "equipos": [], "meses": [], "error": str(e)}), 500

@deportivo_bp.route('/api/kpis_pagos')
def api_kpis_pagos():
    try:
        from app import client, NOMBRE_EXCEL
        from collections import defaultdict
        from datetime import datetime
        import json

        # ── Calcular temporada deportiva ──────────────────────────────────────
        hoy = datetime.now()
        cur_year, cur_month = hoy.year, hoy.month

        # Temporada Sep→Jun: si estamos en Sep-Dic, la temporada empieza este año
        # Si estamos en Ene-Ago, la temporada empieza el año anterior
        if cur_month >= 9:
            season_start_year = cur_year
        else:
            season_start_year = cur_year - 1

        # Generar lista de meses de la temporada hasta el mes actual (inclusive)
        season_months = []  # lista de (año, mes) en orden cronológico
        if cur_month >= 9:
            for m in range(9, cur_month + 1):
                season_months.append((season_start_year, m))
        else:
            for m in range(9, 13):
                season_months.append((season_start_year, m))
            for m in range(1, cur_month + 1):
                season_months.append((season_start_year + 1, m))

        # Conceptos de columnas y mapeo de nombres de mes
        month_labels = {
            1: 'Ene', 2: 'Feb', 3: 'Mar', 4: 'Abr', 5: 'May', 6: 'Jun',
            7: 'Jul', 8: 'Ago', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dic'
        }

        # 1. Cargar configuración financiera
        sheet_config = client.open(NOMBRE_EXCEL).worksheet("CONFIGURACION")
        config_rows = sheet_config.get_all_values()
        inscripciones_config = {}
        formas_pago_config = []
        cuotas_mes_config = {}
        for r in config_rows:
            if len(r) >= 2:
                key = r[0].strip()
                val = r[1].strip()
                if key == "INSCRIPCIONES_EQUIPO":
                    try: inscripciones_config = json.loads(val)
                    except: pass
                elif key == "FORMAS_PAGO":
                    try: formas_pago_config = json.loads(val)
                    except: pass
                elif key == "CUOTAS_MES":
                    try: cuotas_mes_config = json.loads(val)
                    except: pass

        if not formas_pago_config:
            formas_pago_config = [
                {"nombre": "Efectivo", "total": 490, "modalidad": "mensual", "cuota": 49, "meses": 10, "tipo": "Efectivo"},
                {"nombre": "Domiciliación", "total": 490, "modalidad": "mensual", "cuota": 49, "meses": 10, "tipo": "Domiciliación"},
                {"nombre": "Transferencia", "total": 490, "modalidad": "mensual", "cuota": 49, "meses": 10, "tipo": "Transferencia"}
            ]

        # Convert/extract the grid configuration list from FORMAS_PAGO key
        grid_config = []
        if isinstance(formas_pago_config, dict):
            if "tipos" in formas_pago_config:
                grid_config = formas_pago_config["tipos"]
            elif "grid" in formas_pago_config:
                grid_config = formas_pago_config["grid"]
        elif isinstance(formas_pago_config, list):
            if formas_pago_config and "tipo" in formas_pago_config[0]:
                grid_config = formas_pago_config
            else:
                for item in formas_pago_config:
                    tipo = str(item.get("modalidad", "mensual")).upper()
                    forma = str(item.get("nombre", "Efectivo")).upper()
                    importe = float(item.get("cuota", 49.0))
                    meses_count = int(item.get("meses", 10))
                    meses = [9, 10, 11, 12, 1, 2, 3, 4, 5, 6][:meses_count]
                    grid_config.append({
                        "tipo": tipo,
                        "forma": forma,
                        "importe": importe,
                        "meses": meses
                    })

        # 2. Cargar jugadores activos
        sheet_jug = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        jug_rows = sheet_jug.get_all_values()
        if not jug_rows:
            return jsonify({"jugadores": [], "columnas": [], "equipos": []})
            
        headers_jug = [h.strip().upper() for h in jug_rows[0]]
        idx_rec_fecha = headers_jug.index("RECORDATORIO_FECHA") if "RECORDATORIO_FECHA" in headers_jug else -1
        idx_rec_comentario = headers_jug.index("RECORDATORIO_COMENTARIO") if "RECORDATORIO_COMENTARIO" in headers_jug else -1
        idx_rec1 = headers_jug.index("RECORDATORIO_1") if "RECORDATORIO_1" in headers_jug else -1
        idx_nom = headers_jug.index("NOMBRE") if "NOMBRE" in headers_jug else 0
        idx_ape = headers_jug.index("APELLIDO") if "APELLIDO" in headers_jug else -1
        idx_eq = headers_jug.index("EQUIPO") if "EQUIPO" in headers_jug else -1
        idx_baja = headers_jug.index("BAJA_DESDE") if "BAJA_DESDE" in headers_jug else -1
        idx_alta = headers_jug.index("ALTA_DESDE") if "ALTA_DESDE" in headers_jug else -1
        idx_forma = headers_jug.index("FORMA_PAGO") if "FORMA_PAGO" in headers_jug else -1
        idx_tipo = headers_jug.index("TIPO PAGO") if "TIPO PAGO" in headers_jug else -1

        def parse_date_simple(d_str):
            if not d_str: return None
            s = str(d_str).strip().replace('-', '/')
            for fmt in ("%d/%m/%Y", "%Y/%m/%d", "%d/%m/%y"):
                try: return datetime.strptime(s, fmt)
                except: continue
            return None

        players = []
        for row in jug_rows[1:]:
            if len(row) <= max(idx_nom, idx_eq): continue
            nom = row[idx_nom].strip()
            eq = row[idx_eq].strip()
            if not nom or not eq: continue
            
            ape = row[idx_ape].strip() if idx_ape != -1 and len(row) > idx_ape else ""
            baja_str = row[idx_baja].strip() if idx_baja != -1 and len(row) > idx_baja else ""
            alta_str = row[idx_alta].strip() if idx_alta != -1 and len(row) > idx_alta else ""
            forma = row[idx_forma].strip() if idx_forma != -1 and len(row) > idx_forma else ""
            tipo = row[idx_tipo].strip() if idx_tipo != -1 and len(row) > idx_tipo else ""
            
            rec_fecha_val = ""
            if idx_rec_fecha != -1 and len(row) > idx_rec_fecha and row[idx_rec_fecha].strip():
                rec_fecha_val = row[idx_rec_fecha].strip()
            elif idx_rec1 != -1 and len(row) > idx_rec1:
                rec_fecha_val = row[idx_rec1].strip()
                
            rec_comentario_val = ""
            if idx_rec_comentario != -1 and len(row) > idx_rec_comentario:
                rec_comentario_val = row[idx_rec_comentario].strip()
            
            players.append({
                "nombre": nom,
                "apellido": ape,
                "equipo": eq,
                "alta_dt": parse_date_simple(alta_str),
                "baja_dt": parse_date_simple(baja_str),
                "forma_pago": forma or "BIANIAL",
                "tipo_pago": tipo,
                "recordatorio_fecha": rec_fecha_val,
                "recordatorio_comentario": rec_comentario_val
            })

        # 3. Cargar teléfonos de la hoja DATOS JUGADORES
        phones = {}
        try:
            sheet_datos = client.open(NOMBRE_EXCEL).worksheet("DATOS JUGADORES")
            datos_rows = sheet_datos.get_all_records()
            for d in datos_rows:
                d_clean = {k.strip().upper(): str(v).strip() for k, v in d.items()}
                nombre = d_clean.get("JUGADOR_NOMBRE", "")
                equipo = d_clean.get("JUGADOR_EQUIPO_LETRA", d_clean.get("JUGADOR_EQUIPO", ""))
                movil = d_clean.get("JUGADOR_TELEFONO_MOVIL", "")
                if nombre and movil:
                    phones[(nombre.lower(), equipo.lower())] = movil
        except Exception as e_phone:
            print(f"Aviso al cargar teléfonos: {e_phone}")

        # 4. Cargar pagos
        sheet_pagos = client.open(NOMBRE_EXCEL).worksheet("PAGOS JUGADORES")
        pagos_data = sheet_pagos.get_all_values()
        
        headers_pag = [str(h).upper().strip().replace(' ', '_') for h in pagos_data[0]] if pagos_data else []
        idx_p_eq = headers_pag.index('EQUIPO') if 'EQUIPO' in headers_pag else 1
        idx_p_jug = headers_pag.index('JUGADOR') if 'JUGADOR' in headers_pag else 2
        idx_p_con = headers_pag.index('CONCEPTO') if 'CONCEPTO' in headers_pag else 5
        idx_p_pag = headers_pag.index('PAGADO') if 'PAGADO' in headers_pag else 6
        idx_p_esp = headers_pag.index('ESPERADO') if 'ESPERADO' in headers_pag else 7

        def parse_float(s):
            try: return float(str(s).replace('€', '').replace(',', '.').strip() or '0')
            except: return 0.0

        def normalizar_concepto(c):
            c = str(c).strip().upper().replace(' ', '_')
            if c.isdigit(): return c.zfill(2)
            if 'ROPA' in c or c in ('PACK_ROPA', 'ROPA', 'EQUIPACION', 'EQUIPACIÓN'): return 'PACK_ROPA'
            if 'INSCRI' in c: return 'INSCRIPCION'
            return c

        raw_payments = []
        if len(pagos_data) > 1:
            for row in pagos_data[1:]:
                if len(row) <= max(idx_p_eq, idx_p_jug, idx_p_con, idx_p_pag, idx_p_esp): continue
                raw_payments.append({
                    "equipo": row[idx_p_eq].strip(),
                    "jugador": row[idx_p_jug].strip(),
                    "concepto": normalizar_concepto(row[idx_p_con]),
                    "pagado": parse_float(row[idx_p_pag]),
                    "esperado": parse_float(row[idx_p_esp])
                })

        # 5. Calcular deudas para cada jugador
        result_jugadores = []
        all_equipos = set()

        for p in players:
            eq = p['equipo']
            all_equipos.add(eq)
            
            # Buscar configuración de forma y tipo de pago
            forma_p = p['forma_pago'].strip().upper()
            tipo_p = p['tipo_pago'].strip().upper() if p['tipo_pago'] else "MENSUAL"
            config_match = None
            for c in grid_config:
                if str(c.get('tipo', '')).strip().upper() == tipo_p:
                    config_match = c
                    break
            # Fallback: match by forma if tipo not found
            if not config_match:
                for c in grid_config:
                    if str(c.get('tipo', '')).strip().upper() == forma_p:
                        config_match = c
                        break
            
            expected_concepts = {}
            
            # A. Matrícula / Inscripción
            valIns = float(inscripciones_config.get(eq, 10.0))
            expected_concepts['INSCRIPCION'] = valIns
            
            # B. Mensualidades de la temporada
            for (y, m) in season_months:
                is_active = True
                if p['alta_dt']:
                    if (y < p['alta_dt'].year) or (y == p['alta_dt'].year and m < p['alta_dt'].month):
                        is_active = False
                if p['baja_dt']:
                    if (y > p['baja_dt'].year) or (y == p['baja_dt'].year and m >= p['baja_dt'].month):
                        is_active = False
                        
                if is_active:
                    concept = str(m).zfill(2)
                    if config_match:
                        config_meses = [int(x) for x in config_match.get('meses', [])]
                        if not config_meses:
                            config_meses = [9, 10, 11, 12, 1, 2, 3, 4, 5, 6]
                        if m in config_meses:
                            expected_concepts[concept] = float(config_match.get('importe', 0.0))
                    else:
                        if forma_p == 'MENSUAL' and m not in (7, 8):
                            expected_concepts[concept] = 49.0

            # C. Pack Ropa
            ropa_key = f"{eq}-PACK_ROPA"
            valRopa = float(cuotas_mes_config.get(ropa_key, 0.0))
            if valRopa > 0:
                expected_concepts['PACK_ROPA'] = valRopa
                
            # D. Filtrar y agrupar pagos del jugador
            paid_concepts = {}
            p_payments = [pay for pay in raw_payments if pay['equipo'].upper() == eq.upper() and pay['jugador'].lower() == p['nombre'].lower()]
            
            # Mapeo de meses de grupo al primer mes del grupo
            month_to_group_start = {}
            if config_match and 'grupos' in config_match:
                for grp in config_match['grupos']:
                    if isinstance(grp, list) and len(grp) > 0:
                        start_m = grp[0]
                        for m in grp:
                            month_to_group_start[m] = start_m

            for pay in p_payments:
                concept = pay['concepto']
                pagado = pay['pagado']
                if concept.isdigit():
                    m_num = int(concept)
                    if m_num in month_to_group_start:
                        concept = str(month_to_group_start[m_num]).zfill(2)
                    else:
                        concept = str(m_num).zfill(2)
                paid_concepts[concept] = paid_concepts.get(concept, 0.0) + pagado

            # E. Comparar y calcular deudas
            conceptos_deuda = []
            total_deuda = 0.0
            
            # Orden de conceptos: Inscripción -> Mensualidades cronológicas -> Ropa
            concept_order = ['INSCRIPCION']
            for (y, m) in season_months:
                concept = str(m).zfill(2)
                if concept not in concept_order:
                    concept_order.append(concept)
            concept_order.append('PACK_ROPA')
            
            processed = set()
            for con in concept_order:
                if con in expected_concepts and con not in processed:
                    processed.add(con)
                    esp = expected_concepts[con]
                    pag = paid_concepts.get(con, 0.0)
                    deu = max(esp - pag, 0.0)
                    
                    if deu > 0.1: # Tolerancia para flotantes
                        month_num = None
                        if con == 'INSCRIPCION':
                            lbl = 'Inscr.'
                            tipo_pago_deuda = 'inscripcion'
                        elif con == 'PACK_ROPA':
                            lbl = 'Ropa'
                            tipo_pago_deuda = 'ropa'
                        elif con.isdigit():
                            tipo_pago_deuda = 'mensual'
                            month_num = int(con)
                            lbl = month_labels.get(month_num, con)
                        else:
                            lbl = con
                            tipo_pago_deuda = 'mensual'

                        lbl_deu = int(deu) if deu.is_integer() else round(deu, 2)
                        entry = {
                            'label': lbl,
                            'deuda': lbl_deu,
                            'tipo': tipo_pago_deuda
                        }
                        if month_num is not None:
                            entry['month_num'] = month_num
                        conceptos_deuda.append(entry)
                        total_deuda += deu

            if total_deuda > 0:
                # Buscar teléfono
                p_key = (p['nombre'].lower(), eq.lower())
                movil = phones.get(p_key, "")
                if not movil:
                    for (name_k, eq_k), tel in phones.items():
                        if eq_k == eq.lower() and p['nombre'].lower().startswith(name_k):
                            movil = tel
                            break
                            
                # Detalle texto simple para previsualizaciones o logs
                debts_text = [f"{c['label']} ({c['deuda']}€)" for c in conceptos_deuda]
                
                result_jugadores.append({
                    'equipo': eq,
                    'nombre': f"{p['nombre']} {p['apellido']}".strip(),
                    'primer_nombre': p['nombre'],
                    'conceptos_deuda': conceptos_deuda,
                    'detalle_text': " · ".join(debts_text),
                    'total_deuda': round(total_deuda, 2),
                    'forma_pago': p['forma_pago'],
                    'tipo_pago': p['tipo_pago'],
                    'recordatorio_fecha': p['recordatorio_fecha'],
                    'recordatorio_comentario': p['recordatorio_comentario'],
                    'telefono': movil
                })

        # Ordenar por equipo -> nombre
        result_jugadores.sort(key=lambda x: (x['equipo'].upper(), x['nombre'].upper()))

        season_months_out = [{'year': y, 'month': m, 'label': month_labels.get(m, str(m))} for (y, m) in season_months]
        return jsonify({
            'jugadores': result_jugadores,
            'equipos': sorted(list(all_equipos)),
            'season_months': season_months_out
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error api_kpis_pagos: {e}")
        return jsonify({'jugadores': [], 'equipos': [], 'error': str(e)}), 500


@deportivo_bp.route('/api/guardar_recordatorio_jugador', methods=['POST'])
def api_guardar_recordatorio_jugador():
    try:
        from app import client, NOMBRE_EXCEL
        data = request.json
        eq = data.get('equipo', '').strip()
        nom = data.get('nombre', '').strip()
        fecha = data.get('fecha', '').strip()
        comentario = data.get('comentario', '').strip()
        
        sheet = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        all_data = sheet.get_all_values()
        if not all_data:
            return jsonify({"status": "error", "message": "Hoja vacía"}), 404
            
        headers = [h.strip().upper() for h in all_data[0]]
        
        if "RECORDATORIO_FECHA" not in headers:
            col_idx_fecha = len(headers) + 1
            sheet.update_cell(1, col_idx_fecha, "RECORDATORIO_FECHA")
            headers.append("RECORDATORIO_FECHA")
        else:
            col_idx_fecha = headers.index("RECORDATORIO_FECHA") + 1
            
        if "RECORDATORIO_COMENTARIO" not in headers:
            col_idx_com = len(headers) + 1
            sheet.update_cell(1, col_idx_com, "RECORDATORIO_COMENTARIO")
            headers.append("RECORDATORIO_COMENTARIO")
        else:
            col_idx_com = headers.index("RECORDATORIO_COMENTARIO") + 1
            
        idx_nom = headers.index("NOMBRE") if "NOMBRE" in headers else 0
        idx_eq = headers.index("EQUIPO") if "EQUIPO" in headers else 2
        
        fila_idx = -1
        for i, fila in enumerate(all_data):
            if len(fila) > max(idx_nom, idx_eq):
                nombre_celda = fila[idx_nom].strip().lower()
                if nombre_celda == nom.lower() and fila[idx_eq].strip().lower() == eq.lower():
                    fila_idx = i + 1
                    break
                    
        if fila_idx != -1:
            sheet.update_cell(fila_idx, col_idx_fecha, fecha)
            sheet.update_cell(fila_idx, col_idx_com, comentario)
            return jsonify({"status": "success"})
        else:
            return jsonify({"status": "error", "message": "Jugador no encontrado"}), 404
    except Exception as e:
        print(f"Error api_guardar_recordatorio_jugador: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500



def _normalizar_nombre_kpi(s):
    import unicodedata
    s = str(s or '').strip().lower()
    s = unicodedata.normalize('NFD', s)
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn')

@deportivo_bp.route('/api/kpis_estadisticas')
def api_kpis_estadisticas():
    from app import client, NOMBRE_EXCEL
    try:
        # 1. Roster oficial (JUGADORES: NOMBRE, EQUIPO)
        sheet_jug = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        jug_rows = sheet_jug.get_all_values()
        headers_jug = [h.strip().upper() for h in jug_rows[0]] if jug_rows else []
        idx_nom = headers_jug.index("NOMBRE") if "NOMBRE" in headers_jug else 0
        idx_eq = headers_jug.index("EQUIPO") if "EQUIPO" in headers_jug else 1

        roster = []
        for row in jug_rows[1:]:
            if len(row) <= max(idx_nom, idx_eq):
                continue
            nom = row[idx_nom].strip()
            eq = row[idx_eq].strip()
            if nom and eq:
                roster.append({"nombre": nom, "equipo": eq})

        # 2. Fichas registradas (DATOS JUGADORES: colegio + domicilio)
        fichas = []
        try:
            sheet_datos = client.open(NOMBRE_EXCEL).worksheet("DATOS JUGADORES")
            for d in sheet_datos.get_all_records():
                d_clean = {str(k).strip().upper(): str(v).strip() for k, v in d.items()}
                fichas.append({
                    "nombre": d_clean.get("JUGADOR_NOMBRE", ""),
                    "equipo": d_clean.get("JUGADOR_EQUIPO_LETRA", ""),
                    "colegio": d_clean.get("JUGADOR_COLEGIO", ""),
                    "domicilio": d_clean.get("JUGADOR_DOMICILIO_CP", "")
                })
        except Exception as e_datos:
            print(f"Aviso al cargar DATOS JUGADORES para estadísticas: {e_datos}")

        # 3. Cruce roster <-> ficha por nombre normalizado + equipo compatible
        resultado = []
        for p in roster:
            n_of = _normalizar_nombre_kpi(p["nombre"])
            e_of = _normalizar_nombre_kpi(p["equipo"])
            ficha_match = None
            for f in fichas:
                if _normalizar_nombre_kpi(f["nombre"]) == n_of:
                    e_reg = _normalizar_nombre_kpi(f["equipo"])
                    if e_of in e_reg or e_reg in e_of:
                        ficha_match = f
                        break
            resultado.append({
                "nombre": p["nombre"],
                "equipo": p["equipo"],
                "colegio": (ficha_match["colegio"] if ficha_match and ficha_match["colegio"] else None),
                "domicilio": (ficha_match["domicilio"] if ficha_match and ficha_match["domicilio"] else None)
            })

        equipos = sorted(set(p["equipo"] for p in resultado))
        return jsonify({"jugadores": resultado, "equipos": equipos})
    except Exception as e:
        print(f"Error en api_kpis_estadisticas: {e}")
        return jsonify({"jugadores": [], "equipos": [], "error": str(e)}), 500

@deportivo_bp.route('/api/kpis_rrss')
def api_kpis_rrss():
    try:
        from app import client, NOMBRE_EXCEL
        from datetime import datetime, timedelta
        
        # Generar últimos 3 meses como columnas fijas para simplificar
        meses = []
        for i in range(3):
            # Restar i meses (aproximado usando 30 dias)
            d = datetime.now() - timedelta(days=30*i)
            mes_str = f"{d.strftime('%b').capitalize()} {d.strftime('%Y')}"
            meses.insert(0, mes_str)
            
        valores = {}
        try:
            sheet_kpis = client.open(NOMBRE_EXCEL).worksheet("KPIS")
            for row in sheet_kpis.get_all_values()[1:]:
                if len(row) >= 4 and row[0] == "RRSS" and row[1] == "INSTAGRAM":
                    valores[row[2]] = row[3]
        except:
            pass
            
        variaciones = {}
        prev_val = 0
        for m in meses:
            curr_val_str = valores.get(m, "0")
            try:
                curr_val = int(curr_val_str)
            except:
                curr_val = 0
            
            diff = curr_val - prev_val if prev_val != 0 else 0
            variaciones[m] = f"+{diff}" if diff > 0 else str(diff)
            prev_val = curr_val

        return jsonify({
            "meses": meses,
            "valores": valores,
            "variaciones": variaciones
        })
    except Exception as e:
        print(f"Error api_kpis_rrss: {e}")
        return jsonify({"meses": [], "valores": {}, "variaciones": {}}), 500

@deportivo_bp.route('/api/kpis_manual', methods=['POST'])
def api_kpis_manual():
    try:
        from app import client, NOMBRE_EXCEL
        data = request.json
        tipo = data.get('tipo') # BALONES o RRSS
        identificador = data.get('identificador') # Equipo o INSTAGRAM
        mes = data.get('mes')
        valor = str(data.get('valor'))
        
        try:
            sheet_kpis = client.open(NOMBRE_EXCEL).worksheet("KPIS")
        except:
            sheet_kpis = client.open(NOMBRE_EXCEL).add_worksheet(title="KPIS", rows="100", cols="10")
            sheet_kpis.update('A1', [["TIPO", "IDENTIFICADOR", "MES", "VALOR"]])
            
        all_v = sheet_kpis.get_all_values()
        fila_idx = -1
        
        for i, row in enumerate(all_v):
            if i == 0: continue
            if len(row) >= 3 and row[0] == tipo and row[1] == identificador and row[2] == mes:
                fila_idx = i + 1
                break
                
        if fila_idx != -1:
            sheet_kpis.update_cell(fila_idx, 4, valor)
        else:
            sheet_kpis.append_row([tipo, identificador, mes, valor])
            
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error api_kpis_manual: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@deportivo_bp.route('/clasificaciones')
def clasificaciones():
    usuario = session.get('usuario')
    if not usuario:
        return redirect('/')
    
    perms = session.get('permisos', {})
    if perms.get('ENTRENAMIENTOS') != 'SI' and perms.get('D.DEPORTIVA') != 'SI' and usuario.lower() != 'admin':
        return "Acceso denegado", 403

    from competicion_scraper import load_cached_data, intentar_autosync_background

    intentar_autosync_background()
    data_rffm = load_cached_data()

    client = current_app.gs_client
    NOMBRE_EXCEL = current_app.gs_name
    
    equipos = []
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("EQUIPO")
        all_v = sheet.get_all_values()
        if all_v:
            headers = normalizar_cabeceras_dep(all_v[0])
            idx_eq = headers.index("EQUIPO") if "EQUIPO" in headers else 0
            equipos = sorted(list(set(str(row[idx_eq]).strip() for row in all_v[1:] if len(row) > idx_eq and str(row[idx_eq]).strip())))
    except Exception as e:
        print(f"Error cargando equipos en clasificaciones: {e}")

    equipo_activo = session.get('equipo_defecto', '')
    if not equipo_activo and equipos:
        equipo_activo = equipos[0]
        session['equipo_defecto'] = equipo_activo

    return render_template('deportivo/clasificaciones.html',
                           usuario=usuario,
                           equipo_defecto=equipo_activo,
                           equipos=equipos,
                           data_rffm=data_rffm)

@deportivo_bp.route('/clasificaciones/pdf')
def clasificaciones_pdf():
    usuario = session.get('usuario')
    if not usuario:
        return redirect('/')

    from competicion_scraper import load_cached_data, _rival_coincide, intentar_autosync_background
    from datetime import datetime, timedelta

    intentar_autosync_background()
    cache = load_cached_data()
    equipos_pdf = []

    def _norm(s):
        return (s or '').replace('"', '').replace("'", '').strip().upper()

    # Filtro por fin de semana (?fecha=YYYY-MM-DD)
    fecha_param = request.args.get('fecha', '').strip()
    finde_fechas = set()
    finde_label = ''
    if fecha_param:
        try:
            d = datetime.strptime(fecha_param, '%Y-%m-%d')
            dow = d.weekday()
            sab = d - timedelta(days=max(0, dow - 5)) if dow >= 5 else d + timedelta(days=5 - dow)
            dom = sab + timedelta(days=1)
            for dd in (sab, dom):
                finde_fechas.add(dd.strftime('%d-%m-%Y'))
            finde_label = sab.strftime('%d/%m/%Y') + ' – ' + dom.strftime('%d/%m/%Y')
        except Exception:
            fecha_param = ''

    for eq in cache.get('equipos', []):
        calendario = eq.get('calendario', [])
        calendario_grupo = eq.get('calendario_grupo', [])
        clasificacion = eq.get('clasificacion', [])

        # Si hay filtro: solo incluir equipos con partido ese finde
        partido_finde = None
        if finde_fechas:
            for p in calendario:
                if p.get('fecha') in finde_fechas:
                    partido_finde = p
                    break
            if not partido_finde:
                continue

        # Último partido jugado
        ultimo = None
        if partido_finde and partido_finde.get('estado'):
            ultimo = partido_finde
        if not ultimo:
            for p in reversed(calendario):
                if p.get('estado'):
                    ultimo = p
                    break

        # Rival a destacar en amarillo: el de la PRÓXIMA jornada (la siguiente a la que se muestra),
        # no el de la jornada mostrada (ese ya se ve resaltado en verde como nuestro partido).
        ref_jornada = (partido_finde or {}).get('jornada', '')
        proximo = None
        if ref_jornada:
            idx_ref = next((i for i, p in enumerate(calendario) if p.get('jornada') == ref_jornada), None)
            if idx_ref is not None and idx_ref + 1 < len(calendario):
                proximo = calendario[idx_ref + 1]
        if not proximo:
            for p in calendario:
                if not p.get('estado') and not p.get('resultado'):
                    proximo = p
                    break

        rival_proximo = proximo.get('rival', '') if proximo else ''
        rival_norm = _norm(rival_proximo)

        # Nuestra fila y la del rival en clasificación
        idx_nuestro = None
        idx_rival = None
        for i, fila in enumerate(clasificacion):
            nombre = _norm(fila.get('equipo', ''))
            if 'FUENTELARREYNA' in nombre:
                idx_nuestro = i
            if rival_norm and _rival_coincide(rival_norm, nombre) and idx_rival is None:
                idx_rival = i

        # Jornada a mostrar
        ref = partido_finde or ultimo
        jornada_label = ref.get('jornada', '') if ref else ''
        fecha_label = ref.get('fecha', '') if ref else ''

        # Partidos del grupo en esa jornada
        sin_datos_grupo = not calendario_grupo
        partidos_jornada = []
        if calendario_grupo and jornada_label:
            partidos_jornada = [p for p in calendario_grupo if p.get('jornada') == jornada_label]

        # Fallback: si no hay calendario_grupo, mostrar solo el nuestro
        if not partidos_jornada and ref:
            loc = ref.get('rival', '') if not ref.get('es_local') else 'CLUB FUENTELARREYNA'
            vis = ref.get('rival', '') if ref.get('es_local') else 'CLUB FUENTELARREYNA'
            r = ref.get('resultado', '')
            if r:
                partidos_jornada = [{'local': loc, 'visitante': vis, 'resultado': r,
                                     'jornada': jornada_label, 'fecha': fecha_label}]

        equipos_pdf.append({
            'nombre': eq.get('nombre', ''),
            'categoria': eq.get('categoria', ''),
            'letra': eq.get('letra', ''),
            'sin_datos_grupo': sin_datos_grupo,
            'clasificacion': clasificacion,
            'idx_nuestro': idx_nuestro,
            'idx_rival': idx_rival,
            'rival_proximo': rival_proximo,
            'ultimo': ultimo,
            'proximo': proximo,
            'jornada_label': jornada_label,
            'fecha_label': fecha_label,
            'partidos_jornada': partidos_jornada,
        })

    orden_cat = {'AFICIONADO': 0, 'JUVENIL': 1, 'CADETE': 2, 'INFANTIL': 3,
                 'ALEVIN': 4, 'ALEVIN F7': 5, 'BENJAMIN': 6, 'PREBENJAMIN': 7}
    equipos_pdf.sort(key=lambda e: (orden_cat.get(e['categoria'], 99), e['letra']))

    return render_template('deportivo/clasificaciones_pdf.html',
                           usuario=usuario,
                           equipos=equipos_pdf,
                           last_updated=cache.get('last_updated', ''),
                           fecha_param=fecha_param,
                           finde_label=finde_label)


@deportivo_bp.route('/api/sincronizar_rffm', methods=['POST'])
def api_sincronizar_rffm():
    usuario = session.get('usuario')
    if not usuario:
        return jsonify({"status": "error", "message": "No session"}), 401
        
    data = request.json or {}
    username_rffm = data.get('usuario_rffm', '').strip()
    password_rffm = data.get('password_rffm', '').strip()
    recordar = bool(data.get('recordar', False))

    if not username_rffm or not password_rffm:
        return jsonify({"status": "error", "message": "Faltan credenciales"}), 400

    from competicion_scraper import sync_rffm, guardar_credenciales_rffm

    success, message = sync_rffm(username_rffm, password_rffm)
    if success:
        if recordar:
            guardar_credenciales_rffm(username_rffm, password_rffm)
        return jsonify({"status": "success", "message": message})
    else:
        return jsonify({"status": "error", "message": message}), 400


@deportivo_bp.route('/api/sincronizar_rffm/status', methods=['GET'])
def api_sincronizar_rffm_status():
    usuario = session.get('usuario')
    if not usuario:
        return jsonify({"status": "error", "message": "No session"}), 401

    from competicion_scraper import auto_sync_status
    return jsonify(auto_sync_status())


@deportivo_bp.route('/api/informes/colores_rivales', methods=['GET'])
def api_informes_colores_get():
    from competicion_scraper import load_kit_clash_report
    data = load_kit_clash_report()
    if not data:
        return jsonify({"status": "empty", "entries": []})
    return jsonify(data)


@deportivo_bp.route('/api/informes/colores_rivales', methods=['POST'])
def api_informes_colores_generar():
    usuario = session.get('usuario')
    if not usuario:
        return jsonify({"status": "error", "message": "No session"}), 401

    data = request.json or {}
    username_rffm = data.get('usuario_rffm', '').strip()
    password_rffm = data.get('password_rffm', '').strip()

    if not username_rffm or not password_rffm:
        return jsonify({"status": "error", "message": "Faltan credenciales"}), 400

    from competicion_scraper import build_kit_clash_report

    ok, message, report = build_kit_clash_report(username_rffm, password_rffm)
    if ok:
        return jsonify({**report, "message": message})
    else:
        return jsonify({"status": "error", "message": message}), 400


@deportivo_bp.route('/api/convocatorias', methods=['GET'])
def api_convocatorias_get():
    from competicion_scraper import load_convocatorias_report
    data = load_convocatorias_report()
    if not data:
        return jsonify({"status": "empty", "entries": []})
    return jsonify(data)


@deportivo_bp.route('/api/convocatorias', methods=['POST'])
def api_convocatorias_generar():
    usuario = session.get('usuario')
    if not usuario:
        return jsonify({"status": "error", "message": "No session"}), 401

    data = request.json or {}
    username_rffm = data.get('usuario_rffm', '').strip()
    password_rffm = data.get('password_rffm', '').strip()

    if not username_rffm or not password_rffm:
        return jsonify({"status": "error", "message": "Faltan credenciales"}), 400

    from competicion_scraper import build_convocatorias_report

    ok, message, report = build_convocatorias_report(username_rffm, password_rffm)
    if ok:
        return jsonify({**report, "message": message})
    else:
        return jsonify({"status": "error", "message": message}), 400


@deportivo_bp.route('/api/convocatorias/anotaciones', methods=['GET'])
def api_convocatorias_anotaciones_get():
    from competicion_scraper import load_convocatoria_anotaciones
    return jsonify(load_convocatoria_anotaciones())


@deportivo_bp.route('/api/convocatorias/anotaciones', methods=['POST'])
def api_convocatorias_anotaciones_post():
    if not session.get('usuario'):
        return jsonify({"status": "error", "message": "No session"}), 401
    data = request.json or {}
    clave = (data.get('clave') or '').strip()
    campos = data.get('campos') or {}
    if not clave:
        return jsonify({"status": "error", "message": "Falta clave"}), 400
    from competicion_scraper import guardar_convocatoria_anotacion
    actual = guardar_convocatoria_anotacion(clave, campos)
    return jsonify({"status": "success", "data": actual})


@deportivo_bp.route('/api/informes/checklist_equipacion', methods=['GET'])
def api_checklist_equipacion_get():
    from competicion_scraper import load_checklist_equipacion
    return jsonify(load_checklist_equipacion())


@deportivo_bp.route('/api/informes/checklist_equipacion', methods=['POST'])
def api_checklist_equipacion_guardar():
    if not session.get('usuario'):
        return jsonify({"status": "error", "message": "No session"}), 401

    data = request.json or {}
    match_key = data.get('match_key', '').strip()
    campos = data.get('campos') or {}
    if not match_key:
        return jsonify({"status": "error", "message": "Falta match_key"}), 400

    from competicion_scraper import guardar_checklist_equipacion
    actual = guardar_checklist_equipacion(match_key, campos)
    return jsonify({"status": "success", "data": actual})


def _get_or_crear_sheet_horarios_temporada():
    client = current_app.gs_client
    NOMBRE_EXCEL = current_app.gs_name
    try:
        return client.open(NOMBRE_EXCEL).worksheet("HORARIOS_TEMPORADA")
    except Exception:
        sheet = client.open(NOMBRE_EXCEL).add_worksheet(title="HORARIOS_TEMPORADA", rows="1000", cols="5")
        sheet.append_row(["GRUPO", "HORARIO", "COLUMNA", "TEXTO", "COLOR"])
        return sheet


@deportivo_bp.route('/api/horarios_temporada', methods=['GET'])
def api_horarios_temporada_get():
    sheet = _get_or_crear_sheet_horarios_temporada()
    all_v = sheet.get_all_values()
    datos = {}
    for row in all_v[1:]:
        if len(row) < 3 or not row[0].strip():
            continue
        grupo, horario, columna = row[0].strip(), row[1].strip(), row[2].strip()
        texto = row[3].strip() if len(row) > 3 else ''
        color = row[4].strip() if len(row) > 4 else ''
        datos.setdefault(grupo, {}).setdefault(horario, {})[columna] = {"texto": texto, "color": color}
    return jsonify({"status": "success", "datos": datos})


@deportivo_bp.route('/api/horarios_temporada', methods=['POST'])
def api_horarios_temporada_guardar():
    if not session.get('usuario'):
        return jsonify({"status": "error", "message": "No autenticado"}), 401

    data = request.json or {}
    grupo = (data.get('grupo') or '').strip()
    horario = (data.get('horario') or '').strip()
    columna = (data.get('columna') or '').strip()
    texto = data.get('texto')
    color = data.get('color')
    if not grupo or not horario or not columna:
        return jsonify({"status": "error", "message": "Faltan datos (grupo, horario o columna)."}), 400

    sheet = _get_or_crear_sheet_horarios_temporada()
    all_v = sheet.get_all_values()
    fila_idx = -1
    for i, row in enumerate(all_v[1:], start=2):
        if len(row) > 2 and row[0].strip() == grupo and row[1].strip() == horario and row[2].strip() == columna:
            fila_idx = i
            break

    if fila_idx != -1:
        fila_actual = all_v[fila_idx - 1]
        texto_final = texto if texto is not None else (fila_actual[3].strip() if len(fila_actual) > 3 else '')
        color_final = color if color is not None else (fila_actual[4].strip() if len(fila_actual) > 4 else '')
        sheet.update(f'A{fila_idx}:E{fila_idx}', [[grupo, horario, columna, texto_final, color_final]], value_input_option='USER_ENTERED')
    else:
        sheet.append_row([grupo, horario, columna, texto or '', color or ''])

    return jsonify({"status": "success"})


# --- OBJ SEMANALES ---

@deportivo_bp.route('/api/obj_semanales/resumen')
def api_obj_semanales_resumen():
    usuario = session.get('usuario')
    if not usuario:
        return jsonify({"status": "error"}), 401
    from competicion_scraper import construir_obj_semanales
    try:
        datos = construir_obj_semanales()
        return jsonify({"status": "ok", "partidos": datos})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@deportivo_bp.route('/api/criterios_objetivo', methods=['GET', 'POST'])
def api_criterios_objetivo():
    usuario = session.get('usuario')
    if not usuario:
        return jsonify({"status": "error"}), 401
    from competicion_scraper import load_criterios_objetivo, guardar_criterios_objetivo, CRITERIOS_OBJETIVO_DESCRIPCIONES
    if request.method == 'POST':
        if usuario != 'admin':
            return jsonify({"status": "error", "message": "Solo el admin puede modificar los criterios"}), 403
        try:
            criterios = guardar_criterios_objetivo(request.json or {})
            return jsonify({"status": "ok", "criterios": criterios, "descripciones": CRITERIOS_OBJETIVO_DESCRIPCIONES})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "ok", "criterios": load_criterios_objetivo(), "descripciones": CRITERIOS_OBJETIVO_DESCRIPCIONES})


@deportivo_bp.route('/api/shield_proxy')
def api_shield_proxy():
    """Proxy para servir escudos de la RFFM localmente desde static/shields/."""
    from competicion_scraper import download_shield, get_session as rffm_session, SHIELDS_DIR
    import os
    from flask import send_file, abort
    
    url = request.args.get('url', '').strip()
    club_id = request.args.get('id', '').strip()
    
    if not url and not club_id:
        abort(404)
    
    # Extraer club_id de la URL si no se dio
    if not club_id and url:
        from competicion_scraper import extract_shield_id
        club_id = extract_shield_id(url)
    
    if not club_id:
        abort(404)
    
    local_path = os.path.join(SHIELDS_DIR, f"club_{club_id}.png")
    
    # Si ya existe localmente, servirlo directamente
    if os.path.exists(local_path) and os.path.getsize(local_path) > 500:
        return send_file(local_path, mimetype='image/png')
    
    # Intentar descargar si tenemos una URL
    if url:
        # Intentar con sesión anónima primero (algunos escudos son públicos)
        try:
            sess = rffm_session()
            r = sess.get(url, timeout=8)
            ct = r.headers.get("Content-Type", "")
            if r.status_code == 200 and ("image" in ct or r.content[:4] in [b'\x89PNG', b'\xff\xd8\xff\xe0', b'GIF8']):
                with open(local_path, 'wb') as f:
                    f.write(r.content)
                return send_file(local_path, mimetype='image/png')
        except Exception:
            pass
    
    abort(404)


@deportivo_bp.route('/api/descargar_escudos', methods=['POST'])
def api_descargar_escudos():
    """Descarga y cachea todos los escudos visibles en los datos actuales usando credenciales RFFM."""
    usuario = session.get('usuario')
    if not usuario:
        return jsonify({"status": "error", "message": "No session"}), 401
    
    from competicion_scraper import (load_cached_data, download_shield, 
                                      get_session as rffm_session, SHIELDS_DIR)
    import re
    
    data = request.json or {}
    username_rffm = data.get('usuario_rffm', '').strip()
    password_rffm = data.get('password_rffm', '').strip()
    
    # Crear sesión autenticada si tenemos credenciales
    auth_sess = None
    if username_rffm and password_rffm:
        auth_sess = rffm_session()
        try:
            auth_sess.post(
                "https://intranet.ffmadrid.es/nfg/NLogin",
                data={"NUser": username_rffm, "NPass": password_rffm, "LoginAjax": "1"},
                timeout=10
            )
        except Exception:
            auth_sess = None
    
    # Recopilar todas las URLs de escudos de los datos cacheados
    rffm_data = load_cached_data()
    shield_urls = set()
    
    for eq in rffm_data.get('equipos', []):
        # Escudos de clasificación
        for item in eq.get('clasificacion', []):
            if item.get('shield'):
                shield_urls.add(item['shield'])
        # Escudos del último partido
        up = eq.get('ultimo_partido', {}) or {}
        if up.get('local_shield'): shield_urls.add(up['local_shield'])
        if up.get('visitante_shield'): shield_urls.add(up['visitante_shield'])
        # Escudos del calendario
        for p in eq.get('calendario', []):
            if p.get('rival_shield'): shield_urls.add(p['rival_shield'])
    
    descargados = 0
    fallidos = 0
    
    for url in shield_urls:
        if not url or url.startswith('/static/'):
            continue
        m = re.search(r'club_(\d+)', url)
        if not m:
            continue
        club_id = m.group(1)
        result = download_shield(url, club_id, auth_session=auth_sess)
        if result:
            descargados += 1
        else:
            fallidos += 1
    
    return jsonify({
        "status": "success",
        "descargados": descargados,
        "fallidos": fallidos,
        "total": len(shield_urls)
    })


# --- INFORME MENSUAL ---

def _nt_dep(s):
    for a, b in [('á','a'),('é','e'),('í','i'),('ó','o'),('ú','u'),('ü','u'),
                 ('Á','a'),('É','e'),('Í','i'),('Ó','o'),('Ú','u')]:
        s = s.replace(a, b)
    return s.strip().lower()

_DIA_MAP_DEP = {'lunes':0,'martes':1,'miercoles':2,'jueves':3,'viernes':4,'sabado':5,'domingo':6}


@deportivo_bp.route('/api/informe_mensual', methods=['GET'])
def api_informe_mensual():
    if not session.get('usuario'):
        return jsonify({"status": "error"}), 401

    import calendar as _cal_mod
    import glob as _glob_mod
    from datetime import date as _date

    mes_str = request.args.get('mes', '')
    try:
        anio, mes_int = int(mes_str[:4]), int(mes_str[5:7])
    except Exception:
        hoy = _date.today()
        anio, mes_int = hoy.year, hoy.month

    nm = _cal_mod.monthrange(anio, mes_int)[1]
    saturdays = sum(1 for d in range(1, nm + 1) if _cal_mod.weekday(anio, mes_int, d) == 5)

    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'equipos_config.json')
    equipos_data = []
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            equipos_data = json.load(f).get('equipos', [])
    except Exception:
        pass

    client = current_app.gs_client
    NOMBRE_EXCEL = current_app.gs_name

    # ASISTENCIAS
    datos_asis = {}
    try:
        ws_asis = client.open(NOMBRE_EXCEL).worksheet("ASISTENCIAS")
        all_asis = ws_asis.get_all_values()
        for row in (all_asis[1:] if all_asis else []):
            if len(row) < 5: continue
            eq_n = _nt_dep(row[1])
            p = row[0].strip().split('/')
            if len(p) < 3: continue
            try:
                fd, fm, fy = int(p[0]), int(p[1]), int(p[2])
            except Exception: continue
            if fm != mes_int or fy != anio: continue
            estado = row[4].strip() if len(row) > 4 else ''
            val    = row[5].strip() if len(row) > 5 else ''
            datos_asis.setdefault(eq_n, {}).setdefault(f"{fd:02d}/{fm:02d}/{fy}", []).append((estado, val))
    except Exception: pass

    # FORMULARIO_PARTIDOS
    forms_eq = {}
    try:
        ws_form = client.open(NOMBRE_EXCEL).worksheet('FORMULARIO_PARTIDOS')
        all_form = ws_form.get_all_values()
        if all_form and len(all_form) > 1:
            hdrs = [h.strip().upper().replace(' ', '') for h in all_form[0]]
            idx_eq = next((i for i, h in enumerate(hdrs) if 'EQUIPO' in h), 1)
            idx_fe = next((i for i, h in enumerate(hdrs) if 'MARCA' in h or 'TEMPORAL' in h), 0)
            for row in all_form[1:]:
                if len(row) <= max(idx_eq, idx_fe): continue
                pf = row[idx_fe].strip().split()[0].split('/') if row[idx_fe].strip() else []
                try:
                    if len(pf) >= 3 and int(pf[1]) == mes_int and int(pf[2]) == anio:
                        k = row[idx_eq].strip().upper()
                        forms_eq[k] = forms_eq.get(k, 0) + 1
                except Exception: pass
    except Exception: pass

    # INFORMES_SEMANALES (audios)
    audios_eq = {}
    try:
        ws_inf = _get_or_crear_sheet_informes_semanales()
        for row in (ws_inf.get_all_values()[1:] or []):
            if len(row) < 3: continue
            p = row[0].strip().split('/')
            try:
                if int(p[1]) == mes_int and int(p[2]) == anio and row[2].strip().upper() == 'SI':
                    k = row[1].strip().upper()
                    audios_eq[k] = audios_eq.get(k, 0) + 1
            except Exception: pass
    except Exception: pass

    # OBJ CUMPLIDOS
    obj_eq = {}
    try:
        from competicion_scraper import construir_obj_semanales
        for p in construir_obj_semanales():
            if p.get('cumplido') is None: continue
            fp = (p.get('fecha') or '').split('-')
            try:
                if int(fp[1]) != mes_int or int(fp[2]) != anio: continue
            except Exception: continue
            cat   = (p.get('categoria') or '').strip().upper()
            letra = (p.get('letra') or '').strip().upper()
            key   = f"{cat} {letra}".strip() if letra else cat
            s = obj_eq.setdefault(key, {'cumplidos': 0, 'total': 0})
            s['total'] += 1
            if p.get('cumplido') is True:
                s['cumplidos'] += 1
    except Exception: pass

    def _vacio(v): return not v or v.strip() == '-'

    result = []
    for eq in equipos_data:
        nombre = eq['nombre']
        eq_n   = _nt_dep(nombre)
        eq_up  = nombre.upper()

        BASE_DIR = os.path.dirname(os.path.abspath(__file__))

        # Días laborables del mes (lo que muestra el calendario: Lun-Vie)
        total_entrenos = sum(1 for d in range(1, nm + 1) if _cal_mod.weekday(anio, mes_int, d) < 5)

        # Días configurados del equipo (para asistencias)
        dias_semana = [_DIA_MAP_DEP.get(_nt_dep(d), -1) for d in eq.get('dias', [])]
        dias_semana = [d for d in dias_semana if d >= 0]
        fechas_entreno = [f"{d:02d}/{mes_int:02d}/{anio}" for d in range(1, nm + 1)
                          if _cal_mod.weekday(anio, mes_int, d) in dias_semana]

        datos_eq = datos_asis.get(eq_n, {})
        asist_hechas = sum(
            1 for f in fechas_entreno
            if f in datos_eq and not any(_vacio(e) or (e.upper() == 'SI' and _vacio(v)) for e, v in datos_eq[f])
        )

        equipo_folder  = eq_up.replace(' ', '_')
        upload_folder  = current_app.config.get('UPLOAD_FOLDER', os.path.join(BASE_DIR, 'static', 'uploads'))
        mes_s2  = f"{mes_int:02d}"
        anio_s2 = str(anio)
        prefijo_up = f"entreno_{equipo_folder}_"

        # Contar desde UPLOAD_FOLDER (misma fuente que el calendario, formato DD-MM-YYYY)
        dias_subidos = set()
        if os.path.exists(upload_folder):
            for _uf in os.listdir(upload_folder):
                if not _uf.startswith(prefijo_up): continue
                rest = _uf[len(prefijo_up):].rsplit('.', 1)[0]  # "DD-MM-YYYY"
                p = rest.split('-')
                if len(p) == 3 and p[1] == mes_s2 and p[2] == anio_s2:
                    dias_subidos.add(p[0])

        # Contar también desde sesiones/ (sesiones creadas online con Sesion_DD-MM-YYYY.jpg)
        sesiones_dir = os.path.join(BASE_DIR, 'static', 'data', 'sesiones', equipo_folder)
        if os.path.exists(sesiones_dir):
            for fname in os.listdir(sesiones_dir):
                try:
                    base = fname.rsplit('.', 1)[0]  # "Sesion_DD-MM-YYYY"
                    partes = base.replace('Sesion_', '').split('-')
                    if len(partes) >= 3:
                        fd, fm, fy = int(partes[0]), int(partes[1]), int(partes[2])
                        if fm == mes_int and fy == anio:
                            dias_subidos.add(f"{fd:02d}")
                except Exception:
                    pass

        sesiones_subidas = len(dias_subidos)

        balones = 0
        for bpath in _glob_mod.glob(os.path.join(BASE_DIR, 'static', 'data', f'balones_{nombre}_*.json')):
            try:
                with open(bpath, 'r', encoding='utf-8') as fp:
                    bdata = json.load(fp)
                for _, vals in bdata.items():
                    ini = vals.get('inicio', [])
                    fin = vals.get('final', [])
                    if not ini or not fin: continue
                    ts = ini[-1].get('timestamp', '')
                    try:
                        if int(ts.split('/')[1]) != mes_int: continue
                    except Exception: continue
                    try:
                        q = int(ini[-1].get('cantidad', 0)) - int(fin[-1].get('cantidad', 0))
                        if q > 0: balones += q
                    except Exception: pass
            except Exception: pass

        obj_stats = obj_eq.get(eq_up, {'cumplidos': 0, 'total': 0})

        result.append({
            'nombre':        nombre,
            'asistencias':   {'hechas': asist_hechas,    'total': len(fechas_entreno)},
            'entrenamientos':{'subidos': sesiones_subidas,'total': total_entrenos},
            'formularios':   {'hechos': forms_eq.get(eq_up, 0), 'total': saturdays},
            'audios':        {'enviados': audios_eq.get(eq_up, 0), 'total': saturdays},
            'balones':       balones,
            'obj':           obj_stats,
        })

    mes_label = ['ENE','FEB','MAR','ABR','MAY','JUN','JUL','AGO','SEP','OCT','NOV','DIC'][mes_int - 1]
    return jsonify({
        'status': 'ok',
        'equipos': result,
        'mes_label': f"{mes_label} {anio}",
        'mes_iso': f"{anio}-{mes_int:02d}",
    })


# --- INFORMES SEMANALES ---

def _get_or_crear_sheet_informes_semanales():
    client = current_app.gs_client
    NOMBRE_EXCEL = current_app.gs_name
    try:
        return client.open(NOMBRE_EXCEL).worksheet("INFORMES_SEMANALES")
    except Exception:
        sheet = client.open(NOMBRE_EXCEL).add_worksheet(title="INFORMES_SEMANALES", rows="2000", cols="3")
        sheet.append_row(["FECHA_FINDE", "EQUIPO", "AUDIO_ENVIADO"])
        return sheet


@deportivo_bp.route('/api/informes_semanales', methods=['GET'])
def api_informes_semanales_get():
    if not session.get('usuario'):
        return jsonify({"status": "error"}), 401

    from datetime import date as _date
    finde_str = request.args.get('finde', '')
    try:
        finde_dt = datetime.strptime(finde_str, '%Y-%m-%d').date()
    except Exception:
        today = _date.today()
        days_since_sat = (today.weekday() - 5) % 7
        finde_dt = today - timedelta(days=days_since_sat)

    sat = finde_dt
    sun = finde_dt + timedelta(days=1)
    sat_str = sat.strftime('%d/%m/%Y')

    equipos = []
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'equipos_config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            equipos = [e['nombre'] for e in json.load(f).get('equipos', [])]
    except Exception:
        pass

    teams_with_game = set()
    try:
        from competicion_scraper import load_convocatorias_report
        conv_data = load_convocatorias_report()
        for entry in (conv_data or {}).get('entries', []):
            fecha_raw = entry.get('fecha', '')
            parts = fecha_raw.split('-')
            if len(parts) == 3:
                ed, em, ey = int(parts[0]), int(parts[1]), int(parts[2])
                if _date(ey, em, ed) in (sat, sun):
                    cat = (entry.get('categoria') or '').strip().upper()
                    letra = (entry.get('letra') or '').strip().upper()
                    teams_with_game.add(f"{cat} {letra}".strip() if letra else cat)
    except Exception:
        pass

    teams_formulario = set()
    try:
        client = current_app.gs_client
        NOMBRE_EXCEL = current_app.gs_name
        ws_form = client.open(NOMBRE_EXCEL).worksheet('FORMULARIO_PARTIDOS')
        all_form = ws_form.get_all_values()
        if all_form and len(all_form) > 1:
            hdrs = [h.strip().upper().replace(' ', '') for h in all_form[0]]
            idx_eq = next((i for i, h in enumerate(hdrs) if 'EQUIPO' in h), 1)
            idx_fe = next((i for i, h in enumerate(hdrs) if 'MARCA' in h or 'TEMPORAL' in h), 0)
            for row in all_form[1:]:
                if len(row) <= max(idx_eq, idx_fe): continue
                fecha_raw = row[idx_fe].strip()
                if not fecha_raw: continue
                pf = fecha_raw.split()[0].split('/')
                try:
                    fd, fm, fy = int(pf[0]), int(pf[1]), int(pf[2])
                    if fy < 100: fy += 2000
                    if _date(fy, fm, fd) in (sat, sun):
                        teams_formulario.add(row[idx_eq].strip().upper())
                except Exception:
                    pass
    except Exception:
        pass

    audio_data = {}
    try:
        ws_inf = _get_or_crear_sheet_informes_semanales()
        all_inf = ws_inf.get_all_values()
        for row in (all_inf[1:] if all_inf else []):
            if len(row) >= 3 and row[0].strip() == sat_str:
                audio_data[row[1].strip().upper()] = row[2].strip().upper()
    except Exception:
        pass

    result = []
    for eq in equipos:
        eq_up = eq.upper()
        if teams_with_game:
            tiene_partido = any(eq_up == tg or tg.startswith(eq_up) or eq_up.startswith(tg) for tg in teams_with_game)
        else:
            tiene_partido = True
        result.append({
            'nombre': eq,
            'tiene_partido': tiene_partido,
            'formulario': eq_up in teams_formulario,
            'audio': audio_data.get(eq_up)
        })

    return jsonify({
        'status': 'ok',
        'equipos': result,
        'finde': sat_str,
        'finde_iso': sat.strftime('%Y-%m-%d')
    })


@deportivo_bp.route('/api/informes_semanales/audio', methods=['POST'])
def api_informes_semanales_audio_post():
    if not session.get('usuario'):
        return jsonify({"status": "error"}), 401

    data = request.json or {}
    finde_iso = (data.get('finde') or '').strip()
    equipo = (data.get('equipo') or '').strip().upper()
    valor = (data.get('valor') or '').strip().upper()

    if not finde_iso or not equipo or valor not in ('SI', 'NO'):
        return jsonify({"status": "error", "message": "Datos inválidos"}), 400

    try:
        finde_dt = datetime.strptime(finde_iso, '%Y-%m-%d').date()
        sat_str = finde_dt.strftime('%d/%m/%Y')
    except Exception:
        return jsonify({"status": "error", "message": "Fecha inválida"}), 400

    try:
        ws = _get_or_crear_sheet_informes_semanales()
        all_data = ws.get_all_values()
        for i, row in enumerate(all_data[1:] if all_data else [], start=2):
            if len(row) >= 2 and row[0].strip() == sat_str and row[1].strip().upper() == equipo:
                ws.update_cell(i, 3, valor)
                return jsonify({"status": "ok"})
        ws.append_row([sat_str, equipo, valor])
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@deportivo_bp.route('/api/material', methods=['GET', 'POST'])
def api_material():
    if not session.get('usuario'):
        return jsonify({}), 401
    client = current_app.gs_client
    nombre_excel = current_app.gs_name

    if request.method == 'GET':
        try:
            try:
                ws = client.open(nombre_excel).worksheet('MATERIAL')
                data = ws.get_all_values()
                if not data:
                    return jsonify({'headers': ['EQUIPO', 'MOCHILAS'], 'rows': []})
                return jsonify({'headers': data[0], 'rows': data[1:]})
            except Exception:
                # Hoja no existe — inicializar con equipos del sistema
                try:
                    sheet_eq = client.open(nombre_excel).worksheet("EQUIPO")
                    rows_eq = sheet_eq.get_all_values()
                    hdrs = normalizar_cabeceras_dep(rows_eq[0]) if rows_eq else []
                    idx_e = hdrs.index("EQUIPO") if "EQUIPO" in hdrs else 0
                    equipos = sorted(list(set(str(r[idx_e]).strip() for r in rows_eq[1:] if len(r) > idx_e and r[idx_e].strip())))
                except Exception:
                    equipos = []
                ws = client.open(nombre_excel).add_worksheet(title='MATERIAL', rows=100, cols=20)
                initial = [['EQUIPO', 'MOCHILAS']] + [[eq, ''] for eq in equipos]
                if initial:
                    ws.update('A1', initial)
                return jsonify({'headers': ['EQUIPO', 'MOCHILAS'], 'rows': [[eq, ''] for eq in equipos]})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    elif request.method == 'POST':
        data = request.json or {}
        headers = data.get('headers', [])
        rows = data.get('rows', [])
        try:
            try:
                ws = client.open(nombre_excel).worksheet('MATERIAL')
            except Exception:
                ws = client.open(nombre_excel).add_worksheet(title='MATERIAL', rows=100, cols=20)
            ws.clear()
            all_data = [headers] + rows
            if all_data:
                ws.update('A1', all_data)
            return jsonify({'status': 'ok'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
