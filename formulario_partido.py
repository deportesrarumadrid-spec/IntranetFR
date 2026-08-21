from flask import Blueprint, render_template, request, jsonify, current_app
from datetime import datetime
import os, json, re

_JDM_PATH = os.path.join(os.path.dirname(__file__), 'static', 'data', 'jugador_del_mes.json')

_JDM_MESES = [
    ('SEP','09'),('OCT','10'),('NOV','11'),('DIC','12'),
    ('ENE','01'),('FEB','02'),('MAR','03'),('ABR','04'),('MAY','05'),('JUN','06'),
]

def _jdm_default():
    ahora = datetime.now()
    y_sep = ahora.year if ahora.month >= 9 else ahora.year - 1
    out = []
    for label, num in _JDM_MESES:
        y = y_sep if int(num) >= 9 else y_sep + 1
        out.append({
            'mes': label, 'yearmon': f'{y}-{num}',
            'f7':      [{'nombre':'','ganador':False},{'nombre':'','ganador':False}],
            'once':    [{'nombre':'','ganador':False},{'nombre':'','ganador':False}],
            'porteros':[{'nombre':'','ganador':False},{'nombre':'','ganador':False}],
            'entrenador': '', 'gol': ''
        })
    return out

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

        equipo_default = request.args.get('equipo', '').strip()
        fecha_default  = request.args.get('fecha', '').strip()
        return render_template('formulario_partido.html', equipos=equipos,
                               equipo_default=equipo_default, fecha_default=fecha_default)

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


@formulario_partido_bp.route('/api/jugador_del_mes', methods=['GET', 'POST'])
def api_jugador_del_mes():
    if request.method == 'GET':
        try:
            if os.path.exists(_JDM_PATH):
                with open(_JDM_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = _jdm_default()
            return jsonify({'status': 'success', 'data': data})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
    else:
        try:
            data = request.json
            os.makedirs(os.path.dirname(_JDM_PATH), exist_ok=True)
            with open(_JDM_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return jsonify({'status': 'success'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500


@formulario_partido_bp.route('/api/jugador_del_mes/sugerencias', methods=['GET'])
def api_jugador_del_mes_sugerencias():
    try:
        from app import app as main_app
        client = main_app.gs_client
        sh = client.open(main_app.gs_name)
        try:
            ws = sh.worksheet('FORMULARIO_PARTIDOS')
            all_v = ws.get_all_values()
        except Exception:
            return jsonify({'status': 'success', 'data': {}})

        if not all_v:
            return jsonify({'status': 'success', 'data': {}})

        headers = [str(h).strip() for h in all_v[0]]

        def _idx(col):
            return headers.index(col) if col in headers else -1

        idx_fecha   = _idx('MARCA TEMPORAL')
        idx_equipo  = _idx('EQUIPO')
        idx_mejores = _idx('NUESTROS_MEJORES')

        agregado = {}  # {yearmon: {equipo: {nombre_lower: {nombre, count}}}}

        for row in all_v[1:]:
            if idx_fecha < 0 or idx_equipo < 0 or idx_mejores < 0:
                continue
            fecha_str   = row[idx_fecha].strip()   if idx_fecha   < len(row) else ''
            equipo      = row[idx_equipo].strip()  if idx_equipo  < len(row) else ''
            mejores_raw = row[idx_mejores].strip() if idx_mejores < len(row) else ''
            if not fecha_str or not equipo or not mejores_raw:
                continue
            try:
                parts = fecha_str.split(' ')[0].split('/')
                yearmon = f'{parts[2]}-{parts[1].zfill(2)}' if len(parts) == 3 else None
            except Exception:
                yearmon = None
            if not yearmon:
                continue
            nombres = [n.strip() for n in re.split(r'[,\n]+', mejores_raw) if n.strip()]
            bucket = agregado.setdefault(yearmon, {}).setdefault(equipo, {})
            for nombre in nombres:
                key = nombre.lower()
                if key not in bucket:
                    bucket[key] = {'nombre': nombre, 'count': 0}
                bucket[key]['count'] += 1

        resultado = {}
        for ym, equipos in agregado.items():
            resultado[ym] = {}
            for eq, jugs in equipos.items():
                resultado[ym][eq] = sorted(jugs.values(), key=lambda x: x['count'], reverse=True)

        return jsonify({'status': 'success', 'data': resultado})
    except Exception as e:
        print(f"Error api_jugador_del_mes_sugerencias: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
