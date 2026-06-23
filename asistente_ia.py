import json
from datetime import datetime
from flask import Blueprint, request, jsonify, session

try:
    import google.generativeai as genai
except ImportError:
    genai = None

from financiero import leer_hoja_limpia

asistente_bp = Blueprint('asistente_bp', __name__)

HOJAS_CONTEXTO = ["JUGADORES", "PAGOS JUGADORES", "DATOS JUGADORES", "STAFF", "PAGOS STAFF"]


def _elegir_modelo():
    try:
        modelos_visibles = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        return next((m for m in modelos_visibles if 'gemini-1.5-flash' in m),
               next((m for m in modelos_visibles if 'flash' in m),
               next((m for m in modelos_visibles if 'gemini-1.5-pro' in m),
               'models/gemini-1.5-flash')))
    except Exception:
        return 'models/gemini-1.5-flash'


def _recopilar_contexto():
    from app import client, NOMBRE_EXCEL
    contexto = {}
    for hoja in HOJAS_CONTEXTO:
        try:
            contexto[hoja] = leer_hoja_limpia(client, NOMBRE_EXCEL, hoja)
        except Exception:
            contexto[hoja] = []
    return contexto


@asistente_bp.route('/api/asistente/preguntar', methods=['POST'])
def preguntar():
    if session.get('usuario', '').lower() != 'admin':
        return jsonify({"status": "error", "message": "No autorizado"}), 403

    if not genai:
        return jsonify({"status": "error", "message": "Librería IA no detectada en el servidor."}), 500

    data = request.json or {}
    pregunta = (data.get('pregunta') or '').strip()
    historial = data.get('historial') or []
    if not pregunta:
        return jsonify({"status": "error", "message": "Pregunta vacía"}), 400

    contexto = _recopilar_contexto()
    hoy = datetime.now().strftime('%d/%m/%Y')

    historial_txt = "".join(f"\n{h.get('rol', 'usuario').upper()}: {h.get('texto', '')}" for h in historial[-6:])

    prompt = f"""
Eres el asistente de datos interno de la Escuela de Fútbol Club Fuentelarreyna. Respondes en español, de forma breve y directa, basándote EXCLUSIVAMENTE en los datos JSON proporcionados a continuación (jugadores, pagos, fichas de contacto y staff). Hoy es {hoy}.

Reglas:
- Si la pregunta menciona un mes (ej. "septiembre"), interpreta el CONCEPTO de pago como su número de mes con dos cifras (enero=01, ..., diciembre=12), salvo "Inscripción" que es un concepto aparte.
- Un pago se considera realizado si PAGADO es mayor o igual que ESPERADO (siendo ESPERADO > 0), o si hay un registro con PAGADO > 0 para ese concepto.
- Si no encuentras el dato pedido en el JSON, dilo claramente en lugar de inventarlo.
- Sé conciso: responde en 1-3 frases, sin repetir la pregunta ni explicar tu razonamiento.

DATOS (JSON):
{json.dumps(contexto, ensure_ascii=False, default=str)}

CONVERSACIÓN PREVIA:{historial_txt if historial_txt else ' (ninguna)'}

PREGUNTA NUEVA DEL USUARIO:
{pregunta}
"""

    try:
        model = genai.GenerativeModel(model_name=_elegir_modelo())
        response = model.generate_content(prompt)
        respuesta = (response.text or '').strip()
        return jsonify({"status": "success", "respuesta": respuesta})
    except Exception as e:
        print(f"Error en asistente IA: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
