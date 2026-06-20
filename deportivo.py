import os
import json
import base64
import calendar
import unicodedata
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

    # 3. Fotos subidas
    if os.path.exists(UPLOAD_FOLDER):
        archivos_reales = [f for f in os.listdir(UPLOAD_FOLDER) if f.startswith(f"entreno_{usuario}_")]
    else:
        archivos_reales = []
    fotos_subidas = [f.split('_')[-1].split('.')[0] for f in archivos_reales]

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
                           dias_transcurridos=hoy.day if es_mes_actual else (30 if (anio < hoy.year or (anio == hoy.year and mes < hoy.month)) else 0),
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

@deportivo_bp.route('/direccion_deportiva')
def direccion_deportiva():
    client = current_app.gs_client
    NOMBRE_EXCEL = current_app.gs_name
    
    usuario = session.get('usuario')
    if not usuario:
        return redirect('/')

    if session.get('permisos', {}).get('D.DEPORTIVA') != 'SI':
        return "Acceso denegado", 403
    
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
        
        return render_template('direccion.html', usuario=usuario, equipos=equipos, jugadores_raw=jugadores)
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
        usuario_sesion = session.get('usuario', 'admin')
        dias_con_archivo = set()
        upload_folder = current_app.config.get('UPLOAD_FOLDER', os.path.join(os.getcwd(), 'static', 'uploads'))
        if os.path.exists(upload_folder):
            for filename in os.listdir(upload_folder):
                if filename.startswith(f"entreno_{usuario_sesion}_"):
                    parts = filename.split('_')
                    if len(parts) >= 3:
                        try:
                            dia_num = int(parts[-1].split('.')[0])
                            dias_con_archivo.add(dia_num)
                        except ValueError:
                            pass
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
        
        sheet_pagos = client.open(NOMBRE_EXCEL).worksheet("PAGOS JUGADORES")
        pagos_data = sheet_pagos.get_all_values()
        
        if not pagos_data: return jsonify({})
        headers = [str(h).upper().strip().replace(' ', '_') for h in pagos_data[0]]
        
        idx_eq = headers.index('EQUIPO') if 'EQUIPO' in headers else 1
        idx_nom = headers.index('NOMBRE') if 'NOMBRE' in headers else 2
        idx_con = headers.index('CONCEPTO') if 'CONCEPTO' in headers else 5
        idx_pag = headers.index('PAGADO') if 'PAGADO' in headers else 6
        idx_esp = headers.index('ESPERADO') if 'ESPERADO' in headers else 7
        
        morosos = defaultdict(list)
        
        for row in pagos_data[1:]:
            if len(row) > max(idx_pag, idx_esp):
                try:
                    pagado_str = str(row[idx_pag]).replace('€','').replace(',','.').strip() or "0"
                    esperado_str = str(row[idx_esp]).replace('€','').replace(',','.').strip() or "0"
                    pagado = float(pagado_str)
                    esperado = float(esperado_str)
                    
                    deuda = esperado - pagado
                    if deuda > 0:
                        eq = row[idx_eq].strip()
                        nom = row[idx_nom].strip()
                        con = row[idx_con].strip()
                        morosos[eq].append({
                            "nombre": nom,
                            "concepto": con,
                            "pendiente": round(deuda, 2)
                        })
                except Exception as e:
                    pass
                    
        return jsonify(morosos)
    except Exception as e:
        print(f"Error api_kpis_pagos: {e}")
        return jsonify({}), 500

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