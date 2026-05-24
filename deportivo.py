import os
import json
import base64
import calendar
from datetime import datetime
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
            d, m, y = fecha_row.split('/')
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
        semanas.append(semana_formateada)

    # 3. Fotos subidas
    archivos_reales = [f for f in os.listdir(UPLOAD_FOLDER) if f.startswith(f"entreno_{usuario}_")]
    fotos_subidas = [f.split('_')[-1].split('.')[0] for f in archivos_reales]

    return render_template('deportivo/calendario.html',
                           usuario=usuario,
                           mes_actual=mes_actual,
                           objetivos=objetivos,
                           semanas=semanas,
                           archivos_subidos=len(archivos_reales),
                           fotos_subidas=fotos_subidas,
                           dias_transcurridos=hoy.day if es_mes_actual else (30 if (anio < hoy.year or (anio == hoy.year and mes < hoy.month)) else 0),
                           equipos=equipos,
                           equipo_defecto=equipo_activo
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