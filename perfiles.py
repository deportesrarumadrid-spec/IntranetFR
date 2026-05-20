from flask import Blueprint, request, jsonify, current_app
import gspread

perfiles_bp = Blueprint('perfiles_bp', __name__)

# Definición de las cabeceras esperadas en la hoja "PERFILES"
PERFILES_HEADERS = ["USUARIO", "CONTRASEÑA", "ENTRENAMIENTOS", "ASISTENCIAS", "FINANCIERO", "D.DEPORTIVA"]

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

        sheet = get_perfiles_sheet()
        # Verificar si el usuario ya existe
        existing_users = sheet.col_values(1) # Columna de USUARIO
        if usuario in existing_users:
            return jsonify({"status": "error", "message": f"El usuario '{usuario}' ya existe."}), 409

        # Construir la fila con los permisos, por defecto 'NO'
        new_row = [
            usuario,
            contrasena,
            data.get('ENTRENAMIENTOS', 'NO'),
            data.get('ASISTENCIAS', 'NO'),
            data.get('FINANCIERO', 'NO'),
            data.get('D.DEPORTIVA', 'NO')
        ]
        sheet.append_row(new_row)
        return jsonify({"status": "success", "message": "Perfil añadido correctamente."})
    except Exception as e:
        print(f"Error al añadir perfil: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@perfiles_bp.route('/api/perfiles/<username>', methods=['PUT'])
def update_perfil(username):
    """Actualiza un perfil de usuario existente."""
    data = request.json
    
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
        
        # Actualizar solo los campos proporcionados en la solicitud
        updates = []
        # Normalizamos las llaves del JSON recibido para comparar correctamente
        data_norm = {str(k).upper(): v for k, v in data.items()}

        for i, header in enumerate(PERFILES_HEADERS):
            if header in data_norm:
                updates.append({
                    'range': gspread.utils.rowcol_to_a1(row_index, i + 1),
                    'values': [[data_norm[header]]]
                })
        
        if updates:
            sheet.spreadsheet.values_batch_update({
                'valueInputOption': 'USER_ENTERED',
                'data': updates
            })
        
        return jsonify({"status": "success", "message": "Perfil actualizado correctamente."})
    except Exception as e:
        print(f"Error al actualizar perfil: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@perfiles_bp.route('/api/perfiles/<username>', methods=['DELETE'])
def delete_perfil(username):
    """Elimina un perfil de usuario."""
    try:
        sheet = get_perfiles_sheet()
        cell = sheet.find(username, in_column=1) # Buscar por USUARIO en la primera columna
        if not cell:
            return jsonify({"status": "error", "message": "Usuario no encontrado."}), 404
        
        sheet.delete_rows(cell.row)
        return jsonify({"status": "success", "message": "Perfil eliminado correctamente."})
    except Exception as e:
        print(f"Error al eliminar perfil: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500