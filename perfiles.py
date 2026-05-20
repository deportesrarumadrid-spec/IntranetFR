from flask import Blueprint, request, jsonify, current_app
import gspread

perfiles_bp = Blueprint('perfiles_bp', __name__)

# Definición de las cabeceras esperadas en la hoja "PERFILES"
PERFILES_HEADERS = ["USUARIO", "CONTRASEÑA", "ENTRENAMIENTOS", "ASISTENCIAS", "FINANCIERO", "D.DEPORTIVA", "SEL. EQ."]

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
        current_headers = [str(h).strip() for h in sheet.row_values(1)]
        expected_headers = [h.strip() for h in PERFILES_HEADERS]
        if current_headers != expected_headers:
            # Usamos keyword arguments para máxima compatibilidad entre versiones de gspread
            sheet.update(values=[expected_headers], range_name='A1')
            print("DEBUG: Cabeceras de PERFILES sincronizadas en Google Sheets.")
            
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
        records = sheet.get_all_records()
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
            str(data.get('SEL. EQ.', data.get('ELEGIR_EQUIPO', 'NO'))).upper()
        ]
        sheet.append_row(new_row)
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
        nuevos_valores_fila = [
            data_norm.get('CONTRASEÑA', found_row_data[1] if len(found_row_data) > 1 else ""),
            str(data_norm.get('ENTRENAMIENTOS', found_row_data[2] if len(found_row_data) > 2 else "NO")).upper(),
            str(data_norm.get('ASISTENCIAS', found_row_data[3] if len(found_row_data) > 3 else "NO")).upper(),
            str(data_norm.get('FINANCIERO', found_row_data[4] if len(found_row_data) > 4 else "NO")).upper(),
            str(data_norm.get('D.DEPORTIVA', found_row_data[5] if len(found_row_data) > 5 else "NO")).upper(),
            str(data_norm.get('SEL. EQ.', found_row_data[6] if len(found_row_data) > 6 else "NO")).upper()
        ]

        # Actualizamos el rango completo de la fila (B hasta G) en una sola operación
        # Esto garantiza que todos los cambios se guarden de forma atómica y fiable
        rango_a_actualizar = f"B{row_index}:G{row_index}"
        print(f"DEBUG: Guardando cambios para {username} en fila {row_index}: {nuevos_valores_fila}")
        
        # Usamos keyword arguments para evitar problemas de orden entre versiones de gspread
        sheet.update(values=[nuevos_valores_fila], range_name=rango_a_actualizar, value_input_option='USER_ENTERED')
        
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
        
        sheet.delete_rows(row_index)
        return jsonify({"status": "success", "message": "Perfil eliminado correctamente."})
    except Exception as e:
        print(f"Error al eliminar perfil: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500