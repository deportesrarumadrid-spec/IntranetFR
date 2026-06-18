import os
import json
import re
from datetime import datetime
from flask import Blueprint, render_template, session, redirect, url_for, current_app, jsonify, request

import gspread # Explicitly import gspread for exceptions
jugadores_detalles_bp = Blueprint('jugadores_detalles_bp', __name__)

def _limpiar_h(h):
    """Normaliza cabeceras para búsquedas robustas de columnas."""
    if not h: return ""
    return str(h).strip().upper().replace('Ó','O').replace('Í','I').replace('É','E').replace('Á','A').replace('Ú','U').replace(' ','').replace('_','').replace('.','')

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

@jugadores_detalles_bp.route('/api/jugadores_detalles')
def api_jugadores_detalles():
    client = getattr(current_app, 'gs_client', None)
    NOMBRE_EXCEL = getattr(current_app, 'gs_name', "Control Asistencia Club")
    
    equipo_sess = request.args.get('equipo_actual') or session.get('equipo_defecto') # Prioriza el parámetro URL, luego la sesión
    if not equipo_sess:
        print("DEBUG: api_jugadores_detalles - No hay equipo seleccionado en la sesión.")
        return jsonify({"error": "No hay equipo seleccionado"}), 400
    
    target_norm = _limpiar_h(equipo_sess)
    print(f"DEBUG: api_jugadores_detalles - Equipo seleccionado: '{equipo_sess}', Normalizado: '{target_norm}'")

    try:
        # 1. Obtener datos de JUGADORES (nombre, equipo, posicion, pierna, fortalezas, debilidades)
        print("DEBUG: api_jugadores_detalles - Intentando abrir hoja 'JUGADORES'")
        sheet_jug = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        all_jug = sheet_jug.get_all_values()
        if not all_jug: return jsonify({"error": "Hoja de jugadores vacía"}), 404

        h_jug = [_limpiar_h(h) for h in all_jug[0]]
        idx_nom_jug = next((i for i, h in enumerate(h_jug) if h == "NOMBRE"), -1)
        idx_eq_jug = next((i for i, h in enumerate(h_jug) if h in ["EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS"]), -1)
        idx_pos_jug = next((i for i, h in enumerate(h_jug) if h == "POSICION"), -1)
        idx_pierna_jug = next((i for i, h in enumerate(h_jug) if h in ["PIERNA", "DIESTROZURDO"]), -1)
        idx_fort_jug = next((i for i, h in enumerate(h_jug) if h == "FORTALEZAS"), -1)
        idx_deb_jug = next((i for i, h in enumerate(h_jug) if h == "DEBILIDADES"), -1)

        if idx_nom_jug == -1 or idx_eq_jug == -1:
            print(f"ERROR: api_jugadores_detalles - Columnas 'NOMBRE' ({idx_nom_jug}) o 'EQUIPO' ({idx_eq_jug}) no encontradas en hoja JUGADORES.")
            return jsonify({"error": "Columnas 'NOMBRE' o 'EQUIPO' no encontradas en hoja JUGADORES"}), 400

        jugadores_detalles = {} # {nombre_upper: {data}}
        for i, row in enumerate(all_jug[1:]):
            if len(row) > idx_eq_jug and _limpiar_h(row[idx_eq_jug]) == target_norm:
                nombre = row[idx_nom_jug].strip()
                if nombre:
                    jugadores_detalles[nombre.upper()] = {
                        "nombre": nombre,
                        "posicion": row[idx_pos_jug].strip() if idx_pos_jug != -1 and len(row) > idx_pos_jug else "",
                        "pierna": row[idx_pierna_jug].strip() if idx_pierna_jug != -1 and len(row) > idx_pierna_jug else "",
                        "fortalezas": row[idx_fort_jug].strip() if idx_fort_jug != -1 and len(row) > idx_fort_jug else "",
                        "debilidades": row[idx_deb_jug].strip() if idx_deb_jug != -1 and len(row) > idx_deb_jug else "",
                        "comentarios": [],
                        "stats": { # Initialize stats with categories
                            "fisicas": {},
                            "tecnicas": {},
                            "tacticas": {},
                            "psicologicas": {}
                        }
                    }
                    print(f"DEBUG: api_jugadores_detalles - Jugador '{nombre}' del equipo '{row[idx_eq_jug]}' añadido.")
            elif len(row) > idx_eq_jug:
                print(f"DEBUG: api_jugadores_detalles - Fila {i+2} de JUGADORES: Equipo '{row[idx_eq_jug]}' (normalizado: '{_limpiar_h(row[idx_eq_jug])}') no coincide con '{target_norm}'.")
        
        if not jugadores_detalles:
            print(f"DEBUG: api_jugadores_detalles - No se encontraron jugadores para el equipo '{equipo_sess}' en la hoja 'JUGADORES'.")
        
        # 2. Obtener datos de ASISTENCIAS (charlas)
        try: # Added try-except for WorksheetNotFound
            print("DEBUG: api_jugadores_detalles - Intentando abrir hoja 'ASISTENCIAS'")
            sheet_asis = client.open(NOMBRE_EXCEL).worksheet("ASISTENCIAS")
            all_asis = sheet_asis.get_all_values()
            h_asis = [_limpiar_h(h) for h in all_asis[0]]
            idx_nom_asis = next((i for i, h in enumerate(h_asis) if h == "NOMBRE"), -1)
            idx_eq_asis = next((i for i, h in enumerate(h_asis) if h in ["EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS"]), -1)
            idx_cha_asis = next((i for i, h in enumerate(h_asis) if h in ["CHARLA", "CHARLAS"]), -1)
            idx_fecha_asis = 0 # Asumimos que la fecha es la primera columna

            if idx_nom_asis != -1 and idx_eq_asis != -1 and idx_cha_asis != -1:
                for row in all_asis[1:]:
                    if len(row) > idx_eq_asis and _limpiar_h(row[idx_eq_asis]) == target_norm:
                        nombre_asis = row[idx_nom_asis].strip().upper()
                        charla_val = row[idx_cha_asis].strip()
                        fecha_asis = row[idx_fecha_asis].strip()
                        if nombre_asis in jugadores_detalles and charla_val:
                            jugadores_detalles[nombre_asis]["comentarios"].append(f"Charla ({fecha_asis}): {charla_val}") # Removed extra print here
                            print(f"DEBUG: api_jugadores_detalles - Comentario de charla para '{nombre_asis}': '{charla_val}'")
        except gspread.exceptions.WorksheetNotFound:
            print("AVISO: La hoja 'ASISTENCIAS' no existe. No se cargarán comentarios de charlas.")
        except Exception as e:
            print(f"Error al cargar comentarios de ASISTENCIAS: {e}")

        # 3. Obtener datos de COORDINACION (observaciones)
        try: # Added try-except for WorksheetNotFound
            print("DEBUG: api_jugadores_detalles - Intentando abrir hoja 'COORDINACION'")
            sheet_coord = client.open(NOMBRE_EXCEL).worksheet("COORDINACION")
            all_coord = sheet_coord.get_all_values()
            h_coord = [_limpiar_h(h) for h in all_coord[0]]
            idx_nom_coord = next((i for i, h in enumerate(h_coord) if h == "JUGADOR"), -1)
            idx_eq_coord = next((i for i, h in enumerate(h_coord) if h in ["EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS"]), -1)
            idx_obs_coord = next((i for i, h in enumerate(h_coord) if h == "OBSERVACIONES"), -1)
            idx_fecha_coord = next((i for i, h in enumerate(h_coord) if h == "FECHA"), -1)

            if idx_nom_coord != -1 and idx_eq_coord != -1 and idx_obs_coord != -1 and idx_fecha_coord != -1:
                for row in all_coord[1:]:
                    if len(row) > idx_eq_coord and _limpiar_h(row[idx_eq_coord]) == target_norm:
                        nombre_coord = row[idx_nom_coord].strip().upper()
                        obs_val = row[idx_obs_coord].strip()
                        fecha_coord = row[idx_fecha_coord].strip()
                        if nombre_coord in jugadores_detalles and obs_val: # Removed extra print here
                            jugadores_detalles[nombre_coord]["comentarios"].append(f"Coord. ({fecha_coord}): {obs_val}") 
                            print(f"DEBUG: api_jugadores_detalles - Comentario de coordinación para '{nombre_coord}': '{obs_val}'")
        except gspread.exceptions.WorksheetNotFound:
            print("AVISO: La hoja 'COORDINACION' no existe. No se cargarán comentarios de coordinación.")
        except Exception as e:
            print(f"Error al cargar comentarios de COORDINACION: {e}")
        
        # 4. Obtener datos de STATS JUGADORES (nueva hoja)
        # Cabeceras de stats solicitadas por el usuario
        stats_headers_map = {
            "FISICAS": ["Velocidad", "Resistencia"],
            "TECNICAS": ["Precision pase corto", "Precision pase largo", "Capacidad de robo", "Juego de cabeza", "Regate/desborde", "Tiro", "Control", "Conduccion", "Definicion", "Creatividad con balon"],
            "TACTICAS": ["Vision de juego", "Toma de decisiones", "Agresividad/Intensidad", "Sacrificio defensivo", "Posicionamiento"],
            "PSICOLOGICAS": ["Concentracion", "Liderazgo", "Actitud entrenamientos", "Actitud partidos"]
        }
        
        try:
            print("DEBUG: api_jugadores_detalles - Intentando abrir hoja 'STATS JUGADORES'")
            sheet_stats = client.open(NOMBRE_EXCEL).worksheet("STATS JUGADORES")
            all_stats = sheet_stats.get_all_values()
            print(f"DEBUG: api_jugadores_detalles - Cabeceras de STATS JUGADORES (raw): {all_stats[0] if all_stats else 'N/A'}")
            h_stats = [_limpiar_h(h) for h in all_stats[0]]
            idx_nom_stats = next((i for i, h in enumerate(h_stats) if h == "NOMBRE"), -1)
            idx_eq_stats = next((i for i, h in enumerate(h_stats) if h in ["EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS"]), -1)

            if idx_nom_stats != -1 and idx_eq_stats != -1:
                for row in all_stats[1:]:
                    if len(row) > idx_eq_stats and _limpiar_h(row[idx_eq_stats]) == target_norm:
                        nombre_stats = row[idx_nom_stats].strip().upper()
                        if nombre_stats in jugadores_detalles:
                            for category, stat_names in stats_headers_map.items():
                                for stat_name in stat_names:
                                    normalized_stat_name = _limpiar_h(stat_name)
                                    idx_stat = next((i for i, h in enumerate(h_stats) if h == normalized_stat_name), -1)
                                    if idx_stat == -1:
                                        print(f"DEBUG: api_jugadores_detalles - Columna para stat '{stat_name}' (normalizada: '{normalized_stat_name}') NO encontrada en STATS JUGADORES.")
                                    elif len(row) > idx_stat and row[idx_stat].strip():
                                        jugadores_detalles[nombre_stats]["stats"][category.lower()][stat_name] = row[idx_stat].strip() # Removed extra print here
                                        print(f"DEBUG: api_jugadores_detalles - Stat '{stat_name}' para '{nombre_stats}': '{row[idx_stat].strip()}'")
        except gspread.exceptions.WorksheetNotFound:
            print("AVISO: La hoja 'STATS JUGADORES' no existe. No se cargarán estadísticas detalladas.")
        except Exception as e:
            print(f"Error al cargar STATS JUGADORES: {e}")

        # Convertir el diccionario de jugadores a una lista para el frontend
        jugadores_list = list(jugadores_detalles.values())
        print(f"DEBUG: api_jugadores_detalles - Total de jugadores a enviar al frontend: {len(jugadores_list)}")
        
        return jsonify({"jugadores": jugadores_list})

    except Exception as e:
        print(f"Error en api_jugadores_detalles: {e}")
        return jsonify({"error": str(e)}), 500