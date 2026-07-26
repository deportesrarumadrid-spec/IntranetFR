from flask import Blueprint, request, jsonify, current_app
import json, os
from datetime import datetime

loteria_bp = Blueprint('loteria_bp', __name__)

def _dd():
    return os.path.join(current_app.root_path, 'static', 'data')

def _path(temp):
    s = str(temp).replace('/', '-').replace('_', '-').replace(' ', '-')
    return os.path.join(_dd(), f'loteria_{s}.json')

def _calc(pagado):
    p = float(pagado or 0)
    return round(p * 4 / 5), round(p * 1 / 5)

@loteria_bp.route('/api/loteria/temporadas', methods=['GET'])
def lot_list():
    dd = _dd()
    os.makedirs(dd, exist_ok=True)
    temps = []
    for f in sorted(os.listdir(dd)):
        if f.startswith('loteria_') and f.endswith('.json'):
            try:
                with open(os.path.join(dd, f), encoding='utf-8') as fh:
                    d = json.load(fh)
                    temps.append(d.get('temporada', ''))
            except:
                pass
    return jsonify(temps)

@loteria_bp.route('/api/loteria/temporadas', methods=['POST'])
def lot_new():
    d = request.get_json() or {}
    nombre = str(d.get('nombre', '')).strip()
    if not nombre:
        return jsonify({"error": "Nombre requerido"}), 400
    path = _path(nombre)
    if os.path.exists(path):
        return jsonify({"error": "Ya existe"}), 409

    tacos = []
    try:
        client = current_app.gs_client
        sheet = client.open(current_app.gs_name).worksheet("JUGADORES")
        vals = sheet.get_all_values()
        if vals:
            h = [x.strip().upper() for x in vals[0]]
            fn = h.index('NOMBRE') if 'NOMBRE' in h else 0
            fe = h.index('EQUIPO') if 'EQUIPO' in h else 1
            i = 0
            for row in vals[1:]:
                if len(row) > max(fn, fe) and row[fn].strip():
                    n0 = i * 10 + 1
                    tacos.append({
                        "id": i + 1,
                        "num_inicio": n0, "num_fin": n0 + 9,
                        "persona": row[fn].strip(), "equipo": row[fe].strip(),
                        "fecha_entrega": "", "estado": "",
                        "pagado": 0, "lot": 0, "fr": 0, "historial": []
                    })
                    i += 1
    except Exception as e:
        print(f"loteria jugadores: {e}")

    os.makedirs(_dd(), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump({"temporada": nombre, "tacos": tacos}, fh, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "tacos": len(tacos)})

@loteria_bp.route('/api/loteria/<temporada>', methods=['GET'])
def lot_get(temporada):
    path = _path(temporada)
    if not os.path.exists(path):
        return jsonify({"temporada": temporada, "tacos": []})
    with open(path, encoding='utf-8') as fh:
        return jsonify(json.load(fh))

@loteria_bp.route('/api/loteria/<temporada>', methods=['PATCH'])
def lot_patch(temporada):
    d = request.get_json() or {}
    path = _path(temporada)
    if not os.path.exists(path):
        return jsonify({"error": "no encontrada"}), 404
    with open(path, encoding='utf-8') as fh:
        doc = json.load(fh)
    if 'num_papeletas' in d:
        doc['num_papeletas'] = max(10, int(d['num_papeletas']))
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    return jsonify({"ok": True})

@loteria_bp.route('/api/loteria/<temporada>/taco', methods=['POST'])
def lot_post_taco(temporada):
    d = request.get_json() or {}
    path = _path(temporada)
    if not os.path.exists(path):
        return jsonify({"error": "no encontrada"}), 404
    with open(path, encoding='utf-8') as fh:
        doc = json.load(fh)
    n_inicio = int(d.get('num_inicio', 0))
    n_fin = int(d.get('num_fin', n_inicio + 9))
    if any(t['num_inicio'] == n_inicio for t in doc['tacos']):
        return jsonify({"error": "ya existe"}), 409
    nuevo_id = max((t['id'] for t in doc['tacos']), default=0) + 1
    taco = {
        "id": nuevo_id,
        "num_inicio": n_inicio, "num_fin": n_fin,
        "persona": str(d.get('persona', '')).strip(),
        "equipo": str(d.get('equipo', '')).strip(),
        "fecha_entrega": str(d.get('fecha_entrega', '')).strip(),
        "estado": str(d.get('estado', '')).strip(),
        "pagado": float(d.get('pagado', 0)),
        "lot": 0, "fr": 0, "historial": []
    }
    taco['lot'], taco['fr'] = _calc(taco['pagado'])
    doc['tacos'].append(taco)
    doc['tacos'].sort(key=lambda x: x['num_inicio'])
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "taco": taco})

@loteria_bp.route('/api/loteria/<temporada>/<int:tid>', methods=['PUT'])
def lot_put(temporada, tid):
    d = request.get_json() or {}
    path = _path(temporada)
    with open(path, encoding='utf-8') as fh:
        doc = json.load(fh)
    taco = next((t for t in doc['tacos'] if t['id'] == tid), None)
    if not taco:
        return jsonify({"error": "no encontrado"}), 404

    hist = {
        "ts": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "de": f"{taco.get('estado','—')} {taco.get('pagado',0)}€",
        "a": f"{d.get('estado', taco.get('estado',''))} {d.get('pagado', taco.get('pagado',0))}€"
    }
    taco.setdefault('historial', []).append(hist)

    for k in ('estado', 'fecha_entrega', 'persona', 'equipo'):
        if k in d:
            taco[k] = str(d[k]).strip()
    if 'pagado' in d:
        taco['pagado'] = float(d['pagado'])
        taco['lot'], taco['fr'] = _calc(taco['pagado'])

    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "taco": taco})
