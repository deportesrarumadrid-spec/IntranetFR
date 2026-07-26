import os
from flask import Blueprint, request, session, jsonify

import competicion_scraper as cs

horarios_bp = Blueprint('horarios_bp', __name__)

RFFM_USER = os.environ.get('RFFM_USER', '')
RFFM_PASS = os.environ.get('RFFM_PASS', '')

CLUB_ID = '4239'  # CLUB FUENTELARREYNA
CAMPO_PROPIO = '106'  # VALDEYEROS (HA)

# Minutos de duración estimada por categoría (para sugerir la hora del siguiente partido).
OFFSETS_MINUTOS = {
    'PREBENJAMIN': 60,
    'BENJAMIN': 60,
    'ALEVIN': 75,
    'ALEVIN F7': 75,
    'INFANTIL': 85,
    'CADETE': 95,
    'JUVENIL': 105,
}

HORARIOS_HEADERS = ["SEMANA_INICIO", "DIA", "DEPORTE", "FILA", "HORA", "CATEG", "LOCAL", "VISITANTE"]


def _rffm_session():
    sess = cs.get_session()
    sess.post('https://intranet.ffmadrid.es/nfg/NLogin', data={
        "NUser": RFFM_USER, "NPass": RFFM_PASS, "LoginAjax": "1"
    })
    return sess


@horarios_bp.route('/api/horarios/offsets')
def api_horarios_offsets():
    if not session.get('usuario'):
        return jsonify({"status": "error", "message": "No session"}), 401
    return jsonify(OFFSETS_MINUTOS)


@horarios_bp.route('/api/horarios/opciones')
def api_horarios_opciones():
    if not session.get('usuario'):
        return jsonify({"status": "error", "message": "No session"}), 401

    fecha_desde = request.args.get('desde')  # DD-MM-YYYY
    fecha_hasta = request.args.get('hasta')  # DD-MM-YYYY
    if not fecha_desde or not fecha_hasta:
        return jsonify({"status": "error", "message": "Faltan fechas (desde/hasta)"}), 400

    try:
        sess = _rffm_session()
        cod_temporada, _, _ = cs._temporada_actual()
        matches = cs.fetch_temporada_matches(
            sess, club_id=CLUB_ID, cod_temporada=cod_temporada,
            fecha_desde=fecha_desde, fecha_hasta=fecha_hasta
        )

        opciones = []
        for m in matches:
            campo_raw = m.get('campo', '')
            cod_campo = campo_raw.split(' - ')[0].strip()
            if cod_campo != CAMPO_PROPIO:
                continue

            cat, letra = cs.detect_categoria_y_letra(m['competicion'])
            if not cat:
                continue

            local = m['nombre_casa']
            visitante = m['nombre_fuera']
            rival = visitante if 'FUENTELARREYNA' in local.upper() else local
            deporte_raw = (m.get('deporte') or '').upper().replace(' ', '').replace('Ú', 'U')
            deporte = 'F7' if 'FUTBOL-7' in deporte_raw or 'F7' in deporte_raw else 'F11'

            etiqueta_cat = f"{cat} {letra}".strip()
            opciones.append({
                "fecha": m['fecha'], "deporte": deporte,
                "categoria": cat, "letra": letra, "categoria_label": etiqueta_cat,
                "rival": rival, "local": local, "visitante": visitante,
                "hora_rffm": m['hora'],
                "label": f"{etiqueta_cat} - {rival}"
            })

        opciones.sort(key=lambda o: (o['fecha'], o['hora_rffm']))
        return jsonify(opciones)
    except Exception as e:
        print(f"Error en api_horarios_opciones: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


def _get_horarios_sheet():
    from app import client, NOMBRE_EXCEL
    spreadsheet = client.open(NOMBRE_EXCEL)
    todas_hojas = {s.title.upper().strip(): s for s in spreadsheet.worksheets()}
    if "HORARIOS" in todas_hojas:
        return todas_hojas["HORARIOS"]
    sheet = spreadsheet.add_worksheet(title="HORARIOS", rows="1000", cols=str(len(HORARIOS_HEADERS)))
    sheet.append_row(HORARIOS_HEADERS)
    return sheet


@horarios_bp.route('/api/horarios/plantilla')
def api_horarios_plantilla():
    if not session.get('usuario'):
        return jsonify({"status": "error", "message": "No session"}), 401
    semana = request.args.get('semana')  # YYYY-MM-DD (viernes de esa semana)
    if not semana:
        return jsonify({"status": "error", "message": "Falta semana"}), 400
    try:
        sheet = _get_horarios_sheet()
        all_v = sheet.get_all_values()
        if len(all_v) < 2:
            return jsonify([])
        headers = [h.strip().upper() for h in all_v[0]]
        idx = {h: i for i, h in enumerate(headers)}
        filas = []
        for row in all_v[1:]:
            if not any(str(c).strip() for c in row):
                continue
            if len(row) <= idx.get('SEMANA_INICIO', 0) or row[idx['SEMANA_INICIO']].strip() != semana:
                continue
            filas.append({
                "dia": row[idx['DIA']] if idx.get('DIA') is not None and len(row) > idx['DIA'] else "",
                "deporte": row[idx['DEPORTE']] if idx.get('DEPORTE') is not None and len(row) > idx['DEPORTE'] else "",
                "fila": row[idx['FILA']] if idx.get('FILA') is not None and len(row) > idx['FILA'] else "",
                "hora": row[idx['HORA']] if idx.get('HORA') is not None and len(row) > idx['HORA'] else "",
                "categ": row[idx['CATEG']] if idx.get('CATEG') is not None and len(row) > idx['CATEG'] else "",
                "local": row[idx['LOCAL']] if idx.get('LOCAL') is not None and len(row) > idx['LOCAL'] else "",
                "visitante": row[idx['VISITANTE']] if idx.get('VISITANTE') is not None and len(row) > idx['VISITANTE'] else "",
            })
        return jsonify(filas)
    except Exception as e:
        print(f"Error en api_horarios_plantilla: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@horarios_bp.route('/api/horarios/guardar', methods=['POST'])
def api_horarios_guardar():
    if not session.get('usuario'):
        return jsonify({"status": "error", "message": "No session"}), 401
    data = request.json or {}
    semana = str(data.get('semana', '')).strip()
    filas = data.get('filas', [])
    if not semana:
        return jsonify({"status": "error", "message": "Falta semana"}), 400
    try:
        sheet = _get_horarios_sheet()
        all_v = sheet.get_all_values()
        headers = [h.strip().upper() for h in all_v[0]] if all_v else HORARIOS_HEADERS
        idx_semana = headers.index('SEMANA_INICIO') if 'SEMANA_INICIO' in headers else 0

        # Eliminar filas existentes de esa semana (de abajo hacia arriba para no desordenar índices)
        filas_a_borrar = [i for i, row in enumerate(all_v[1:], start=2)
                           if len(row) > idx_semana and row[idx_semana].strip() == semana]
        for row_num in reversed(filas_a_borrar):
            sheet.delete_rows(row_num)

        nuevas = []
        for f in filas:
            if not (f.get('hora') or f.get('categ')):
                continue
            nuevas.append([
                semana, f.get('dia', ''), f.get('deporte', ''), str(f.get('fila', '')),
                f.get('hora', ''), f.get('categ', ''), f.get('local', ''), f.get('visitante', '')
            ])
        if nuevas:
            sheet.append_rows(nuevas, value_input_option='USER_ENTERED')

        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error en api_horarios_guardar: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
