from flask import Blueprint, render_template, request, jsonify, current_app
from datetime import datetime

formulario_partido_bp = Blueprint('formulario_partido_bp', __name__)

@formulario_partido_bp.route('/formulario-partido', methods=['GET', 'POST'])
def formulario_partido():
    # En GET, renderiza el formulario
    if request.method == 'GET':
        # Obtenemos la lista de equipos disponibles
        equipos = []
        try:
            from app import app as main_app
            client = main_app.gs_client
            sheet_eq = client.open(main_app.gs_name).worksheet("EQUIPO")
            rows_eq = sheet_eq.get_all_values()
            if rows_eq:
                headers = [str(h).strip().upper() for h in rows_eq[0]]
                if "EQUIPO" in headers:
                    idx_e = headers.index("EQUIPO")
                    equipos = sorted(list(set(str(r[idx_e]).strip() for r in rows_eq[1:] if len(r) > idx_e and r[idx_e].strip())))
        except Exception as e:
            print(f"Error al cargar equipos para el formulario: {e}")
            equipos = ["PRIMER EQUIPO", "BENJAMIN A", "ALEVIN A", "INFANTIL A"] # Fallback

        return render_template('formulario_partido.html', equipos=equipos)

    # En POST, guarda en Google Sheets
    if request.method == 'POST':
        try:
            data = request.json
            from app import app as main_app
            client = main_app.gs_client
            sh = client.open(main_app.gs_name)
            try:
                ws = sh.worksheet('FORMULARIO_PARTIDOS')
            except:
                ws = sh.add_worksheet(title='FORMULARIO_PARTIDOS', rows='1000', cols='20')
                ws.append_row(['MARCA TEMPORAL', 'EQUIPO', 'RIVAL', 'EVAL_GLOBAL', 'EVAL_INTENSIDAD', 'EVAL_FISICO', 'FALLOS_EQUIPO', 'MEJORAR_FISICO', 'PORT_NOTA', 'PORT_FALLOS', 'RIVAL_DESTACADO', 'NUESTROS_MEJORES', 'NUESTROS_FLOJOS', 'AUDIO_ENVIADO'])
            
            # Use provided date if exists, otherwise current time
            fecha_custom = data.get('fecha_partido', '').strip()
            if fecha_custom:
                # Assuming YYYY-MM-DD from input type date, we format it to match MARCA TEMPORAL (DD/MM/YYYY 12:00:00)
                try:
                    dt = datetime.strptime(fecha_custom, "%Y-%m-%d")
                    marca_temporal = dt.strftime("%d/%m/%Y 12:00:00")
                except:
                    marca_temporal = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            else:
                marca_temporal = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            
            row_data = [
                marca_temporal,
                data.get('equipo', ''),
                data.get('rival', ''),
                data.get('eval_global', ''),
                data.get('eval_intensidad', ''),
                data.get('eval_fisico', ''),
                data.get('fallos_equipo', ''),
                data.get('mejorar_fisico', ''),
                data.get('port_nota', ''),
                data.get('port_fallos', ''),
                data.get('rival_destacado', ''),
                data.get('nuestros_mejores', ''),
                data.get('nuestros_flojos', ''),
                data.get('audio_enviado', 'NO')
            ]
            
            ws.append_row(row_data, value_input_option='USER_ENTERED')
            return jsonify({'status': 'success', 'message': 'Guardado correctamente'})
            
        except Exception as e:
            print(f"Error al guardar formulario partido: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

@formulario_partido_bp.route('/api/formularios_partido', methods=['GET'])
def api_formularios_partido():
    try:
        from app import app as main_app
        client = main_app.gs_client
        sh = client.open(main_app.gs_name)
        try:
            ws = sh.worksheet('FORMULARIO_PARTIDOS')
            all_v = ws.get_all_values()
            if not all_v:
                return jsonify([])
            headers = [str(h).strip() for h in all_v[0]]
            data = []
            for row in all_v[1:]:
                row_dict = {}
                for i, h in enumerate(headers):
                    if i < len(row):
                        row_dict[h] = row[i]
                    else:
                        row_dict[h] = ""
                data.append(row_dict)
            return jsonify(data)
        except:
            return jsonify([])
    except Exception as e:
        print(f"Error api_formularios_partido: {e}")
        return jsonify({"error": str(e)}), 500
