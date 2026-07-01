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
        # 1. Obtener la lista de jugadores para el equipo desde la hoja "JUGADORES" (misma fuente que ASISTENCIAS)
        print("DEBUG: api_jugadores_detalles - Leyendo jugadores desde la hoja 'JUGADORES'")
        sheet_jugadores = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        all_jugadores = sheet_jugadores.get_all_values()
        if not all_jugadores: return jsonify({"error": "Hoja 'JUGADORES' vacía"}), 404

        h_jugadores = [_limpiar_h(h) for h in all_jugadores[0]]
        idx_nom_jug = next((i for i, h in enumerate(h_jugadores) if h == "NOMBRE"), -1)
        idx_eq_jug = next((i for i, h in enumerate(h_jugadores) if h in ["EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS"]), -1)

        if idx_nom_jug == -1 or idx_eq_jug == -1:
            return jsonify({"error": "Columnas 'NOMBRE' o 'EQUIPO' no encontradas en 'JUGADORES'"}), 400

        # 2. Crear el diccionario de detalles para los jugadores del equipo seleccionado
        jugadores_detalles = {} # {nombre_upper: {data}}
        for row in all_jugadores[1:]:
            if len(row) > max(idx_nom_jug, idx_eq_jug) and _limpiar_h(row[idx_eq_jug].strip()) == target_norm:
                nombre = row[idx_nom_jug].strip()
                if nombre:
                    jugadores_detalles[nombre.upper()] = {
                        "nombre": nombre,
                        "posicion": "", "posicion_sec": "", "pierna": "", "fortalezas": "", "debilidades": "",
                        "comentarios": [],
                        "stats": { "fisicas": {}, "tecnicas": {}, "tacticas": {}, "psicologicas": {} }
                    }
                    print(f"DEBUG: api_jugadores_detalles - Jugador '{nombre}' del equipo '{equipo_sess}' añadido desde JUGADORES.")

        # 3. Enriquecer con comentarios de ASISTENCIAS
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
                for row in all_asis[1:]: # Usamos all_asis que ya leímos
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

        # 4. Enriquecer con comentarios de COORDINACION
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
        
        # 5. Enriquecer con datos de la misma hoja "MI EQUIPO" (stats, posición, etc.)
        # Cabeceras de stats solicitadas por el usuario
        stats_headers_map = {
            "FISICAS": ["Velocidad", "Resistencia"],
            "TECNICAS": ["Precision pase corto", "Precision pase largo", "Capacidad de robo", "Juego de cabeza", "Regate/desborde", "Tiro", "Control", "Conduccion", "Definicion", "Creatividad con balon"],
            "TACTICAS": ["Vision de juego", "Toma de decisiones", "Agresividad/Intensidad", "Sacrificio defensivo", "Posicionamiento"],
            "PSICOLOGICAS": ["Concentracion", "Liderazgo", "Actitud entrenamientos", "Actitud partidos"]
        }

        try:
            sheet_miequipo = client.open(NOMBRE_EXCEL).worksheet("MI EQUIPO")
            all_miequipo = sheet_miequipo.get_all_values()
            h_miequipo = [_limpiar_h(h) for h in all_miequipo[0]]
            idx_nom_me = next((i for i, h in enumerate(h_miequipo) if h == "NOMBRE"), -1)
            idx_eq_me = next((i for i, h in enumerate(h_miequipo) if h in ["EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS"]), -1)

            for row in all_miequipo[1:]:
                if len(row) > max(idx_nom_me, idx_eq_me) and _limpiar_h(row[idx_eq_me].strip()) == target_norm:
                    nombre_me = row[idx_nom_me].strip().upper()
                    if nombre_me in jugadores_detalles:
                        # Actualizar campos básicos
                        for campo, col_name in [("posicion", "POSICION"), ("posicion_sec", "POSICION_SEC"), ("pierna", "PIERNA"), ("fortalezas", "FORTALEZAS"), ("debilidades", "DEBILIDADES")]:
                            idx = next((i for i, h in enumerate(h_miequipo) if h == _limpiar_h(col_name)), -1)
                            if idx != -1 and len(row) > idx:
                                jugadores_detalles[nombre_me][campo] = row[idx].strip()
                        
                        # Actualizar stats
                        for category, stat_names in stats_headers_map.items():
                            for stat_name in stat_names:
                                idx_stat = next((i for i, h in enumerate(h_miequipo) if h == _limpiar_h(stat_name)), -1)
                                if idx_stat != -1 and len(row) > idx_stat and row[idx_stat].strip():
                                    jugadores_detalles[nombre_me]["stats"][category.lower()][stat_name] = row[idx_stat].strip()
        except gspread.exceptions.WorksheetNotFound:
            print("AVISO: La hoja 'MI EQUIPO' no existe. No se cargarán datos adicionales (posición, stats, etc.).")

        # Convertir el diccionario de jugadores a una lista para el frontend
        jugadores_list = list(jugadores_detalles.values())
        print(f"DEBUG: api_jugadores_detalles - Total de jugadores a enviar al frontend: {len(jugadores_list)}")
        
        return jsonify({"jugadores": jugadores_list})

    except Exception as e:
        print(f"Error en api_jugadores_detalles: {e}")
        return jsonify({"error": str(e)}), 500

@jugadores_detalles_bp.route('/api/jugadores_detalles/update', methods=['POST'])
def update_jugador_detalle():
    client = getattr(current_app, 'gs_client', None)
    NOMBRE_EXCEL = getattr(current_app, 'gs_name', "Control Asistencia Club")
    
    data = request.json
    nombre_jugador = data.get('nombre')
    stat_name = data.get('statName')
    valor = data.get('valor')
    equipo_actual = session.get('equipo_defecto')

    if not all([nombre_jugador, stat_name, equipo_actual]):
        return jsonify({"status": "error", "message": "Faltan datos en la petición"}), 400

    target_sheet_name = "MI EQUIPO"
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet(target_sheet_name)
        all_data = sheet.get_all_values()
        if not all_data:
            return jsonify({"status": "error", "message": f"La hoja {target_sheet_name} está vacía."}), 404

        headers = [_limpiar_h(h) for h in all_data[0]]
        
        # Encontrar la columna a actualizar
        col_stat_idx = next((i for i, h in enumerate(headers) if h == _limpiar_h(stat_name)), -1)
        if col_stat_idx == -1:
            return jsonify({"status": "error", "message": f"Columna '{stat_name}' no encontrada en la hoja '{target_sheet_name}'"}), 404

        # Encontrar la fila del jugador
        idx_nom = next((i for i, h in enumerate(headers) if h == "NOMBRE"), -1)
        idx_eq = next((i for i, h in enumerate(headers) if h in ["EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS"]), -1)
        
        if idx_nom == -1 or idx_eq == -1:
            return jsonify({"status": "error", "message": "Columnas 'NOMBRE' o 'EQUIPO' no encontradas para buscar al jugador."}), 404

        fila_idx = -1
        for i, row in enumerate(all_data[1:]): # Empezar desde la segunda fila (datos)
            if len(row) > max(idx_nom, idx_eq):
                nombre_en_fila = row[idx_nom].strip().upper()
                equipo_en_fila = _limpiar_h(row[idx_eq].strip()) # Añadido .strip() para más robustez
                if nombre_en_fila == nombre_jugador.upper() and equipo_en_fila == _limpiar_h(equipo_actual):
                    fila_idx = i + 2 # +1 por empezar en 0, +1 por saltar cabecera
                    break
        
        if fila_idx != -1:
            sheet.update_cell(fila_idx, col_stat_idx + 1, valor)
            return jsonify({"status": "success", "message": f"'{stat_name}' de {nombre_jugador} actualizado a '{valor}'."})
        else:
            # Si no se encuentra, CREAR una nueva fila para el jugador
            print(f"AVISO: No se encontró al jugador '{nombre_jugador}', se creará una nueva fila en 'MI EQUIPO'.")
            nueva_fila = [''] * len(headers)
            nueva_fila[idx_nom] = nombre_jugador
            nueva_fila[idx_eq] = equipo_actual
            nueva_fila[col_stat_idx] = valor
            sheet.append_row(nueva_fila, value_input_option='USER_ENTERED')
            return jsonify({"status": "success", "message": f"Se ha creado una nueva entrada para {nombre_jugador} y se ha guardado '{stat_name}'."})

    except Exception as e:
        print(f"Error en update_jugador_detalle: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500