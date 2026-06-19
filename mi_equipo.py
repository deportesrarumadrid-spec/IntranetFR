import os
import json
import re
import calendar
from datetime import datetime
from flask import Blueprint, render_template, session, redirect, url_for, current_app, jsonify, request

mi_equipo_bp = Blueprint('mi_equipo_bp', __name__)

# Mapeo para días de la semana (ISO: Lunes=0, Domingo=6)
MAP_WEEKDAYS = {
    'L': 0, 'LUNES': 0, 'M': 1, 'MARTES': 1, 
    'X': 2, 'MIERCOLES': 2, 'MIÉRCOLES': 2,
    'J': 3, 'JUEVES': 3, 'V': 4, 'VIERNES': 4,
    'S': 5, 'SABADO': 5, 'SÁBADO': 5, 'D': 6, 'DOMINGO': 6
}

def _norm_fecha(f):
    """Normaliza fechas DD/MM/YYYY para evitar duplicados por ceros iniciales."""
    f = str(f).strip().replace('-', '/')
    if '/' not in f: return f
    return "/".join([str(int(p)) if p.strip().isdigit() else p.strip() for p in f.split('/')])

def _parse_fecha_flexible(f_str):
    """Intenta parsear fechas de forma robusta soportando varios formatos (D/M/YYYY, D-M-Y, etc)."""
    if not f_str: return None
    s = str(f_str).strip().replace('-', '/')
    for fmt in ("%d/%m/%Y", "%Y/%m/%d", "%d/%m/%y"):
        try: return datetime.strptime(s, fmt)
        except: continue
    try: # Fallback manual para casos raros
        p = [int(x) for x in s.split('/') if x.strip().isdigit()]
        if len(p) == 3: return datetime(p[2] if p[2]>100 else 2000+p[2], p[1], p[0])
    except: pass
    return None

@mi_equipo_bp.route('/mi-equipo')
def mi_equipo():
    usuario = session.get('usuario')
    if not usuario:
        return redirect('/')
    
    equipo = session.get('equipo_defecto')
    if not equipo:
        return redirect(url_for('seleccionar_equipo'))
        
    return render_template('mi_equipo.html', usuario=usuario, equipo=equipo, equipo_defecto=equipo)

def _limpiar_h(h):
    """Normaliza cabeceras para búsquedas robustas de columnas."""
    if not h: return ""
    return str(h).strip().upper().replace('Ó','O').replace('Í','I').replace('É','E').replace('Á','A').replace('Ú','U').replace(' ','').replace('_','').replace('.','')

def _normalizar_valoracion(v):
    """Normaliza el valor de la valoración para agrupar valores y flechas antiguas."""
    if v is None:
        return ""
    s = str(v).strip().upper()
    s = s.replace('Á','A').replace('Í','I').replace('É','E').replace('Ó','O').replace('Ú','U')
    s = s.replace('_', ' ').replace('-', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    if any(ch in s for ch in ['↑', '▲']):
        return "EXCELENTE"
    if any(ch in s for ch in ['↓', '▼']):
        return "MAL"
    if any(ch in s for ch in ['→', '➡', '=>', '->']):
        return "NORMAL"
    if s in ["EXCELENTE", "MUY BIEN", "BIEN", "MUYBIEN", "NORMAL", "REGULAR", "MAL", "MUY MAL"]:
        return s
    if s == "OK":
        return "EXCELENTE"
    return s

def _normalizar_asistencia(v):
    """Normaliza el estado de asistencia para soportar acentos y sinónimos."""
    if v is None:
        return ""
    s = str(v).strip().upper()
    s = s.replace('Á','A').replace('Í','I').replace('É','E').replace('Ó','O').replace('Ú','U')
    s = re.sub(r'\s+', ' ', s).strip()
    if s in ['SI', 'SÍ', 'OK', 'YES', 'ASISTIO', 'ASISTE', 'ASISTIDO']:
        return 'SI'
    if s in ['NO', 'AUSENTE', 'FALTA', 'NO ASISTE', 'NOASISTE']:
        return 'NO'
    if s in ['X', 'BAJA', 'EXC', 'EXENTADO', 'EXCUSA', 'EXCUSADO']:
        return 'X'
    if s in ['AV', 'AVISO', 'AVANZADO', 'AVANZADA']:
        return 'AV'
    return s

@mi_equipo_bp.route('/api/stats/mi-equipo')
def api_stats_mi_equipo():
    client = getattr(current_app, 'gs_client', None)
    NOMBRE_EXCEL = getattr(current_app, 'gs_name', "Control Asistencia Club")
    
    equipo_sess = session.get('equipo_defecto')
    if not equipo_sess:
        return jsonify({"error": "No hay equipo seleccionado"}), 400
    
    # 0. Determinar el periodo deportivo (Sep - Ago)
    # Si recibimos ?periodo=25-26 empezamos en Sep 25. 
    # Si no, calculamos el periodo actual basado en la fecha de hoy.
    periodo_req = request.args.get('periodo') 
    if not periodo_req:
        hoy = datetime.now()
        # Determine the start year of the current season (Sept-Aug)
        # If current month is Sep-Dec, season starts in current year.
        # If current month is Jan-Aug, season starts in previous year.
        season_start_year = hoy.year if hoy.month >= 9 else hoy.year - 1
        season_end_year = season_start_year + 1
        periodo_req = f"{str(season_start_year)[2:]}-{str(season_end_year)[2:]}"
        print(f"DEBUG: Dynamic period determined: {periodo_req}") # Add debug log
    
    try:
        y_start = int(f"20{periodo_req.split('-')[0]}")
        meses_periodo = [] # Lista de (mes_num, año_num)
        for m in range(9, 13): meses_periodo.append((m, y_start))
        for m in range(1, 9): meses_periodo.append((m, y_start + 1))
    except:
        return jsonify({"error": "Formato de periodo inválido (debe ser YY-YY)"}), 400

    try:
        # 1. Obtener datos de Asistencias y Valoraciones
        sheet_asis = client.open(NOMBRE_EXCEL).worksheet("ASISTENCIAS")
        all_asis = sheet_asis.get_all_values()
        if not all_asis: return jsonify({"error": "Hoja de asistencias vacía"}), 404

        h_asis = [_limpiar_h(h) for h in all_asis[0]]
        # Indexación de columnas: buscamos específicamente EQUIPO o CATEGORIA
        idx_eq_asis = next((i for i, h in enumerate(h_asis) if h in ["EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS"]), 1)
        idx_nom_asis = next((i for i, h in enumerate(h_asis) if h == "NOMBRE"), 2)
        idx_asis_asis = next((i for i, h in enumerate(h_asis) if h == "ASISTENCIA"), 4)
        idx_val_asis = next((i for i, h in enumerate(h_asis) if h in ["VALORACION", "VALORACIONES"]), 5)
        idx_cha_asis = next((i for i, h in enumerate(h_asis) if h in ["CHARLA", "CHARLAS"]), 7)
        # 2. Obtener total de jugadores del equipo para el ratio (ej: 10 de 12)
        sheet_jug = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        all_jug = sheet_jug.get_all_values()
        h_jug = [_limpiar_h(h) for h in all_jug[0]]
        
        # Búsqueda precisa de columnas en JUGADORES
        idx_eq_jug = next((i for i, h in enumerate(h_jug) if h in ["EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS"]), 1)
        idx_nom_jug = next((i for i, h in enumerate(h_jug) if h == "NOMBRE"), 0)
        # Búsqueda más robusta de columnas de baja/alta (soporta espacios y puntos)
        idx_baja_jug = next((i for i, h in enumerate(h_jug) if h in ["BAJADESDE", "FECHABAJA", "BAJA", "BAJA_DESDE"]), -1)
        idx_alta_jug = next((i for i, h in enumerate(h_jug) if h in ["ALTADESDE", "FECHAALTA", "ALTA", "ALTA_DESDE"]), -1)

        target_norm = _limpiar_h(equipo_sess)
        jugadores_equipo = [r for r in all_jug[1:] if len(r) > idx_eq_jug and _limpiar_h(r[idx_eq_jug]) == target_norm]
        total_plantilla = len(jugadores_equipo)
        
        # Diccionario de plantilla con sus fechas de baja/alta para cálculo de 'X' automático
        plantilla_info = {} # {NOMBRE_UPPER: {'baja': dt, 'alta': dt}}
        for r in jugadores_equipo:
            if len(r) > idx_nom_jug:
                n_u = str(r[idx_nom_jug]).strip().upper()
                baja_dt = None
                alta_dt = None
                if idx_baja_jug != -1 and len(r) > idx_baja_jug:
                    baja_dt = _parse_fecha_flexible(r[idx_baja_jug])
                if idx_alta_jug != -1 and len(r) > idx_alta_jug:
                    alta_dt = _parse_fecha_flexible(r[idx_alta_jug])
                plantilla_info[n_u] = {'baja': baja_dt, 'alta': alta_dt}

        # 2.1 Obtener Días Programados desde STAFF para el denominador real
        sheet_staff = client.open(NOMBRE_EXCEL).worksheet("STAFF")
        all_staff = sheet_staff.get_all_records()
        dias_semana_prog = []
        for s in all_staff:
            s_norm = {_limpiar_h(k): v for k, v in s.items()}
            # FIX: Normalizar también el valor del equipo para que la comparación sea exitosa
            if _limpiar_h(s_norm.get('EQUIPO')) == target_norm:
                raw_dias = str(s_norm.get('DIASENTRENAMIENTO', '')).upper().replace(' ', '')
                dias_semana_prog = [MAP_WEEKDAYS[d] for d in raw_dias.split(',') if d in MAP_WEEKDAYS]
                break

        stats_asistencia = {}
        stats_valoracion = {}
        
        # Diccionario para agrupar y deduplicar: {(mes_clave, nombre_upper, dia_clave): {'asistencias': [status1, status2], 'valoraciones': [val1, val2], 'charlas': [charla1, charla2]}}
        registros_diarios = {}

        # Procesar ASISTENCIAS (Fila 0 es cabecera)
        for row in all_asis[1:]:
            if len(row) > max(idx_eq_asis, idx_nom_asis, idx_asis_asis, idx_val_asis) and _limpiar_h(row[idx_eq_asis]) == target_norm:
                fecha_str = _norm_fecha(row[0]) # Normalizamos para evitar 5/5 vs 05/05
                nombre = row[idx_nom_asis].strip()
                asistencia = _normalizar_asistencia(row[idx_asis_asis])
                valoracion = _normalizar_valoracion(row[idx_val_asis])
                
                try:
                    parts = fecha_str.split('/') # parts[0]=dia, parts[1]=mes, parts[2]=año
                    if len(parts) < 3: continue
                    m_num_extracted = int(parts[1])
                    y_num_extracted = int(parts[2])
                    mes_clave = f"{m_num_extracted:02d}/{y_num_extracted}"
                    dia_clave = str(int(parts[0]))
                except: continue
                
                # Identificador único por Jugador y Día para colapsar duplicados y filtrar por plantilla oficial
                nombre_upper = nombre.upper() # Normalizamos el nombre a mayúsculas
                if nombre_upper in plantilla_info:
                    id_registro = (mes_clave, nombre_upper, dia_clave)
                    charla_val = str(row[idx_cha_asis]).strip().upper() if len(row) > idx_cha_asis else ""

                    if id_registro not in registros_diarios: # Si es la primera vez que vemos este registro (jugador/día)
                        registros_diarios[id_registro] = {'asistencias': [], 'valoraciones': [], 'charlas': []}
                    
                    registros_diarios[id_registro]['asistencias'].append(asistencia)
                    if valoracion: registros_diarios[id_registro]['valoraciones'].append(valoracion)
                    if charla_val: registros_diarios[id_registro]['charlas'].append(charla_val)

        # Ahora, resolvemos los registros diarios para obtener un estado final único por jugador/día
        registros_diarios_final = {} # {(mes_clave, nombre_upper, dia_clave): {'asis': final_status, 'val': final_val, 'charla': final_charla}}
        for (mes_clave, nombre_upper, dia_clave), data_raw in registros_diarios.items():
            final_asis = ''
            # Prioridad: SI/OK > X > NO > AV > Vacío
            if 'SI' in data_raw['asistencias'] or 'OK' in data_raw['asistencias']:
                final_asis = 'SI'
            elif 'X' in data_raw['asistencias']: # Si hay una X y no hay SI, la X es el estado final
                final_asis = 'X'
            elif 'NO' in data_raw['asistencias']:
                final_asis = 'NO'
            elif 'AV' in data_raw['asistencias']:
                final_asis = 'AV'
            
            # Para valoración, tomamos la última no vacía o la primera si hay varias
            final_val = next((v for v in reversed(data_raw['valoraciones']) if v), '')
            final_charla = 'SI' if 'SI' in data_raw['charlas'] or 'OK' in data_raw['charlas'] else 'NO'

            registros_diarios_final[(mes_clave, nombre_upper, dia_clave)] = {
                'asis': final_asis,
                'val': final_val,
                'charla': final_charla
            }

        # Reinicializamos stats_asistencia y stats_valoracion para usar los registros_diarios_final
        stats_asistencia = {}
        stats_valoracion = {}

        # Una vez deduplicados y resueltos los datos, calculamos las estadísticas reales
        for (mes_clave, nombre_upper, dia_clave), datos in registros_diarios_final.items():
            # FILTRO DE PRECISIÓN: Solo contamos si ocurre en los días oficiales (STAFF)
            try:
                m_c, y_c = map(int, mes_clave.split('/'))
                if dias_semana_prog and datetime(y_c, m_c, int(dia_clave)).weekday() not in dias_semana_prog:
                    continue
            except: continue

            if mes_clave not in stats_asistencia: # Inicializamos si es el primer registro para este mes
                stats_asistencia[mes_clave] = {"total_si": 0, "total_x": 0, "jugadores": {}, "dias_entreno": set()}
            
            stats_asistencia[mes_clave]["dias_entreno"].add(dia_clave)
            
            if mes_clave not in stats_valoracion:
                stats_valoracion[mes_clave] = {"ARRIBA": 0, "MEDIO": 0, "ABAJO": 0, "CHARLAS": 0, "dias_evaluados": set()}
            
            if nombre_upper not in stats_asistencia[mes_clave]["jugadores"]: # Inicializamos si es el primer registro para este jugador en este mes
                stats_asistencia[mes_clave]["jugadores"][nombre_upper] = {"si": 0, "x": 0}

            if datos['charla'] in ['SI', 'OK']:
                stats_valoracion[mes_clave]["CHARLAS"] += 1

            if datos['asis'] == 'SI': # Solo 'SI' (ya resuelto de SI/OK)
                stats_asistencia[mes_clave]["total_si"] += 1
                stats_asistencia[mes_clave]["jugadores"][nombre_upper]["si"] += 1
            elif datos['asis'] == 'X':
                stats_asistencia[mes_clave]["total_x"] += 1
                stats_asistencia[mes_clave]["jugadores"][nombre_upper]["x"] += 1

            valoracion = _normalizar_valoracion(datos['val'])
            if valoracion in ["EXCELENTE", "MUY BIEN", "BIEN", "MUYBIEN", "NORMAL", "REGULAR", "MAL", "MUY MAL"]:
                stats_valoracion[mes_clave]["dias_evaluados"].add(dia_clave)
                if valoracion in ["EXCELENTE", "MUY BIEN", "BIEN", "MUYBIEN"]:
                    stats_valoracion[mes_clave]["ARRIBA"] += 1
                elif valoracion == "NORMAL":
                    stats_valoracion[mes_clave]["MEDIO"] += 1
                elif valoracion in ["REGULAR", "MAL", "MUY MAL"]:
                    stats_valoracion[mes_clave]["ABAJO"] += 1

        # Formatear datos finales para Chart.js
        asis_chart = {"labels": [], "media": [], "ratio": [], "monthly_player_rankings": []}
        tabla_valoracion = []
        nombres_m = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]
        
        total_stats_individual = {} # {nombre: {"si": 0, "x": 0, "total": 0}}

        hoy = datetime.now() # Get current date for filtering

        for m_num, y_num in meses_periodo:
            m_clave = f"{m_num:02d}/{y_num}"
            lbl = f"{nombres_m[m_num-1]}{str(y_num)[2:]}"
            
            # Filter out future months: only process months up to the current one
            if y_num > hoy.year or (y_num == hoy.year and m_num > hoy.month):
                continue # Skip future months

            asis_chart["labels"].append(lbl)
            
            # --- Estadísticas de Asistencia ---
            d = stats_asistencia.get(m_clave, {"total_si": 0, "total_x": 0, "jugadores": {}, "dias_entreno": set()})

            # 1. Identificamos los días de entrenamiento reales del calendario
            dias_entrenamiento_mes = []
            if dias_semana_prog:
                for day in range(1, calendar.monthrange(y_num, m_num)[1] + 1):
                    dt_val = datetime(y_num, m_num, day)
                    if dt_val.weekday() in dias_semana_prog:
                        dias_entrenamiento_mes.append(dt_val)
            else:
                for d_str in d.get("dias_entreno", []):
                    try: dias_entrenamiento_mes.append(datetime(y_num, m_num, int(d_str)))
                    except: pass

            num_dias_mes = len(dias_entrenamiento_mes)
            total_huecos = num_dias_mes * total_plantilla

            # 2. CÁLCULO DE 'X' BASADO EN ROSTER (Source of Truth) - Forzamos la resta de periodos de baja
            total_x_oficial = 0
            for nombre_u, dates in plantilla_info.items():
                for dt_entreno in dias_entrenamiento_mes:
                    d_check = dt_entreno.replace(hour=0, minute=0, second=0, microsecond=0)
                    b_check = dates['baja'].replace(hour=0, minute=0, second=0, microsecond=0) if dates['baja'] else None
                    a_check = dates['alta'].replace(hour=0, minute=0, second=0, microsecond=0) if dates['alta'] else None
                    
                    if b_check and d_check >= b_check:
                        if not a_check or d_check < a_check:
                            total_x_oficial += 1

            total_disponible = total_huecos - total_x_oficial
            
            if total_disponible > 0:
                media_asis = round((d["total_si"] / total_disponible * 100), 2)
            else: media_asis = 0

            asis_chart["media"].append(media_asis)
            asis_chart["ratio"].append(f"{d['total_si']} SI de {total_disponible} (T:{total_huecos} - X:{total_x_oficial})")
            
            # Ranking Mensual: Solo jugadores de la plantilla
            p_stats_month = []
            for n in plantilla_info.keys():
                pj = d["jugadores"].get(n, {"si": 0, "x": 0})
                disp_indiv = num_dias_mes - pj["x"]
                p_perc = round((pj["si"] / disp_indiv * 100), 1) if disp_indiv > 0 else 0
                p_stats_month.append({"nombre": n, "perc": p_perc})
                
                # Acumulado Total Temporada
                if n not in total_stats_individual: total_stats_individual[n] = {"si": 0, "x": 0}
                total_stats_individual[n]["si"] += pj["si"]
                total_stats_individual[n]["x"] += pj["x"]

            p_stats_month.sort(key=lambda x: x["perc"], reverse=True)
            asis_chart["monthly_player_rankings"].append(p_stats_month)

            # --- Estadísticas de Valoración para la Tabla ---
            dv = stats_valoracion.get(m_clave, {"ARRIBA": 0, "MEDIO": 0, "ABAJO": 0, "CHARLAS": 0, "dias_evaluados": set()})
            
            # Suma de valoraciones reales (flechas totales)
            sum_arrows = dv["ARRIBA"] + dv["MEDIO"] + dv["ABAJO"]
            total_v_for_percentage = sum_arrows if sum_arrows > 0 else 1
            
            tabla_valoracion.append({
                "mes": lbl,
                "arriba": round((dv["ARRIBA"] / total_v_for_percentage) * 100, 1) if sum_arrows > 0 else 0,
                "medio": round((dv["MEDIO"] / total_v_for_percentage) * 100, 1) if sum_arrows > 0 else 0,
                "abajo": round((dv["ABAJO"] / total_v_for_percentage) * 100, 1) if sum_arrows > 0 else 0,
                "charlas": dv["CHARLAS"],
                "total_v": sum_arrows,
                "n_arriba": dv["ARRIBA"],
                "n_medio": dv["MEDIO"],
                "n_abajo": dv["ABAJO"]
            })

        # Calcular Ranking Total Temporada
        total_ranking = []
        for n, s in total_stats_individual.items():
            # El total acumulado también se basa en la suma de días programados en toda la temporada
            total_dias_temp = 0
            for m_n, y_n in meses_periodo:
                if dias_semana_prog:
                    num_days_m = calendar.monthrange(y_n, m_n)[1]
                    total_dias_temp += sum(1 for d in range(1, num_days_m+1) if datetime(y_n, m_n, d).weekday() in dias_semana_prog)
                else:
                    m_c = f"{m_n:02d}/{y_n}"
                    total_dias_temp += len(stats_asistencia.get(m_c, {}).get("dias_entreno", []))
            
            disp_total = total_dias_temp - s["x"]
            perc = (s["si"] / disp_total * 100) if disp_total > 0 else 0
            total_ranking.append({
                "nombre": n,
                "perc": round(perc, 1),
                "si_count": s["si"],  # Add SI count
                "total_available_days": disp_total # Add total available days
            })
        
        total_ranking.sort(key=lambda x: x["perc"], reverse=True)

        return jsonify({
            "asistencia": asis_chart,
            "total_ranking": total_ranking, # Return the full sorted list of player objects
            "tabla_valoracion": tabla_valoracion
        })
    except Exception as e:
        print(f"Error en stats: {e}")
        return jsonify({"error": str(e)}), 500