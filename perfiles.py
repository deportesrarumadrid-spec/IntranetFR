from flask import Blueprint, request, jsonify
import gspread
from app import client, NOMBRE_EXCEL # Importamos la conexión a Google Sheets desde app.py

perfiles_bp = Blueprint('perfiles_bp', __name__)

# Definición de las cabeceras esperadas en la hoja "PERFILES"
PERFILES_HEADERS = ["USUARIO", "CONTRASEÑA", "ENTRENAMIENTOS", "ASISTENCIAS", "FINANCIERO", "D.DEPORTIVA"]

def get_perfiles_sheet():
    """
    Obtiene la hoja de Google Sheets 'PERFILES'.
    Si no existe, la crea con las cabeceras predefinidas.
    """
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("PERFILES")
        # Opcional: Verificar y corregir cabeceras si no coinciden
        current_headers = sheet.row_values(1)
        if current_headers != PERFILES_HEADERS:
            print("Advertencia: Las cabeceras de la hoja 'PERFILES' no coinciden. Intentando corregir.")
            # Una estrategia simple: si faltan, las añadimos. Si sobran, las ignoramos por ahora.
            # Para una gestión más robusta, se podría reordenar o pedir confirmación.
            if not current_headers or len(current_headers) < len(PERFILES_HEADERS):
                sheet.clear() # Limpiar y reescribir si está muy desordenado o vacío
                sheet.append_row(PERFILES_HEADERS)
            else:
                # Intentar actualizar solo las celdas de cabecera que no coincidan
                for i, header in enumerate(PERFILES_HEADERS):
                    if i < len(current_headers) and current_headers[i] != header:
                        sheet.update_cell(1, i + 1, header)
                    elif i >= len(current_headers): # Si hay nuevas cabeceras que no existían
                        sheet.update_cell(1, i + 1, header)
        return sheet
    except gspread.exceptions.WorksheetNotFound:
        print("Creando hoja 'PERFILES' en Google Sheets...")
        sheet = client.open(NOMBRE_EXCEL).add_worksheet(title="PERFILES", rows="100", cols=len(PERFILES_HEADERS))
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
    data = request.json
    usuario = data.get('USUARIO', '').strip()
    contrasena = data.get('CONTRASEÑA', '')
    
    if not usuario or not contrasena:
        return jsonify({"status": "error", "message": "Usuario y Contraseña son obligatorios."}), 400

    try:
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
        cell = sheet.find(username, in_column=1) # Buscar por USUARIO en la primera columna
        if not cell:
            return jsonify({"status": "error", "message": "Usuario no encontrado."}), 404
        
        row_index = cell.row
        
        # Actualizar solo los campos proporcionados en la solicitud
        updates = []
        for i, header in enumerate(PERFILES_HEADERS):
            if header in data:
                updates.append({
                    'range': gspread.utils.rowcol_to_a1(row_index, i + 1),
                    'values': [[data[header]]]
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