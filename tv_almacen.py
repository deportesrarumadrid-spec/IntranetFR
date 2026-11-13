"""Pantalla de la TV del almacén (Chromecast con TV Bro): muestra la imagen IDA Y VUELTA, dibujada en la propia TV con los
datos de la intranet (static/js/ida_vuelta.js), y la vuelve a dibujar cada 10 min si cambian.

- La TV entra con una clave (sin usuario): /tv?k=…
- Cada vez que cambian los datos, la TV avisa; si es la primera vez en el día, WhatsApp a Rubén "✅ TV actualizada".
- La TV da señales de vida cada 10 min. Si a 1 h no se sabe nada de ella, WhatsApp "⚠️ la TV no se ha actualizado"
  (una vez, hasta que vuelva).
Estado en datos_privados/tv_almacen.json.
"""
import os
import json
import hmac
import hashlib
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, render_template

tv_bp = Blueprint('tv_almacen', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'datos_privados', 'tv_almacen.json')
URL_BASE = 'https://intranet.clubfuentelarreyna.com'
SIN_SENAL_MIN = 60


def clave():
    return hashlib.sha256(((os.environ.get('FLASK_SECRET_KEY') or '') + '|tv_almacen').encode()).hexdigest()[:24]


def _clave_ok():
    return hmac.compare_digest(request.args.get('k', ''), clave())


def _leer():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _escribir(d):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def _avisar(texto):
    from informe_veo import _telefono_ruben
    from app import enviar_whatsapp
    tel = _telefono_ruben()
    if tel:
        enviar_whatsapp(tel, texto)


@tv_bp.route('/tv')
def pantalla():
    if not _clave_ok():
        return 'Sin permiso', 403
    return render_template('tv_almacen.html', k=clave())


@tv_bp.route('/api/tv/datos')
def api_datos():
    if not _clave_ok():
        return jsonify({'status': 'error'}), 403
    from competicion_scraper import construir_obj_semanales
    try:
        return jsonify({'status': 'ok', 'partidos': construir_obj_semanales()})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)[:200]}), 500


@tv_bp.route('/api/tv/senal', methods=['POST'])
def api_senal():
    """La TV avisa de que sigue viva y de la huella de lo que muestra."""
    if not _clave_ok():
        return jsonify({'status': 'error'}), 403
    huella = str((request.json or {}).get('huella', ''))[:64]
    ahora = datetime.now()
    d = _leer()
    d['ultima_senal'] = ahora.isoformat(timespec='minutes')
    aviso = None
    if d.get('sin_senal_avisado'):
        d['sin_senal_avisado'] = False
        aviso = '✅ La TV del almacén vuelve a estar conectada.'
    if huella and huella != d.get('huella'):
        primera = not d.get('huella')
        d['huella'] = huella
        d['actualizada'] = ahora.isoformat(timespec='minutes')
        if not primera and d.get('aviso_dia') != ahora.date().isoformat():
            d['aviso_dia'] = ahora.date().isoformat()
            aviso = '✅ TV del almacén actualizada con las clasificaciones nuevas.'
    _escribir(d)
    if aviso:
        try:
            _avisar(aviso)
        except Exception as e:
            print(f'[TV] aviso: {e}', flush=True)
    return jsonify({'status': 'ok'})


def tick(ahora):
    """Si la TV lleva 1 h sin dar señales, aviso (una vez)."""
    if ahora.minute % 10:
        return
    d = _leer()
    if not d.get('ultima_senal') or d.get('sin_senal_avisado'):
        return
    if ahora - datetime.fromisoformat(d['ultima_senal']) > timedelta(minutes=SIN_SENAL_MIN):
        d['sin_senal_avisado'] = True
        _escribir(d)
        _avisar(f"⚠️ La TV del almacén no se ha actualizado: no da señales desde las {d['ultima_senal'][11:16]} "
                f"del {d['ultima_senal'][8:10]}/{d['ultima_senal'][5:7]} (¿sin internet o apagada?).")


@tv_bp.route('/api/tv/estado')
def api_estado():
    from whatsapp_envios import _permitido
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    d = _leer()
    return jsonify({'status': 'ok', 'url': f'{URL_BASE}/tv?k={clave()}', 'ultima_senal': d.get('ultima_senal', ''),
                    'actualizada': d.get('actualizada', '')})
