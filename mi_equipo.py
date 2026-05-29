import os
import json
import re
import calendar
from datetime import datetime
from flask import Blueprint, render_template, session, redirect, url_for, current_app, jsonify, request

mi_equipo_bp = Blueprint('mi_equipo_bp', __name__)

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
        inicio_y = hoy.year if hoy.month >= 9 else hoy.year - 1
        periodo_req = "25-26" # Forzamos el periodo solicitado Sep 25 - Ago 26
    
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
        idx_val_asis = next((i for i, h in enumerate(h_asis) if h == "VALORACION"), 5)
        # 2. Obtener total de jugadores del equipo para el ratio (ej: 10 de 12)
        sheet_jug = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        all_jug = sheet_jug.get_all_values()
        h_jug = [_limpiar_h(h) for h in all_jug[0]]
        idx_eq_jug = -1
        for col in ["EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS"]:
            if col in h_jug:
                idx_eq_jug = h_jug.index(col); break
        if idx_eq_jug == -1: idx_eq_jug = 1 # Fallback si no encuentra columna
        target_norm = _limpiar_h(equipo_sess)
        jugadores_equipo = [r for r in all_jug[1:] if len(r) > idx_eq_jug and _limpiar_h(r[idx_eq_jug]) == target_norm]
        total_plantilla = len(jugadores_equipo)

        stats_asistencia = {}
        stats_valoracion = {}
        
        # Procesar ASISTENCIAS (Fila 0 es cabecera)
        for row in all_asis[1:]:
            if len(row) > max(idx_eq_asis, idx_nom_asis, idx_asis_asis, idx_val_asis) and _limpiar_h(row[idx_eq_asis]) == target_norm:
                fecha_str = row[0] # DD/MM/YYYY
                nombre = row[idx_nom_asis].strip()
                asistencia = str(row[idx_asis_asis]).strip().upper()
                valoracion = str(row[idx_val_asis]).strip().upper()
                
                try:
                    parts = fecha_str.split('/')
                    mes_clave = f"{parts[1]}/{parts[2]}" # Agrupamos por Mes/Año
                    dia_clave = parts[0]
                except: continue
                
                # --- Lógica de Asistencias ---
                if mes_clave not in stats_asistencia:
                    stats_asistencia[mes_clave] = {"total_presencial": 0, "num_sesiones": set(), "jugadores": {}}
                
                if nombre not in stats_asistencia[mes_clave]["jugadores"]:
                    stats_asistencia[mes_clave]["jugadores"][nombre] = {"si": 0, "total": 0}
                
                stats_asistencia[mes_clave]["jugadores"][nombre]["total"] += 1
                if asistencia in ['SI', 'OK']:
                    stats_asistencia[mes_clave]["num_sesiones"].add(dia_clave)
                    stats_asistencia[mes_clave]["total_presencial"] += 1
                    stats_asistencia[mes_clave]["jugadores"][nombre]["si"] += 1

                # --- Lógica de Valoraciones (Flechas) ---
                if mes_clave not in stats_valoracion:
                    stats_valoracion[mes_clave] = {"ARRIBA": 0, "MEDIO": 0, "ABAJO": 0, "count": 0}
                
                if valoracion in ["EXCELENTE", "MUY BIEN", "BIEN", "MUYBIEN"]:
                    stats_valoracion[mes_clave]["ARRIBA"] += 1
                    stats_valoracion[mes_clave]["count"] += 1
                elif valoracion == "NORMAL":
                    stats_valoracion[mes_clave]["MEDIO"] += 1
                    stats_valoracion[mes_clave]["count"] += 1
                elif valoracion in ["REGULAR", "MAL", "MUY MAL"]:
                    stats_valoracion[mes_clave]["ABAJO"] += 1
                    stats_valoracion[mes_clave]["count"] += 1

        # Formatear datos finales para Chart.js (SIEMPRE 12 MESES)
        asis_chart = {"labels": [], "media": [], "ratio": [], "max_player": [], "min_player": [], "objetivo": []}
        tabla_valoracion = []
        nombres_m = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]
        
        for m_num, y_num in meses_periodo:
            m_clave = f"{m_num:02d}/{y_num}"
            lbl = f"{nombres_m[m_num-1]}{str(y_num)[2:]}"
            
            asis_chart["labels"].append(lbl)
            
            # --- Estadísticas de Asistencia ---
            d = stats_asistencia.get(m_clave, {"total_presencial": 0, "num_sesiones": set(), "jugadores": {}})
            num_sesiones = len(d.get("num_sesiones", []))
            media_presentes = d["total_presencial"] / num_sesiones if num_sesiones > 0 else 0
            media_asis = (media_presentes / total_plantilla * 100) if total_plantilla > 0 else 0
            
            asis_chart["media"].append(round(media_asis, 1))
            asis_chart["ratio"].append(f"{round(media_presentes, 1)} de {total_plantilla}" if total_plantilla > 0 else "0 de 0")
            asis_chart["objetivo"].append(96.3) # Valor fijo solicitado
            
            p_stats = sorted([(n, (pj["si"]/pj["total"]*100)) for n, pj in d["jugadores"].items() if pj["total"] > 0], key=lambda x: x[1])
            asis_chart["min_player"].append(f"{p_stats[0][0]} ({round(p_stats[0][1],1)}%)" if p_stats else "N/A")
            asis_chart["max_player"].append(f"{p_stats[-1][0]} ({round(p_stats[-1][1],1)}%)" if p_stats else "N/A")

            # --- Estadísticas de Valoración para la Tabla ---
            dv = stats_valoracion.get(m_clave, {"ARRIBA": 0, "MEDIO": 0, "ABAJO": 0, "count": 0})
            total_v = dv["count"] if dv["count"] > 0 else 1
            
            tabla_valoracion.append({
                "mes": lbl,
                "arriba": round((dv["ARRIBA"] / total_v) * 100, 1) if dv["count"] > 0 else 0,
                "medio": round((dv["MEDIO"] / total_v) * 100, 1) if dv["count"] > 0 else 0,
                "abajo": round((dv["ABAJO"] / total_v) * 100, 1) if dv["count"] > 0 else 0,
                "total_v": dv["count"]
            })

        return jsonify({
            "asistencia": asis_chart,
            "tabla_valoracion": tabla_valoracion
        })
    except Exception as e:
        print(f"Error en stats: {e}")
        return jsonify({"error": str(e)}), 500