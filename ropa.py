import gspread
from gspread.utils import rowcol_to_a1
from flask import Blueprint, request, jsonify, session, current_app
from datetime import datetime

ropa_bp = Blueprint('ropa_bp', __name__)

def get_ropa_sheet():
    client = current_app.gs_client
    name = current_app.gs_name
    try:
        return client.open(name).worksheet("ROPA")
    except gspread.exceptions.WorksheetNotFound:
        # Creamos la hoja con la estructura inicial de 8 columnas de prendas (A, B, C + 8 = K)
        sheet = client.open(name).add_worksheet(title="ROPA", rows="1000", cols="20")
        headers = ["FECHA", "EQUIPO", "JUGADOR", "PRENDA 1", "PRENDA 2", "PRENDA 3", "PRENDA 4", "PRENDA 5", "PRENDA 6", "PRENDA 7", "PRENDA 8"]
        sheet.append_row(headers)
        return sheet

def log_cambio_ropa(jugador, prenda, anterior, nuevo, usuario):
    """Registra el historial de cambios para auditoría."""
    try:
        client = current_app.gs_client
        name = current_app.gs_name
        try:
            log_sheet = client.open(name).worksheet("ROPA_LOG")
        except:
            log_sheet = client.open(name).add_worksheet(title="ROPA_LOG", rows="1000", cols="6")
            log_sheet.append_row(["FECHA", "USUARIO", "JUGADOR", "PRENDA", "VALOR ANTERIOR", "VALOR NUEVO"])
        
        log_sheet.append_row([
            datetime.now().strftime("%d/%m/%Y %H:%M"),
            usuario, jugador, prenda, anterior, nuevo
        ])
    except: pass

@ropa_bp.route('/api/ropa/logs', methods=['GET'])
def get_ropa_logs():
    jugador = request.args.get('jugador')
    prenda = request.args.get('prenda')
    client = current_app.gs_client
    name = current_app.gs_name
    try:
        log_sheet = client.open(name).worksheet("ROPA_LOG")
        all_logs = log_sheet.get_all_values()
        if not all_logs: return jsonify([])
        
        records = []
        for row in reversed(all_logs[1:]): # Más recientes primero
            if len(row) > 3 and row[2].strip() == jugador.strip() and row[3].strip() == prenda.strip():
                records.append({
                    "fecha": row[0],
                    "usuario": row[1],
                    "anterior": row[4],
                    "nuevo": row[5]
                })
        return jsonify(records)
    except:
        return jsonify([])

@ropa_bp.route('/api/ropa', methods=['GET'])
def get_ropa_data():
    sheet = get_ropa_sheet()
    all_v = sheet.get_all_values()
    if not all_v: return jsonify({"headers": [], "data": []})
    return jsonify({"headers": all_v[0], "data": all_v[1:]})

@ropa_bp.route('/api/ropa/header', methods=['POST'])
def update_ropa_header():
    datos = request.json
    col_idx = datos.get('col_idx') # 1-based
    nuevo_nombre = datos.get('nombre').upper()
    sheet = get_ropa_sheet()
    sheet.update_cell(1, col_idx, nuevo_nombre)
    return jsonify({"status": "success"})

@ropa_bp.route('/api/ropa/nueva_columna', methods=['POST'])
def add_ropa_column():
    sheet = get_ropa_sheet()
    headers = sheet.row_values(1)
    nueva_idx = len(headers) + 1
    nombre = f"PRENDA {nueva_idx - 3}"
    sheet.update_cell(1, nueva_idx, nombre)
    return jsonify({"status": "success", "nombre": nombre})

@ropa_bp.route('/api/ropa/borrar_columna', methods=['POST'])
def borrar_ropa_columna():
    """Borrado seguro: vacía la cabecera y todos los datos de la columna, pero no
    desplaza el resto de columnas (evita romper índices fijos usados en otras partes)."""
    datos = request.json
    col_idx = datos.get('col_idx')  # 1-based
    sheet = get_ropa_sheet()
    all_v = sheet.get_all_values()
    num_filas = len(all_v)
    if num_filas > 0:
        rango = f"{rowcol_to_a1(1, col_idx)}:{rowcol_to_a1(num_filas, col_idx)}"
        sheet.batch_clear([rango])
    return jsonify({"status": "success"})

@ropa_bp.route('/api/ropa/save', methods=['POST'])
def save_ropa():
    datos = request.json
    jugador = datos.get('jugador')
    equipo = datos.get('equipo')
    col_idx = datos.get('col_idx')
    valor_nuevo = datos.get('valor') # Formato: TALLA|ESTADO|FECHA
    usuario = session.get('usuario', 'Sistema')

    sheet = get_ropa_sheet()
    all_v = sheet.get_all_values()
    headers = all_v[0]
    prenda = headers[col_idx-1]
    
    fila_idx = -1
    valor_anterior = ""

    # Buscar fila del jugador (coincidencia de Nombre y Equipo)
    for i, row in enumerate(all_v):
        if i == 0: continue
        if len(row) > 2 and row[2].strip() == jugador.strip() and row[1].strip() == equipo.strip():
            fila_idx = i + 1
            valor_anterior = row[col_idx-1] if len(row) >= col_idx else "Vacio"
            break

    if fila_idx == -1:
        # Crear fila si no existe
        nueva_fila = [""] * len(headers)
        nueva_fila[0] = datetime.now().strftime("%d/%m/%Y")
        nueva_fila[1] = equipo
        nueva_fila[2] = jugador
        nueva_fila[col_idx-1] = valor_nuevo
        sheet.append_row(nueva_fila)
    else:
        sheet.update_cell(fila_idx, col_idx, valor_nuevo)
        sheet.update_cell(fila_idx, 1, datetime.now().strftime("%d/%m/%Y"))

    log_cambio_ropa(jugador, prenda, valor_anterior or "Vacio", valor_nuevo, usuario)
    return jsonify({"status": "success"})


# ─── DORSALES ─────────────────────────────────────────────────────────────────

def get_dorsales_sheet():
    client = current_app.gs_client
    name = current_app.gs_name
    try:
        return client.open(name).worksheet("DORSALES")
    except gspread.exceptions.WorksheetNotFound:
        sheet = client.open(name).add_worksheet(title="DORSALES", rows="500", cols="3")
        sheet.append_row(["EQUIPO", "JUGADOR", "DORSAL"])
        return sheet

@ropa_bp.route('/api/dorsal', methods=['GET'])
def get_dorsales():
    sheet = get_dorsales_sheet()
    all_v = sheet.get_all_values()
    result = {}
    for row in all_v[1:]:
        if len(row) >= 2 and row[1].strip():
            key = f"{row[0].strip()}|{row[1].strip()}"
            result[key] = row[2].strip() if len(row) > 2 else ""
    return jsonify(result)

@ropa_bp.route('/api/dorsal', methods=['POST'])
def save_dorsal():
    datos = request.json or {}
    equipo = datos.get('equipo', '').strip()
    jugador = datos.get('jugador', '').strip()
    dorsal = str(datos.get('dorsal', '')).strip()

    if not jugador:
        return jsonify({"status": "error", "message": "Jugador requerido"}), 400
    if dorsal and (not dorsal.isdigit() or not (1 <= int(dorsal) <= 99)):
        return jsonify({"status": "error", "message": "Dorsal debe ser 1-99"}), 400

    sheet = get_dorsales_sheet()
    all_v = sheet.get_all_values()
    fila_idx = -1
    for i, row in enumerate(all_v):
        if i == 0: continue
        if len(row) >= 2 and row[0].strip() == equipo and row[1].strip() == jugador:
            fila_idx = i + 1
            break

    if fila_idx == -1:
        sheet.append_row([equipo, jugador, dorsal])
    else:
        sheet.update_cell(fila_idx, 3, dorsal)

    return jsonify({"status": "success"})