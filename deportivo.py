import os
import json
import calendar
from datetime import datetime
from flask import Blueprint, render_template, session, request, jsonify

# Creamos el Blueprint para Dirección Deportiva
deportivo_bp = Blueprint('deportivo_bp', __name__)

@deportivo_bp.route('/deportivo')
def deportivo():
    # Importamos las rutas de carpetas desde la app principal
    from app import DATA_FOLDER, UPLOAD_FOLDER
    usuario = session.get('usuario', 'admin')
    mes_actual = request.args.get('mes', '2026-05')
    hoy = datetime.now()
    
    # 1. Cargar objetivos
    obj_path = os.path.join(DATA_FOLDER, f'obj_{usuario}_{mes_actual}.json')
    objetivos = {"tactico": "TIRO, CENTRO", "tecnico": "", "completados": []}
    if os.path.exists(obj_path):
        with open(obj_path, 'r', encoding='utf-8') as f:
            objetivos = json.load(f)

    # 2. Lógica del Calendario
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
                           dias_transcurridos=hoy.day if es_mes_actual else (30 if (anio < hoy.year or (anio == hoy.year and mes < hoy.month)) else 0))

@deportivo_bp.route('/api/seguimiento_coordinacion', methods=['GET', 'POST'])
def api_seguimiento_coordinacion():
    from app import client, NOMBRE_EXCEL
    
    try:
        sheet_name = "COORDINACION"
        try:
            sheet = client.open(NOMBRE_EXCEL).worksheet(sheet_name)
        except:
            sheet = client.open(NOMBRE_EXCEL).add_worksheet(title=sheet_name, rows="1000", cols="6")
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
    from app import client, NOMBRE_EXCEL
    try:
        sheet_name = "TECNIFICACIONES"
        try:
            sheet = client.open(NOMBRE_EXCEL).worksheet(sheet_name)
        except:
            sheet = client.open(NOMBRE_EXCEL).add_worksheet(title=sheet_name, rows="1000", cols="3")
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
            sheet.update(f'B{fila_idx}:C{fila_idx}', [[datos['equipo'], datos['grupo']]])
        else:
            sheet.append_row([datos['fecha'], datos['equipo'], datos['grupo']])
            
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@deportivo_bp.route('/direccion_deportiva')
def direccion_deportiva():
    # Importamos la conexión desde la app principal
    from app import client, NOMBRE_EXCEL
    usuario = session.get('usuario', 'admin')
    
    try:
        # Cargamos los jugadores para el selector de coordinación
        sheet = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        all_v = sheet.get_all_values()
        if not all_v: 
            return render_template('direccion.html', usuario=usuario, equipos=[], jugadores_raw=[])
        
        headers = [h.strip().upper() for h in all_v[0]]
        idx_eq = headers.index("EQUIPO") if "EQUIPO" in headers else -1
        idx_nom = headers.index("NOMBRE") if "NOMBRE" in headers else -1
        
        jugadores = []
        for row in all_v[1:]:
            if len(row) > max(idx_eq, idx_nom):
                jugadores.append({"NOMBRE": row[idx_nom], "EQUIPO": row[idx_eq]})
        
        equipos = sorted(list(set(j['EQUIPO'] for j in jugadores)))
        return render_template('direccion.html', usuario=usuario, equipos=equipos, jugadores_raw=jugadores)
    except Exception as e:
        print(f"Error en direccion_deportiva: {e}")
        return str(e), 500