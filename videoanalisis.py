from flask import Blueprint, render_template, request, jsonify, session, redirect, current_app
from datetime import datetime

videoanalisis_bp = Blueprint('videoanalisis_bp', __name__)

# Categorías que se consideran F7 (todo lo que no esté aquí se trata como F11)
CATEGORIAS_F7 = ['PREBENJAMIN', 'BENJAMIN', 'ALEVIN F7', 'ALEVIN FEM']

HEADERS_VIDEOANALISIS = ['FECHA', 'EQUIPO', 'MOMENTO_CON_BALON', 'MOMENTO_SIN_BALON', 'ANALISIS_ENTRENADOR']


def es_equipo_f11(nombre_equipo):
    nombre = (nombre_equipo or '').strip().upper()
    return not any(cat in nombre for cat in CATEGORIAS_F7)


def _client_name():
    return current_app.gs_client, current_app.gs_name


def obtener_equipos_f11():
    client, name = _client_name()
    equipos = []
    try:
        sheet = client.open(name).worksheet("EQUIPO")
        all_v = sheet.get_all_values()
        if all_v:
            headers = [str(h).strip().upper() for h in all_v[0]]
            if "EQUIPO" in headers:
                idx_e = headers.index("EQUIPO")
                todos = sorted(set(str(r[idx_e]).strip() for r in all_v[1:] if len(r) > idx_e and r[idx_e].strip()))
                equipos = [e for e in todos if es_equipo_f11(e)]
    except Exception as e:
        print(f"Error cargando equipos F11: {e}")
    return equipos


def get_videoanalisis_sheet():
    client, name = _client_name()
    try:
        return client.open(name).worksheet("VIDEOANALISIS")
    except Exception:
        sheet = client.open(name).add_worksheet(title="VIDEOANALISIS", rows="1000", cols="6")
        sheet.append_row(HEADERS_VIDEOANALISIS)
        return sheet


def _upsert_videoanalisis(fecha, equipo, campo, valor):
    """Actualiza (o crea) la fila de FECHA+EQUIPO, escribiendo solo el campo indicado."""
    sheet = get_videoanalisis_sheet()
    all_v = sheet.get_all_values()
    headers = all_v[0] if all_v else HEADERS_VIDEOANALISIS
    idx_campo = headers.index(campo) + 1  # 1-based

    for i, row in enumerate(all_v[1:], start=2):
        if len(row) > 1 and row[0].strip() == fecha.strip() and row[1].strip() == equipo.strip():
            sheet.update_cell(i, idx_campo, valor)
            return
    nueva_fila = [''] * len(headers)
    nueva_fila[0] = fecha
    nueva_fila[1] = equipo
    nueva_fila[idx_campo - 1] = valor
    sheet.append_row(nueva_fila)


@videoanalisis_bp.route('/videoanalisis')
def videoanalisis():
    usuario = session.get('usuario')
    if not usuario:
        return redirect('/')
    perms = session.get('permisos', {})
    if perms.get('ENTRENAMIENTOS') != 'SI' and perms.get('D.DEPORTIVA') != 'SI' and usuario.lower() != 'admin':
        return "Acceso denegado", 403

    equipos_f11 = obtener_equipos_f11()
    return render_template('deportivo/videoanalisis.html', usuario=usuario, equipos_f11=equipos_f11)


@videoanalisis_bp.route('/api/videoanalisis', methods=['GET', 'POST'])
def api_videoanalisis():
    if request.method == 'GET':
        fecha = request.args.get('fecha', '').strip()
        if not fecha:
            return jsonify({"status": "error", "message": "Falta la fecha."}), 400
        try:
            sheet = get_videoanalisis_sheet()
            all_v = sheet.get_all_values()
            headers = all_v[0] if all_v else HEADERS_VIDEOANALISIS
            datos = {}
            for row in all_v[1:]:
                if len(row) > 1 and row[0].strip() == fecha:
                    equipo = row[1].strip()
                    item = {}
                    for j, h in enumerate(headers):
                        item[h] = row[j] if j < len(row) else ''
                    datos[equipo] = item
            return jsonify({"status": "success", "datos": datos})
        except Exception as e:
            print(f"Error api_videoanalisis GET: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    # POST: guarda un campo concreto de una celda (fecha + equipo + campo)
    try:
        datos = request.json or {}
        fecha = (datos.get('fecha') or '').strip()
        equipo = (datos.get('equipo') or '').strip()
        campo = (datos.get('campo') or '').strip().upper()
        valor = datos.get('valor', '')
        if not fecha or not equipo or campo not in HEADERS_VIDEOANALISIS:
            return jsonify({"status": "error", "message": "Datos incompletos."}), 400
        _upsert_videoanalisis(fecha, equipo, campo, valor)
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error api_videoanalisis POST: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@videoanalisis_bp.route('/api/videoanalisis/mi_equipo')
def api_videoanalisis_mi_equipo():
    usuario = session.get('usuario')
    if not usuario:
        return jsonify({"status": "error", "message": "No autenticado"}), 401
    equipo = session.get('equipo_defecto')
    if not equipo:
        return jsonify({"status": "success", "datos": []})
    try:
        sheet = get_videoanalisis_sheet()
        all_v = sheet.get_all_values()
        headers = all_v[0] if all_v else HEADERS_VIDEOANALISIS
        filas = []
        for row in all_v[1:]:
            if len(row) > 1 and row[1].strip() == equipo.strip():
                item = {}
                for j, h in enumerate(headers):
                    item[h] = row[j] if j < len(row) else ''
                # solo incluir si tiene algo de contenido relevante
                if item.get('MOMENTO_CON_BALON') or item.get('MOMENTO_SIN_BALON') or item.get('ANALISIS_ENTRENADOR'):
                    filas.append(item)

        def fecha_orden(item):
            try:
                return datetime.strptime(item.get('FECHA', ''), "%Y-%m-%d")
            except ValueError:
                return datetime.min
        filas.sort(key=fecha_orden, reverse=True)
        return jsonify({"status": "success", "datos": filas})
    except Exception as e:
        print(f"Error api_videoanalisis_mi_equipo: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@videoanalisis_bp.route('/analisis-postpartido', methods=['GET', 'POST'])
def analisis_postpartido():
    if request.method == 'GET':
        equipos_f11 = obtener_equipos_f11()
        return render_template('analisis_postpartido.html', equipos=equipos_f11)

    try:
        datos = request.json or {}
        fecha = (datos.get('fecha') or '').strip()
        equipo = (datos.get('equipo') or '').strip()
        analisis = (datos.get('analisis') or '').strip()
        if not fecha or not equipo or not analisis:
            return jsonify({"status": "error", "message": "Rellena equipo, fecha y análisis."}), 400
        _upsert_videoanalisis(fecha, equipo, 'ANALISIS_ENTRENADOR', analisis)
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error analisis_postpartido POST: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
