"""Sube la agenda del finde (Fútbol 11 y Fútbol 7) como historias de Instagram (@clubfuentelarreyna) los miércoles
a las 17:00, y avisa a Rubén Pozón por WhatsApp con las dos imágenes y si se han subido o no.

API oficial de Instagram (inicio de sesión con Instagram): las imágenes se dejan un rato en una URL pública con nombre
aleatorio (static/publico/), se crea el contenedor STORIES y se publica. La clave va en el .env del servidor
(INSTAGRAM_TOKEN, INSTAGRAM_USER_ID) y se renueva sola cada semana (la renovada se guarda en datos_privados/instagram.json).
"""
import os
import json
import time
import secrets
from datetime import datetime, timedelta
import requests
from flask import Blueprint, request, jsonify

instagram_bp = Blueprint('instagram_historias', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'datos_privados', 'instagram.json')
PUBLICO_DIR = os.path.join(BASE_DIR, 'static', 'publico')
URL_BASE = 'https://intranet.clubfuentelarreyna.com'
API_VERSION = 'v21.0'
DIA_ENVIO, HORA_ENVIO = 2, (17, 0)  # miércoles 17:00


def _leer():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault('activo', True)
    return d


def _escribir(d):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def _token():
    """La clave renovada (si la hay y es de la misma que está en el .env) o la del .env."""
    env = (os.environ.get('INSTAGRAM_TOKEN') or '').strip()
    d = _leer()
    if d.get('token') and d.get('token_origen') == env[-12:]:
        return d['token']
    return env


def _api():
    """Las claves de 'inicio de sesión con Instagram' (IG...) van a graph.instagram.com; las de Facebook (EAA...) a graph.facebook.com."""
    host = 'graph.facebook.com' if _token().startswith('EAA') else 'graph.instagram.com'
    return f'https://{host}/{API_VERSION}'


def _error(r):
    try:
        e = r.json().get('error') or {}
        return e.get('error_user_msg') or e.get('message') or r.text[:200]
    except Exception:
        return r.text[:200]


def cuenta():
    """(ok, '@usuario' o motivo del error)."""
    if not _token():
        return False, 'Falta conectar Instagram (no hay clave en el servidor)'
    try:
        # Con clave de Facebook (EAA...) "me" es el usuario del sistema: se pregunta directamente por la cuenta de Instagram
        uid = (os.environ.get('INSTAGRAM_USER_ID') or '').strip() if _token().startswith('EAA') else ''
        r = requests.get(f'{_api()}/{uid or "me"}', params={'fields': 'username' if uid else 'user_id,username',
                                                             'access_token': _token()}, timeout=20)
        if r.status_code != 200:
            return False, _error(r)
        return True, '@' + r.json().get('username', '')
    except Exception as e:
        return False, str(e)[:200]


def _user_id():
    uid = (os.environ.get('INSTAGRAM_USER_ID') or '').strip()
    if uid:
        return uid
    r = requests.get(f'{_api()}/me', params={'fields': 'user_id', 'access_token': _token()}, timeout=20)
    return str(r.json().get('user_id') or r.json().get('id') or '')


def renovar_token():
    """Las claves de Instagram duran 60 días: se renuevan cada semana (sin cambiar nada en el .env)."""
    tok = _token()
    if not tok or tok.startswith('EAA'):
        return
    d = _leer()
    if d.get('renovado') and datetime.now() - datetime.fromisoformat(d['renovado']) < timedelta(days=7):
        return
    r = requests.get('https://graph.instagram.com/refresh_access_token',
                     params={'grant_type': 'ig_refresh_token', 'access_token': tok}, timeout=20)
    if r.status_code == 200 and r.json().get('access_token'):
        d['token'] = r.json()['access_token']
        d['token_origen'] = (os.environ.get('INSTAGRAM_TOKEN') or '').strip()[-12:]
        d['renovado'] = datetime.now().isoformat(timespec='minutes')
        _escribir(d)
        print('[INSTAGRAM] Clave renovada', flush=True)
    else:
        print(f'[INSTAGRAM] No se pudo renovar la clave: {_error(r)}', flush=True)


def _url_publica(png):
    """Guarda la imagen con nombre aleatorio en static/publico/ y borra las de más de 2 días."""
    os.makedirs(PUBLICO_DIR, exist_ok=True)
    for f in os.listdir(PUBLICO_DIR):
        p = os.path.join(PUBLICO_DIR, f)
        if time.time() - os.path.getmtime(p) > 2 * 86400:
            try:
                os.remove(p)
            except OSError:
                pass
    nombre = secrets.token_urlsafe(24) + '.png'
    with open(os.path.join(PUBLICO_DIR, nombre), 'wb') as f:
        f.write(png)
    return f'{URL_BASE}/static/publico/{nombre}'


def subir_historia(png):
    """(ok, detalle): publica la imagen como historia."""
    tok, api = _token(), _api()
    if not tok:
        return False, 'Falta conectar Instagram (no hay clave en el servidor)'
    uid = _user_id()
    r = requests.post(f'{api}/{uid}/media', data={'image_url': _url_publica(png), 'media_type': 'STORIES',
                                                  'access_token': tok}, timeout=60)
    if r.status_code != 200 or not r.json().get('id'):
        return False, _error(r)
    contenedor = r.json()['id']
    for _ in range(20):  # Instagram descarga y procesa la imagen: esperar a que esté lista
        e = requests.get(f'{api}/{contenedor}', params={'fields': 'status_code', 'access_token': tok}, timeout=20)
        estado = (e.json() if e.status_code == 200 else {}).get('status_code')
        if estado == 'FINISHED':
            break
        if estado == 'ERROR':
            return False, 'Instagram no ha podido procesar la imagen'
        time.sleep(3)
    r = requests.post(f'{api}/{uid}/media_publish', data={'creation_id': contenedor, 'access_token': tok}, timeout=60)
    if r.status_code != 200 or not r.json().get('id'):
        return False, _error(r)
    return True, r.json()['id']


def _tel_ruben():
    from informe_veo import _telefono_ruben
    tel = ''.join(c for c in _telefono_ruben() if c.isdigit())
    return '34' + tel if len(tel) == 9 else tel


def subir_agenda():
    """Genera la agenda del próximo finde, sube F11 y luego F7 a historias y avisa a Rubén. Devuelve (ok, texto)."""
    from agenda_finde import generar
    from objetivos_finde import enviar_imagen
    from app import enviar_whatsapp
    g = generar()
    fallos, subidas = [], []
    for fmt, nombre in (('F11', 'Fútbol 11'), ('F7', 'Fútbol 7')):
        if not g['n'][fmt]:
            continue  # sin partidos de ese formato: no se sube
        try:
            ok, det = subir_historia(g[fmt])
        except Exception as e:
            ok, det = False, str(e)[:200]
        (subidas if ok else fallos).append(nombre if ok else f'{nombre}: {det}')
    if not subidas and not fallos:
        fallos.append('no hay partidos ese fin de semana')
    texto = (f"✅ Agenda {g['titulo']} subida a las historias de Instagram ({', '.join(subidas)})" if not fallos else
             f"⚠️ No se ha podido subir la agenda {g['titulo']} a Instagram:\n" + '\n'.join(f'• {f}' for f in fallos)
             + (f"\n(Sí se ha subido: {', '.join(subidas)})" if subidas else ''))
    tel = _tel_ruben()
    if tel:
        for fmt, nombre in (('F11', 'FÚTBOL 11'), ('F7', 'FÚTBOL 7')):
            if g['n'][fmt]:
                enviar_imagen(f'{tel}@c.us', g[fmt], f"AGENDA {g['titulo']} · {nombre}")
        enviar_whatsapp(tel, texto)
    d = _leer()
    d['ultima_subida'] = {'fecha': datetime.now().isoformat(timespec='minutes'), 'ok': not fallos, 'texto': texto}
    _escribir(d)
    print(f'[INSTAGRAM] {texto}', flush=True)
    return not fallos, texto


def tick(ahora):
    """Lo llama push_scheduler cada minuto: miércoles, una vez, en la media hora siguiente a las 17:00."""
    if ahora.minute == 30 and ahora.hour == 4:
        try:
            renovar_token()
        except Exception as e:
            print(f'[INSTAGRAM] renovar: {e}', flush=True)
    if ahora.weekday() != DIA_ENVIO:
        return
    d = _leer()
    if not d.get('activo'):
        return
    inicio = ahora.replace(hour=HORA_ENVIO[0], minute=HORA_ENVIO[1], second=0, microsecond=0)
    hoy = ahora.date().isoformat()
    if d.get('ultimo') == hoy or not (inicio <= ahora < inicio + timedelta(minutes=30)):
        return
    d['ultimo'] = hoy  # marcar antes de subir: nunca dos veces el mismo miércoles
    _escribir(d)
    subir_agenda()


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@instagram_bp.route('/api/whatsapp/instagram', methods=['GET', 'POST'])
def api_estado():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    d = _leer()
    if request.method == 'POST':
        d['activo'] = bool((request.json or {}).get('activo'))
        _escribir(d)
    ok, cta = cuenta()
    return jsonify({'status': 'ok', 'activo': d['activo'], 'conectado': ok, 'cuenta': cta,
                    'ultima': d.get('ultima_subida') or {}})


@instagram_bp.route('/api/whatsapp/instagram/subir', methods=['POST'])
def api_subir():
    """Sube ya la agenda a historias de Instagram (y avisa a Rubén)."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    ok, cta = cuenta()
    if not ok:
        return jsonify({'status': 'error', 'message': cta})
    # En segundo plano: generar y subir puede pasar del minuto. El resultado llega por WhatsApp.
    import threading
    threading.Thread(target=subir_agenda, daemon=True).start()
    return jsonify({'status': 'ok', 'message': f'Subiendo a {cta}… En un par de minutos te llega el WhatsApp con el resultado.'})
