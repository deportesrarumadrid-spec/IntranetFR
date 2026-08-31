from flask import Blueprint, request, jsonify, current_app
import gspread
import os
import json as _json

def _invalidar_cache_perfiles():
    try:
        import app as _app
        _app._PERFILES_CACHE['records'] = None
    except Exception:
        pass

perfiles_bp = Blueprint('perfiles_bp', __name__)

EQUIPOS_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'equipos_config.json')

# Definición de las cabeceras esperadas en la hoja "PERFILES"
PERFILES_HEADERS = ["USUARIO", "CONTRASEÑA", "ENTRENAMIENTOS", "ASISTENCIAS", "FINANCIERO", "D.DEPORTIVA", "SEL. EQ.", "CRONOGRAMA", "EQUIPO", "TELEFONO", "TIPO", "D.DEP"]

def get_perfiles_sheet():
    """
    Obtiene la hoja de Google Sheets 'PERFILES'.
    Si no existe, la crea con las cabeceras predefinidas.
    """
    try:
        client = getattr(current_app, 'gs_client', None)
        NOMBRE_EXCEL = getattr(current_app, 'gs_name', "Control Asistencia Club")

        if not client:
            raise Exception("No se pudo localizar el cliente de Google Sheets (client).")

        sheet = client.open(NOMBRE_EXCEL).worksheet("PERFILES")
        
        # Sincronización de cabeceras: Si las cabeceras no coinciden exactamente, las actualizamos
        all_values = sheet.get_all_values()
        row1 = all_values[0] if all_values else []
        
        current_headers = [str(h).strip().upper() for h in row1]
        expected_headers = [h.strip() for h in PERFILES_HEADERS]
        
        if current_headers != expected_headers:
            # Forzamos la estructura correcta (Sintaxis universal gspread)
            sheet.update(range_name='A1', values=[expected_headers], value_input_option='USER_ENTERED')
            print(f"DEBUG: Cabeceras de PERFILES sincronizadas.")
            
        return sheet
    except gspread.exceptions.WorksheetNotFound:
        client = getattr(current_app, 'gs_client')
        NOMBRE_EXCEL = getattr(current_app, 'gs_name')
        spreadsheet = client.open(NOMBRE_EXCEL)
        sheet = spreadsheet.add_worksheet(title="PERFILES", rows="100", cols=len(PERFILES_HEADERS))
        sheet.append_row(PERFILES_HEADERS)
        return sheet

@perfiles_bp.route('/api/perfiles', methods=['GET'])
def get_perfiles():
    """Devuelve todos los perfiles de usuario y sus permisos."""
    try:
        sheet = get_perfiles_sheet()
        # Usamos get_all_values y mapeamos manualmente para evitar errores de celdas vacías
        all_v = sheet.get_all_values()
        if not all_v or len(all_v) < 2:
            return jsonify([])

        headers = [str(h).strip().upper() for h in all_v[0]]
        records = []
        for row in all_v[1:]:
            if any(str(cell).strip() for cell in row): # Solo filas con algún dato
                item = {}
                for i, h in enumerate(headers):
                    item[h] = row[i] if i < len(row) else ""
                records.append(item)
        return jsonify(records)
    except Exception as e:
        print(f"Error al obtener perfiles: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@perfiles_bp.route('/api/perfiles', methods=['POST'])
def add_perfil():
    """Añade un nuevo perfil de usuario."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No se han recibido datos válidos."}), 400

        usuario = data.get('USUARIO', '').strip()
        contrasena = data.get('CONTRASEÑA', '')
        
        if not usuario or not contrasena:
            return jsonify({"status": "error", "message": "Usuario y Contraseña son obligatorios."}), 400

        # Impedir la creación de un usuario 'admin' adicional para evitar conflictos
        if usuario.lower() == 'admin':
            return jsonify({"status": "error", "message": "El nombre 'admin' está reservado para el sistema."}), 400

        sheet = get_perfiles_sheet()
        # Verificar si el usuario ya existe
        # Obtenemos todos los usuarios y comparamos en mayúsculas ignorando la cabecera
        existing_users = [str(u).strip().upper() for u in sheet.col_values(1)]
        if usuario.upper() in existing_users:
            return jsonify({"status": "error", "message": f"El usuario '{usuario}' ya existe."}), 409

        # Construir la fila con los permisos, por defecto 'NO'
        new_row = [
            usuario,
            contrasena,
            str(data.get('ENTRENAMIENTOS', 'NO')).upper(),
            str(data.get('ASISTENCIAS', 'NO')).upper(),
            str(data.get('FINANCIERO', 'NO')).upper(),
            str(data.get('D.DEPORTIVA', 'NO')).upper(),
            str(data.get('SEL. EQ.', data.get('ELEGIR_EQUIPO', 'NO'))).upper(),
            str(data.get('CRONOGRAMA', 'NO')).upper(),
            str(data.get('EQUIPO', '')).strip(),
            str(data.get('TELEFONO', '')).strip(),
            str(data.get('TIPO', '')).strip()
        ]
        sheet.append_row(new_row)
        _invalidar_cache_perfiles()
        return jsonify({"status": "success", "message": "Perfil añadido correctamente."})
    except Exception as e:
        print(f"Error al añadir perfil: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@perfiles_bp.route('/api/perfiles/<username>', methods=['PUT'])
def update_perfil(username):
    """Actualiza un perfil de usuario existente."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No se han recibido datos."}), 400

        # Protección extra: El admin master no se puede editar desde aquí
        if username.lower() == 'admin':
            return jsonify({"status": "error", "message": "El usuario administrador master no puede ser modificado."}), 403
            
        sheet = get_perfiles_sheet()
        all_values = sheet.get_all_values()
        row_index = -1
        found_row_data = []
        target_user = username.strip().upper()

        for i, row in enumerate(all_values):
            if row and row[0].strip().upper() == target_user:
                row_index = i + 1
                found_row_data = row
                break

        if row_index == -1:
            return jsonify({"status": "error", "message": "Usuario no encontrado."}), 404
        
        # Normalizamos las llaves del JSON recibido para comparar correctamente
        data_norm = {str(k).strip().upper(): v for k, v in data.items()}

        # Construimos los valores para las columnas B a G (indices 1 a 6 de PERFILES_HEADERS)
        # Tomamos el valor nuevo del JSON o mantenemos el antiguo si no viene en la petición
        existing_pwd = found_row_data[1] if len(found_row_data) > 1 else ""
        nueva_pwd_raw = data_norm.get('CONTRASEÑA')
        if nueva_pwd_raw is not None and str(nueva_pwd_raw).strip():
            pwd_a_guardar = str(nueva_pwd_raw).strip()
        else:
            pwd_a_guardar = existing_pwd

        # D.DEP (col L, índice 11) — se incluye en el rango B:L para garantizar la escritura
        ddep_raw = str(data_norm.get('D.DEP', found_row_data[11] if len(found_row_data) > 11 else '')).strip().upper()
        ddep_to_save = ddep_raw if ddep_raw in ('SI', 'NO') else (found_row_data[11] if len(found_row_data) > 11 else '')
        tipo_val = found_row_data[10] if len(found_row_data) > 10 else ''  # TIPO (col K) — preservar

        nuevos_valores_fila = [
            pwd_a_guardar,
            str(data_norm.get('ENTRENAMIENTOS', found_row_data[2] if len(found_row_data) > 2 else "NO")).upper(),
            str(data_norm.get('ASISTENCIAS', found_row_data[3] if len(found_row_data) > 3 else "NO")).upper(),
            str(data_norm.get('FINANCIERO', found_row_data[4] if len(found_row_data) > 4 else "NO")).upper(),
            str(data_norm.get('D.DEPORTIVA', found_row_data[5] if len(found_row_data) > 5 else "NO")).upper(),
            str(data_norm.get('SEL. EQ.', found_row_data[6] if len(found_row_data) > 6 else "NO")).upper(),
            str(data_norm.get('CRONOGRAMA', found_row_data[7] if len(found_row_data) > 7 else "NO")).upper(),
            str(data_norm.get('EQUIPO', found_row_data[8] if len(found_row_data) > 8 else "")).strip(),
            str(data_norm.get('TELEFONO', found_row_data[9] if len(found_row_data) > 9 else "")).strip(),
            tipo_val,
            ddep_to_save,
        ]

        rango_a_actualizar = f"B{row_index}:L{row_index}"
        print(f"DEBUG: Guardando {username} fila {row_index} B:L → {nuevos_valores_fila}")

        sheet.update(values=[nuevos_valores_fila], range_name=rango_a_actualizar, value_input_option='USER_ENTERED')

        _invalidar_cache_perfiles()
        return jsonify({"status": "success", "message": "Perfil actualizado correctamente."})
    except Exception as e:
        print(f"Error al actualizar perfil: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@perfiles_bp.route('/api/perfiles/<username>', methods=['DELETE'])
def delete_perfil(username):
    """Elimina un perfil de usuario."""
    try:
        sheet = get_perfiles_sheet()
        all_values = sheet.get_all_values()
        row_index = -1
        target_user = username.strip().upper()

        for i, row in enumerate(all_values):
            if row and row[0].strip().upper() == target_user:
                row_index = i + 1
                break

        if row_index == -1:
            return jsonify({"status": "error", "message": "Usuario no encontrado."}), 404
        
        # Leer la fila antes de borrarla para saber si es entrenador
        row_data = all_values[row_index - 1] if row_index - 1 < len(all_values) else []
        headers = [str(h).strip().upper() for h in all_values[0]] if all_values else []
        idx_tipo = headers.index('TIPO') if 'TIPO' in headers else -1
        tipo = str(row_data[idx_tipo]).strip().upper() if idx_tipo >= 0 and idx_tipo < len(row_data) else ''

        sheet.delete_rows(row_index)
        _invalidar_cache_perfiles()

        # Si era un entrenador auto-generado, limpiar también equipos_config.json
        if tipo == 'ENTRENADOR':
            try:
                parts = username.strip().split(' ', 1)
                nombre = parts[0].strip()
                apellido = parts[1].strip() if len(parts) > 1 else ''
                with open(EQUIPOS_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    ec = _json.load(f)
                for eq in ec.get('equipos', []):
                    for i, ent in enumerate(eq.get('entrenadores') or []):
                        if isinstance(ent, dict):
                            if ent.get('nombre','').strip() == nombre and ent.get('apellido','').strip() == apellido:
                                eq['entrenadores'][i] = {'nombre': '', 'apellido': '', 'telefono': ''}
                with open(EQUIPOS_CONFIG_FILE, 'w', encoding='utf-8') as f:
                    _json.dump(ec, f, ensure_ascii=False, indent=2)
            except Exception as e_ec:
                print(f"[delete_perfil] Error limpiando equipos_config: {e_ec}")

        return jsonify({"status": "success", "message": "Perfil eliminado correctamente."})
    except Exception as e:
        print(f"Error al eliminar perfil: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@perfiles_bp.route('/api/sync_entrenadores_perfiles', methods=['POST'])
def sync_entrenadores_perfiles():
    """Equipos → PERFILES: crea/actualiza/elimina perfiles de entrenadores al guardar configuración de equipos."""
    try:
        data = request.get_json() or {}
        equipos = data.get('equipos', [])

        # Construir mapa: clave_lower → {usuario, telefono, equipos:[]}
        trainers_map = {}
        for eq in equipos:
            eq_nombre = (eq.get('nombre') or '').strip()
            if not eq_nombre:
                continue
            for ent in (eq.get('entrenadores') or []):
                if isinstance(ent, str):
                    parts = ent.strip().split(' ', 1)
                    nombre = parts[0].strip()
                    apellido = parts[1].strip() if len(parts) > 1 else ''
                    telefono = ''
                elif isinstance(ent, dict):
                    nombre = (ent.get('nombre') or '').strip()
                    apellido = (ent.get('apellido') or '').strip()
                    telefono = (ent.get('telefono') or '').strip()
                else:
                    continue
                if not nombre and not apellido:
                    continue
                usuario = f"{nombre} {apellido}".strip()
                key = usuario.lower()
                if key not in trainers_map:
                    trainers_map[key] = {'usuario': usuario, 'telefono': telefono, 'equipos': []}
                if eq_nombre not in trainers_map[key]['equipos']:
                    trainers_map[key]['equipos'].append(eq_nombre)
                if not trainers_map[key]['telefono'] and telefono:
                    trainers_map[key]['telefono'] = telefono

        sheet = get_perfiles_sheet()
        all_v = sheet.get_all_values()
        headers = [str(h).strip().upper() for h in all_v[0]] if all_v else list(PERFILES_HEADERS)

        def col_idx(h):
            try: return headers.index(h.upper())
            except ValueError: return -1

        # Perfiles actuales → {usuario_upper: {row_idx, tipo}}
        profiles = {}
        for i, row in enumerate(all_v[1:], start=2):
            if row and any(str(c).strip() for c in row):
                u = str(row[0]).strip().upper()
                if u:
                    idx_t = col_idx('TIPO')
                    tipo = str(row[idx_t]).strip().upper() if idx_t >= 0 and idx_t < len(row) else ''
                    profiles[u] = {'row_idx': i, 'tipo': tipo}

        # Entrenadores actuales con TIPO=ENTRENADOR (para detectar borrados)
        existing_trainer_keys = {u.lower() for u, p in profiles.items() if p['tipo'] == 'ENTRENADOR'}

        # Crear o actualizar
        for key, info in trainers_map.items():
            usuario = info['usuario']
            equipo_str = ', '.join(info['equipos'])
            telefono = info['telefono']
            u_upper = usuario.upper()
            if u_upper in profiles:
                row_idx = profiles[u_upper]['row_idx']
                idx_eq = col_idx('EQUIPO')
                idx_tel = col_idx('TELEFONO')
                updates = []
                if idx_eq >= 0:
                    updates.append({'range': f"I{row_idx}", 'values': [[equipo_str]]})
                if idx_tel >= 0 and telefono:
                    updates.append({'range': f"J{row_idx}", 'values': [[telefono]]})
                if updates:
                    sheet.batch_update(updates)
            else:
                sheet.append_row([
                    usuario, '',  # contraseña vacía, admin la pondrá
                    'SI', 'SI', 'NO', 'NO', 'SI', 'NO',
                    equipo_str, telefono, 'ENTRENADOR'
                ])

        # Eliminar entrenadores que ya no están en ningún equipo
        trainers_to_delete = existing_trainer_keys - set(trainers_map.keys())
        if trainers_to_delete:
            all_v2 = sheet.get_all_values()
            idx_t = col_idx('TIPO')
            rows_to_delete = []
            for i, row in enumerate(all_v2[1:], start=2):
                u = str(row[0]).strip().lower() if row else ''
                tipo = str(row[idx_t]).strip().upper() if idx_t >= 0 and idx_t < len(row) else ''
                if u in trainers_to_delete and tipo == 'ENTRENADOR':
                    rows_to_delete.append(i)
            for r in sorted(rows_to_delete, reverse=True):
                sheet.delete_rows(r)

        return jsonify({'status': 'ok', 'synced': len(trainers_map), 'deleted': len(trainers_to_delete) if trainers_to_delete else 0})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@perfiles_bp.route('/api/sync_entrenadores_equipos_back', methods=['POST'])
def sync_entrenadores_equipos_back():
    """PERFILES → equipos_config: cuando se guarda un entrenador en U&C, sincroniza equipo/teléfono."""
    try:
        data = request.get_json() or {}
        trainer_key = (data.get('trainer_key') or '').strip()
        new_equipo_str = (data.get('equipo') or '').strip()
        new_telefono = (data.get('telefono') or '').strip()

        if not trainer_key:
            return jsonify({'status': 'ok'})

        try:
            with open(EQUIPOS_CONFIG_FILE, 'r', encoding='utf-8') as f:
                ec = _json.load(f)
        except Exception:
            return jsonify({'status': 'ok', 'msg': 'equipos_config not found'})

        parts = trainer_key.strip().split(' ', 1)
        nombre = parts[0].strip()
        apellido = parts[1].strip() if len(parts) > 1 else ''

        # Comprobar si este usuario es entrenador en algún equipo
        current_teams = set()
        for eq in ec.get('equipos', []):
            for ent in (eq.get('entrenadores') or []):
                if isinstance(ent, dict):
                    if ent.get('nombre','').strip() == nombre and ent.get('apellido','').strip() == apellido:
                        current_teams.add((eq.get('nombre') or '').strip())

        if not current_teams:
            return jsonify({'status': 'ok', 'msg': 'not a trainer'})

        # Actualizar teléfono en todos los equipos donde está
        for eq in ec.get('equipos', []):
            for ent in (eq.get('entrenadores') or []):
                if isinstance(ent, dict):
                    if ent.get('nombre','').strip() == nombre and ent.get('apellido','').strip() == apellido:
                        if new_telefono:
                            ent['telefono'] = new_telefono

        # Gestionar cambios de equipo
        new_teams = {t.strip() for t in new_equipo_str.split(',') if t.strip()}
        teams_to_remove = current_teams - new_teams
        teams_to_add = new_teams - current_teams

        for eq in ec.get('equipos', []):
            eq_nombre = (eq.get('nombre') or '').strip()
            if eq_nombre in teams_to_remove:
                for i, ent in enumerate(eq.get('entrenadores') or []):
                    if isinstance(ent, dict):
                        if ent.get('nombre','').strip() == nombre and ent.get('apellido','').strip() == apellido:
                            eq['entrenadores'][i] = {'nombre': '', 'apellido': '', 'telefono': ''}
                            break
            elif eq_nombre in teams_to_add:
                ents = eq.setdefault('entrenadores', [{},{},{}])
                for i, ent in enumerate(ents):
                    if isinstance(ent, dict) and not ent.get('nombre','').strip() and not ent.get('apellido','').strip():
                        eq['entrenadores'][i] = {'nombre': nombre, 'apellido': apellido, 'telefono': new_telefono}
                        break

        with open(EQUIPOS_CONFIG_FILE, 'w', encoding='utf-8') as f:
            _json.dump(ec, f, ensure_ascii=False, indent=2)

        return jsonify({'status': 'ok'})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500