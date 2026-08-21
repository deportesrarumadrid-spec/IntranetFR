import os
import re
import json
import uuid
import base64
from datetime import datetime, timedelta
import unicodedata
import gspread
import time
from gspread.exceptions import APIError
from gspread.http_client import HTTPClient

original_request = HTTPClient.request

def retry_request(self, method, endpoint, *args, **kwargs):
    retries = 5
    backoff = 2.0
    for attempt in range(retries):
        try:
            return original_request(self, method, endpoint, *args, **kwargs)
        except APIError as e:
            is_rate_limit = False
            if hasattr(e, 'response') and e.response is not None:
                if e.response.status_code == 429:
                    is_rate_limit = True
            elif '429' in str(e):
                is_rate_limit = True
            
            if is_rate_limit and attempt < retries - 1:
                print(f"⚠️ Google Sheets API 429 (Límite Excedido). Reintentando en {backoff}s... (Intento {attempt+1}/{retries})")
                time.sleep(backoff)
                backoff *= 1.5
            else:
                raise e

HTTPClient.request = retry_request

from google.oauth2.service_account import Credentials as ServiceAccountCredentials
import requests # ¡IMPORTANTE! Añadir esta línea
try:
    from dotenv import load_dotenv
    load_dotenv() # Carga las variables desde un archivo .env si existe
except ImportError:
    pass
from flask import Flask, render_template, request, redirect, url_for, session, jsonify

from perfiles import get_perfiles_sheet # Importamos la función para obtener la hoja de perfiles

_PERFILES_CACHE = {'records': None, 'ts': 0}
_PERFILES_TTL = 300  # 5 minutos

def _get_perfiles_records():
    now = time.time()
    if _PERFILES_CACHE['records'] is None or now - _PERFILES_CACHE['ts'] > _PERFILES_TTL:
        _PERFILES_CACHE['records'] = get_perfiles_sheet().get_all_records()
        _PERFILES_CACHE['ts'] = now
    return _PERFILES_CACHE['records']

def _invalidar_cache_perfiles():
    _PERFILES_CACHE['records'] = None

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
_secret = os.environ.get('FLASK_SECRET_KEY')
if not _secret:
    raise RuntimeError("FLASK_SECRET_KEY no está definida en las variables de entorno")
app.secret_key = _secret
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Límite de 16MB para subidas
app.permanent_session_lifetime = timedelta(hours=8)

# --- LISTAS MAESTRAS DE IDENTIFICACIÓN ---
DIAS_MESES_IDENT = [
    'LUNES', 'MARTES', 'MIERCOLES', 'JUEVES', 'VIERNES', 'SABADO', 'DOMINGO',
    'L', 'M', 'X', 'J', 'V', 'S', 'D', 'LU', 'MA', 'MI', 'JU', 'VI', 'SA', 'DO', 'MA.', 'MAR.', 'MART.',
    'LUN', 'MAR', 'MIE', 'JUE', 'VIE', 'SAB', 'DOM', 'MART', 'MIER', 'SABA', 'MARS', 'MÁRTES',
    'MARTE', 'MARTES', 'TUE', 'TUES', 'TUESDAY', 'DT',
    'ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE',
    'ENE', 'FEB', 'MAR', 'ABR', 'MAY', 'JUN', 'JUL', 'AGO', 'SEP', 'OCT', 'NOV', 'DIC',
    'E', 'F', 'A', 'MY', 'JL', 'AG', 'O', 'N', 'MR', 'MZ', 'AB', 'MY', 'JN', 'JL', 'AG', 'S', 'O', 'N', 'D', 'M',
    'TODOS', 'TODO', 'TOTAL', 'ALL', 'SELECCIONARTODOS'
]

# --- UTILIDADES DE NOTIFICACIÓN ---
def normalizar_id(texto):
    """Normaliza IDs de usuario quitando tildes, pasando a minúsculas y colapsando espacios."""
    if not texto: return ""
    s = "".join(c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn')
    # Colapsar múltiples espacios internos para mayor robustez en búsquedas
    return " ".join(s.lower().split()).strip()

def es_dia_valido_crono(texto):
    """Verifica si un texto corresponde a un día de la semana de forma robusta."""
    if not texto: return False
    # Normalización extrema: quitamos tildes, espacios y caracteres no imprimibles
    t = "".join(c for c in unicodedata.normalize('NFD', str(texto).upper().strip().replace('\xa0', ' ')) if unicodedata.category(c) != 'Mn')
    return t in DIAS_MESES_IDENT

def normalizar_dia_cronograma(val, vista='SEMANAL'):
    """Asegura formato estándar para visualización (MAYÚSCULAS, sin espacios). Soporta meses para vista ANUAL."""
    if not val: return ""
    # Limpieza agresiva: quitamos puntos, espacios y normalizamos a mayúsculas
    t = str(val).strip().upper().replace('\xa0', ' ').replace('.', '')
    # Normalizamos para comparar sin acentos pero devolver con acentos correctos
    base = "".join(c for c in unicodedata.normalize('NFD', t) if unicodedata.category(c) != 'Mn')
    
    if str(vista).upper() == 'ANUAL':
        mapping_meses = {
            "E": "ENERO", "ENE": "ENERO",
            "F": "FEBRERO", "FEB": "FEBRERO",
            "M": "MARZO", "MAR": "MARZO", "MR": "MARZO", "MZ": "MARZO", # En anual M es Marzo por defecto
            "A": "ABRIL", "ABR": "ABRIL", "AB": "ABRIL", 
            "MY": "MAYO", "MAY": "MAYO", "MA": "MAYO",
            "J": "JUNIO", "JUN": "JUNIO", "JN": "JUNIO",
            "JL": "JULIO", "JUL": "JULIO", "JY": "JULIO", "JULI": "JULIO",
            "AG": "AGOSTO", "AGO": "AGOSTO",
            "S": "SEPTIEMBRE", "SEP": "SEPTIEMBRE",
            "O": "OCTUBRE", "OCT": "OCTUBRE",
            "N": "NOVIEMBRE", "NOV": "NOVIEMBRE",
            "D": "DICIEMBRE", "DIC": "DICIEMBRE"
        }
        res = mapping_meses.get(base, mapping_meses.get(t))
        return res if res else base

    mapping = {
        "L": "LUNES", "LU": "LUNES", "LUN": "LUNES", "LUNES": "LUNES",
        "M": "MARTES", "MA": "MARTES", "MAR": "MARTES", "MART": "MARTES", "MARTE": "MARTES", "MARTES": "MARTES", "MARS": "MARTES", "TUESDAY": "MARTES",
        "TUE": "MARTES", "TUES": "MARTES", "DT": "MARTES", "MÁRTES": "MARTES",
        "X": "MIERCOLES", "MI": "MIERCOLES", "MIE": "MIERCOLES", "MIER": "MIERCOLES", "MIERCOLES": "MIERCOLES", "MIÉRCOLES": "MIERCOLES",
        "J": "JUEVES", "JU": "JUEVES", "JUE": "JUEVES", "JUEVES": "JUEVES",
        "V": "VIERNES", "VI": "VIERNES", "VIE": "VIERNES", "VIERNES": "VIERNES",
        "S": "SABADO", "SA": "SABADO", "SAB": "SABADO", "SABA": "SABADO", "SABADO": "SABADO", "SÁBADO": "SABADO",
        "D": "DOMINGO", "DO": "DOMINGO", "DOM": "DOMINGO", "DOMINGO": "DOMINGO", "DI": "DOMINGO", "DIM": "DOMINGO", "DOMI": "DOMINGO"
    }
    res = mapping.get(base, mapping.get(t))
    return res if res else base

def normalizar_crono_busqueda(val):
    """Normalización profunda para buscar tareas en el cronograma."""
    if not val: return ""
    s = "".join(c for c in unicodedata.normalize('NFD', str(val)) if unicodedata.category(c) != 'Mn')
    return " ".join(s.lower().split()).strip()

def enviar_whatsapp(numero, mensaje):
    """
    Integración real con Whapi.cloud.
    Requiere WHAPI_API_TOKEN en las variables de entorno.
    """
    api_token = os.environ.get("WHAPI_API_TOKEN")
    if not api_token:
        print("ERROR: WHAPI_API_TOKEN no configurado en las variables de entorno.")
        return

    # Limpiamos el número para que solo tenga dígitos (Whapi prefiere formato sin +)
    numero_limpio = "".join(filter(str.isdigit, str(numero)))
    url = "https://gate.whapi.cloud/messages/text"
    payload = {
        "typing_time": 0,
        "to": f"{numero_limpio}@s.whatsapp.net",
        "body": mensaje
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": f"Bearer {api_token}"
    }
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()  # Raise an exception for HTTP errors
        print(f"WHATSAPP ENVIADO a {numero}. Respuesta: {response.json()}")
    except requests.exceptions.RequestException as e:
        print(f"Error al enviar WhatsApp a {numero}: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Whapi API Response: {e.response.text}")

def enviar_push(usuario, mensaje):
    """
    Integración real con OneSignal.
    Requiere ONE_SIGNAL_APP_ID y ONE_SIGNAL_REST_API_KEY en las variables de entorno.
    Asume que el 'usuario' (nombre del coach) se usa como external_user_id en OneSignal.
    """
    app_id = os.environ.get("ONE_SIGNAL_APP_ID")
    rest_api_key = os.environ.get("ONE_SIGNAL_REST_API_KEY")

    if not app_id or not rest_api_key:
        print("ERROR: ONE_SIGNAL_APP_ID o ONE_SIGNAL_REST_API_KEY no configurados en las variables de entorno.")
        return

    url = "https://onesignal.com/api/v1/notifications"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Key {rest_api_key}"
    }

    external_id = normalizar_id(usuario)
    payload = {
        "app_id": app_id,
        "contents": {"es": mensaje, "en": mensaje},
        "headings": {"es": "Intranet Club", "en": "Intranet Club"},
        "include_external_user_ids": [external_id],
        "channel_for_external_user_ids": "push",
        "chrome_web_icon": "https://intranet.clubfuentelarreyna.com/static/escudoapp.png",
        "chrome_web_badge": "https://intranet.clubfuentelarreyna.com/static/badge-escudo.png",
    }

    import sys
    print(f"[PUSH DEBUG] Usuario='{usuario}' ID='{external_id}' AppID='{app_id}' KeyPrefix='{rest_api_key[:20] if rest_api_key else None}'", flush=True)
    try:
        response = requests.post(url, headers=headers, json=payload)
        print(f"[PUSH DEBUG] HTTP {response.status_code}: {response.text[:300]}", flush=True)
        response.raise_for_status()
        res_json = response.json()
        recipients = res_json.get('recipients', 0)
        print(f">>> RESULTADO PUSH ({usuario}): {recipients} dispositivos alcanzados. ID: {res_json.get('id')}", flush=True)
        if recipients == 0:
            print(f"⚠️ AVISO: El entrenador '{usuario}' (ID: {external_id}) aún no ha aceptado notificaciones en su móvil.", flush=True)
    except requests.exceptions.RequestException as e:
        print(f"Error al enviar Push a {usuario}: {e}", flush=True)
        if hasattr(e, 'response') and e.response is not None:
            print(f"OneSignal API Response: {e.response.text}", flush=True)

def parse_dias_entreno(texto):
    """Convierte 'L,X,V' en lista de números [0, 2, 4]"""
    mapping = {
        'L':0, 'LUNES':0, 'M':1, 'MARTES':1, 'X':2, 'MIERCOLES':2, 'MIÉRCOLES':2,
        'J':3, 'JUEVES':3, 'V':4, 'VIERNES':4, 'S':5, 'SABADO':5, 'SÁBADO':5, 'D':6, 'DOMINGO':6
    }
    res = []
    if not texto: return res
    for d in texto.upper().replace(' ', '').split(','):
        if d in mapping: res.append(mapping[d])
    return res

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
DATA_FOLDER   = os.path.join(BASE_DIR, 'static', 'data')
ARCHIVOS_FOLDER = os.path.join(BASE_DIR, 'static', 'archivos')

# --- CONFIGURACIÓN GOOGLE SHEETS ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_service_account_file(os.path.join(BASE_DIR, "secretos.json"), scopes=scope)
from sheets_cache import CachedClient
_raw_client = gspread.authorize(creds)
client = CachedClient(_raw_client, ttl=90)  # caché 90s — lecturas instantáneas en caliente
NOMBRE_EXCEL = "Control Asistencia Club"
app.gs_client = client
app.gs_name = NOMBRE_EXCEL
_asis_cache = {}  # {equipo_lower: {'data': list, 'ts': float}}
_ASIS_CACHE_TTL = 60  # segundos
# ----------------------------------

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['DATA_FOLDER'] = DATA_FOLDER

def normalizar_cabecera_universal(h):
    """Limpia cabeceras de forma agresiva para comparaciones seguras."""
    s = "".join(c for c in unicodedata.normalize('NFD', str(h).upper().strip()) if unicodedata.category(c) != 'Mn')
    return s.replace(' ', '').replace('_', '').replace('.', '').replace('/', '')

for p in [UPLOAD_FOLDER, DATA_FOLDER, ARCHIVOS_FOLDER]: os.makedirs(p, exist_ok=True)

@app.route('/api/guardar_sesion_img', methods=['POST'])
def guardar_sesion_img():
    try:
        data = request.json
        equipo = data.get('equipo', 'GENERAL').strip().replace(' ', '_')
        fecha = data.get('fecha') # DD/MM/YYYY
        usuario = data.get('usuario') or session.get('usuario', 'admin')
        
        if not data.get('image'): return jsonify({"status":"error"}), 400
        img_base64 = data.get('image').split(',')[1]
        
        # 1. Guardar en la carpeta del equipo (DATA_FOLDER/sesiones/EQUIPO)
        folder_equipo = os.path.join(app.config['DATA_FOLDER'], 'sesiones', equipo)
        os.makedirs(folder_equipo, exist_ok=True)
        filename_base = f"Sesion_{fecha.replace('/', '-')}"
        filename_equipo = f"{filename_base}.jpg"
        
        img_data = base64.b64decode(img_base64)

        # Comprobar si ya existe sesión para esa fecha
        json_path = os.path.join(folder_equipo, f"{filename_base}.json")
        if os.path.exists(json_path) and not data.get('overwrite', False):
            return jsonify({"status": "exists", "message": "Ya existe una sesión guardada para esta fecha"}), 409

        with open(os.path.join(folder_equipo, filename_equipo), "wb") as f:
            f.write(img_data)

        # Guardar metadatos para búsqueda (JSON)
        metadata = {
            "fecha": fecha,
            "equipo": equipo,
            "usuario": usuario,
            "notas": data.get('notas', ''),
            "ejercicios": data.get('ejercicios', [])
        }
        with open(os.path.join(folder_equipo, f"{filename_base}.json"), "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=4)
            
        # 2. Guardar como evidencia de entrenamiento para el calendario (botón VER)
        # Formato: entreno_{EQUIPO}_{DD}-{MM}-{YYYY}.jpg  (fecha completa para no confundir meses)
        fp = fecha.split('/')  # ['DD', 'MM', 'YYYY']
        filename_evidencia = f"entreno_{equipo}_{fp[0]}-{fp[1]}-{fp[2]}.jpg"
        with open(os.path.join(app.config['UPLOAD_FOLDER'], filename_evidencia), "wb") as f:
            f.write(img_data)
            
        return jsonify({"status": "success", "url": f"/static/data/sesiones/{equipo}/{filename_equipo}"})
    except Exception as e:
        print(f"Error guardando sesión: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/listar_sesiones')
def listar_sesiones():
    equipo_req = request.args.get('equipo', '').strip().replace(' ', '_')
    usuario = session.get('usuario', 'admin')
    
    sesiones = []
    
    # 1. Escanear Sesiones Online (JSON + JPG)
    path_base = os.path.join(app.config['DATA_FOLDER'], 'sesiones')
    if os.path.exists(path_base):
        for eq_folder in os.listdir(path_base):
            if equipo_req and eq_folder.upper() != equipo_req.upper():
                continue
            full_path = os.path.join(path_base, eq_folder)
            if not os.path.isdir(full_path): continue
            for f in os.listdir(full_path):
                if f.endswith('.json'):
                    try:
                        with open(os.path.join(full_path, f), 'r', encoding='utf-8') as file:
                            meta = json.load(file)
                            # Si hay filtro de equipo, mostrar todas las sesiones del equipo
                            # Si no hay filtro, admin ve todo y el resto solo las suyas
                            if not equipo_req and usuario != 'admin' and meta.get('usuario') != usuario:
                                continue
                            meta['img_url'] = f"/static/data/sesiones/{eq_folder}/{f.replace('.json', '.jpg')}"
                            meta['tipo'] = 'ONLINE'
                            sesiones.append(meta)
                    except: continue

    # 2. Escanear fotos de entrenamiento subidas desde el calendario (por equipo)
    if os.path.exists(UPLOAD_FOLDER):
        clave_eq = sanitizar_equipo_archivo(equipo_req) if equipo_req else ''
        prefijo = f"entreno_{clave_eq}_" if clave_eq else f"entreno_{sanitizar_equipo_archivo(usuario)}_"
        for f in os.listdir(UPLOAD_FOLDER):
            if f.startswith(prefijo) and f.endswith('.jpg'):
                # Extraer fecha del nombre: entreno_EQ_DD-MM-YYYY.jpg
                try:
                    parte_fecha = f[len(prefijo):-4]  # DD-MM-YYYY
                    partes = parte_fecha.split('-')
                    if len(partes) == 3:
                        fecha_str = f"{partes[0]}/{partes[1]}/{partes[2]}"
                    else:
                        fecha_str = datetime.fromtimestamp(os.path.getmtime(os.path.join(UPLOAD_FOLDER, f))).strftime("%d/%m/%Y")
                except:
                    fecha_str = datetime.fromtimestamp(os.path.getmtime(os.path.join(UPLOAD_FOLDER, f))).strftime("%d/%m/%Y")

                sesiones.append({
                    "fecha": fecha_str,
                    "equipo": equipo_req or usuario,
                    "usuario": usuario,
                    "notas": "",
                    "ejercicios": [],
                    "img_url": f"/static/uploads/{f}",
                    "tipo": "SUBIDA"
                })
    
    # Función auxiliar para convertir string de fecha en objeto datetime para ordenar
    def sort_fecha(f_str):
        try:
            # Intentar varios formatos comunes
            for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
                try:
                    return datetime.strptime(f_str, fmt)
                except (ValueError, TypeError):
                    continue
            return datetime.min
        except: return datetime.min

    # DEDUPLICACIÓN: Priorizamos la versión ONLINE sobre la SUBIDA
    vistas = {}
    for s in sesiones:
        clave = f"{s['fecha']}_{s['equipo']}"
        if clave not in vistas:
            vistas[clave] = s
        else:
            if s['tipo'] == 'ONLINE':
                vistas[clave] = s

    return jsonify(sorted(vistas.values(), key=lambda x: sort_fecha(x.get('fecha', '')), reverse=True))

@app.before_request
def _set_session_permanent():
    session.permanent = True

@app.after_request
def _security_headers(response):
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

@app.route('/')
def index():
    return render_template('login.html')

@app.route('/seleccionar_equipo', methods=['GET', 'POST'])
def seleccionar_equipo():
    if not session.get('usuario'): return redirect(url_for('index'))
    
    # Recibimos el equipo seleccionado (Soporta clics en 'cards' vía URL ?equipo=... o formularios)
    equipo_seleccionado = request.args.get('equipo') or request.form.get('equipo')

    if equipo_seleccionado:
        equipo_clean = str(equipo_seleccionado).strip()
        session['equipo_defecto'] = equipo_clean
        perms = session.get('permisos', {})

        # Si versión móvil, redirigir a /movil tras elegir equipo
        if session.get('_version') == 'MOVIL':
            return redirect(url_for('movil'))

        # Redirección inteligente al módulo correspondiente tras elegir equipo
        if session.get('usuario', '').lower() == 'admin': target = url_for('deportivo_bp.direccion_deportiva')
        elif perms.get('D.DEPORTIVA') == 'SI': target = url_for('deportivo_bp.direccion_deportiva')
        elif perms.get('ENTRENAMIENTOS') == 'SI': target = url_for('deportivo_bp.deportivo')
        elif perms.get('ASISTENCIAS') == 'SI': target = url_for('asistencias')
        elif perms.get('FINANCIERO') == 'SI': target = url_for('financiero.financiero')
        else: target = url_for('index')

        return redirect(target)

    try:
        # Obtenemos TODOS los equipos registrados en la pestaña EQUIPO para mostrarlos visualmente
        sheet = client.open(NOMBRE_EXCEL).worksheet("EQUIPO")
        all_v = sheet.get_all_values()
        if not all_v: return render_template('seleccionar_equipo.html', equipos=[], usuario=session.get('usuario'))
        
        headers = [normalizar_cabecera_universal(h) for h in all_v[0]]
        idx_eq = -1
        found_header = False
        # Buscamos la columna que contiene los nombres de los equipos
        for col_name in ["EQUIPO", "NOMBRE", "EQUIPOS", "CATEGORIA", "GRUPO"]:
            if col_name in headers:
                idx_eq = headers.index(col_name)
                found_header = True
                break
        
        # Si no encontramos cabecera clara, usamos la columna 0 y no saltamos la primera fila
        actual_idx = idx_eq if found_header else 0
        start_row = 1 if found_header else 0
        
        todos = sorted(list(set(str(row[actual_idx]).strip() for row in all_v[start_row:] if len(row) > actual_idx and str(row[actual_idx]).strip())))
        # Si el usuario tiene equipos asignados, mostrar solo esos (en el orden del perfil)
        permitidos = session.get('equipos_permitidos', [])
        if permitidos:
            permitidos_upper = [e.upper() for e in permitidos]
            equipos = [e for e in permitidos if e.upper() in [t.upper() for t in todos]]
            # Añadir cualquier equipo de la lista que exista en el sistema aunque el orden difiera
            equipos = sorted(equipos, key=lambda e: permitidos_upper.index(e.upper()) if e.upper() in permitidos_upper else 999)
        else:
            equipos = todos
    except Exception as e:
        print(f"Error recuperando equipos para selección: {e}")
        equipos = []

    # Auto-seleccionar el primer equipo sin mostrar la pantalla de selección
    if equipos:
        session['equipo_defecto'] = equipos[0]
        perms = session.get('permisos', {})
        if session.get('_version') == 'MOVIL':
            return redirect(url_for('movil'))
        if session.get('usuario', '').lower() == 'admin': target = url_for('deportivo_bp.direccion_deportiva')
        elif perms.get('D.DEPORTIVA') == 'SI': target = url_for('deportivo_bp.direccion_deportiva')
        elif perms.get('ENTRENAMIENTOS') == 'SI': target = url_for('deportivo_bp.deportivo')
        elif perms.get('ASISTENCIAS') == 'SI': target = url_for('asistencias')
        elif perms.get('FINANCIERO') == 'SI': target = url_for('financiero.financiero')
        else: target = url_for('index')
        return redirect(target)

    return render_template('seleccionar_equipo.html', equipos=equipos, usuario=session.get('usuario'))

@app.route('/logout')
def logout():
    """Limpia la sesión por completo y redirige al login."""
    session.clear()
    return redirect(url_for('index'))

@app.route('/movil')
def movil():
    usuario = session.get('usuario')
    if not usuario:
        return redirect(url_for('index'))
    perms = session.get('permisos', {})
    if perms.get('ENTRENAMIENTOS') != 'SI' and perms.get('ASISTENCIAS') != 'SI':
        return redirect(url_for('index'))
    equipo = session.get('equipo_defecto', '')

    # Jugadores del equipo
    jugadores = []
    try:
        sheet_jug = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        all_values = sheet_jug.get_all_values()
        if all_values:
            headers = [normalizar_cabecera_universal(h) for h in all_values[0]]
            idx_eq = next((headers.index(c) for c in ["EQUIPO","CATEGORIA","GRUPO","EQUIPOS","EQUIPOACTIVO"] if c in headers), -1)
            idx_nom = headers.index('NOMBRE') if 'NOMBRE' in headers else 0
            for row in all_values[1:]:
                if not any(row): continue
                eq = row[idx_eq].strip() if idx_eq >= 0 and idx_eq < len(row) else ''
                nom = row[idx_nom].strip() if idx_nom < len(row) else ''
                if eq.upper() == equipo.upper() and nom:
                    jugadores.append(nom)
    except Exception as e:
        print(f"Error cargando jugadores en movil: {e}")

    # Objetivos del mes (TÁCTICO / TÉCNICO)
    hoy = datetime.now()
    mes_actual = f"{hoy.year}-{hoy.month:02d}"
    objetivos = {"tactico": "", "tecnico": ""}
    try:
        from deportivo import obtener_tactico_tecnico_metodologia
        sheet_obj = client.open(NOMBRE_EXCEL).worksheet("OBJ TACTEC")
        all_objs = sheet_obj.get_all_values()
        target_y, target_m = mes_actual.split('-')
        for row in all_objs[1:]:
            if len(row) < 2: continue
            fecha_row = row[0].strip()
            if '/' not in fecha_row: continue
            parts = fecha_row.split('/')
            if len(parts) != 3: continue
            d, m, y = [p.zfill(2) if p.strip().isdigit() else p.strip() for p in parts]
            if m == target_m and y == target_y and row[1].strip().upper() == equipo.upper():
                if d == "01" or not objetivos["tactico"]:
                    objetivos["tactico"] = row[3].strip() if len(row) > 3 else objetivos["tactico"]
                    objetivos["tecnico"] = row[4].strip() if len(row) > 4 else objetivos["tecnico"]
        tac_m, tec_m = obtener_tactico_tecnico_metodologia(client, NOMBRE_EXCEL, equipo, mes_actual)
        if tac_m: objetivos["tactico"] = tac_m
        if tec_m: objetivos["tecnico"] = tec_m
    except Exception as e:
        print(f"Error cargando objetivos en movil: {e}")

    # Ejercicios semanales
    ejercicios_semanales = {}
    try:
        from deportivo import limpiar_texto_robusto, normalizar_fecha_sheet
        sheet_eje = client.open(NOMBRE_EXCEL).worksheet("EJERCICIOS")
        all_eje = sheet_eje.get_all_values()
        if all_eje:
            for row in all_eje[1:]:
                if len(row) >= 4 and row[0] and row[1]:
                    equipos_eje = [limpiar_texto_robusto(e) for e in str(row[1]).split(',') if e.strip()]
                    if limpiar_texto_robusto(equipo) in equipos_eje:
                        sem_key = normalizar_fecha_sheet(row[0])
                        if sem_key not in ejercicios_semanales:
                            ejercicios_semanales[sem_key] = []
                        ejercicios_semanales[sem_key].append({
                            "categoria": str(row[2]) if len(row) > 2 else "0",
                            "titulo":    row[3] if len(row) > 3 else "",
                            "descripcion": row[4] if len(row) > 4 else "",
                            "url":       row[5] if len(row) > 5 else "",
                        })
    except Exception as e:
        print(f"Error cargando ejercicios en movil: {e}")

    # Fotos ya subidas este mes (para saber qué días muestran VER vs SUBIR)
    fotos_subidas = []
    try:
        clave_eq = sanitizar_equipo_archivo(equipo)
        mes_str = f"{hoy.month:02d}"
        anio_str = str(hoy.year)
        prefijo = f"entreno_{clave_eq}_"
        if os.path.exists(UPLOAD_FOLDER):
            for f in os.listdir(UPLOAD_FOLDER):
                if not f.startswith(prefijo): continue
                rest = f[len(prefijo):].rsplit('.', 1)[0]
                pf = rest.split('-')
                if len(pf) == 3 and pf[1] == mes_str and pf[2] == anio_str:
                    fotos_subidas.append(str(int(pf[0])))
    except Exception as e:
        print(f"Error cargando fotos en movil: {e}")

    from deportivo import _get_dias_entreno
    dias_entreno_nums = _get_dias_entreno(equipo)

    return render_template('movil.html',
                           usuario=usuario, equipo=equipo,
                           jugadores=jugadores, perms=perms,
                           objetivos=objetivos,
                           ejercicios_semanales=ejercicios_semanales,
                           fotos_subidas=fotos_subidas,
                           equipos_usuario=session.get('equipos_permitidos', []),
                           dias_entreno_nums=dias_entreno_nums)

@app.route('/api/fotos_mes')
def api_fotos_mes():
    if not session.get('usuario'):
        return jsonify([])
    equipo = session.get('equipo_defecto', '')
    try:
        mes  = int(request.args.get('mes',  datetime.now().month))
        anio = int(request.args.get('anio', datetime.now().year))
    except Exception:
        mes, anio = datetime.now().month, datetime.now().year
    fotos = []
    try:
        clave_eq = sanitizar_equipo_archivo(equipo)
        mes_str  = f"{mes:02d}"
        anio_str = str(anio)
        prefijo  = f"entreno_{clave_eq}_"
        if os.path.exists(UPLOAD_FOLDER):
            for f in os.listdir(UPLOAD_FOLDER):
                if not f.startswith(prefijo): continue
                rest = f[len(prefijo):].rsplit('.', 1)[0]
                pf = rest.split('-')
                if len(pf) == 3 and pf[1] == mes_str and pf[2] == anio_str:
                    fotos.append(str(int(pf[0])))
    except Exception as e:
        print(f"Error en api_fotos_mes: {e}")
    return jsonify(fotos)

@app.route('/api/partidos_mes')
def api_partidos_mes():
    if not session.get('usuario'):
        return jsonify([])
    equipo = session.get('equipo_defecto', '')
    try:
        mes  = int(request.args.get('mes',  datetime.now().month))
        anio = int(request.args.get('anio', datetime.now().year))
    except Exception:
        mes, anio = datetime.now().month, datetime.now().year
    dias = []
    try:
        sh = client.open(NOMBRE_EXCEL)
        try:
            ws = sh.worksheet('FORMULARIO_PARTIDOS')
            all_v = ws.get_all_values()
        except Exception:
            return jsonify([])
        if not all_v:
            return jsonify([])
        headers = [str(h).strip() for h in all_v[0]]
        idx_fecha  = headers.index('MARCA TEMPORAL') if 'MARCA TEMPORAL' in headers else -1
        idx_equipo = headers.index('EQUIPO') if 'EQUIPO' in headers else -1
        if idx_fecha < 0 or idx_equipo < 0:
            return jsonify([])
        mes_str  = f"{mes:02d}"
        anio_str = str(anio)
        for row in all_v[1:]:
            eq = row[idx_equipo].strip() if idx_equipo < len(row) else ''
            if eq != equipo:
                continue
            fecha_str = row[idx_fecha].strip() if idx_fecha < len(row) else ''
            try:
                parts = fecha_str.split(' ')[0].split('/')
                if len(parts) == 3 and parts[1].zfill(2) == mes_str and parts[2] == anio_str:
                    dias.append(str(int(parts[0])))
            except Exception:
                pass
    except Exception as e:
        print(f"Error en api_partidos_mes: {e}")
    return jsonify(dias)

@app.route('/OneSignalSDKWorker.js')
def onesignal_worker():
    # Necesario para que OneSignal funcione. El archivo debe estar en la carpeta /static/
    return app.send_static_file('OneSignalSDKWorker.js')

@app.route('/save_all', methods=['POST'])
def save_all():
    # Solo el administrador tiene permiso para marcar objetivos realizados
    if session.get('usuario') != 'admin':
        return jsonify({"status": "error", "message": "Acceso restringido: Solo el administrador puede marcar objetivos realizados"}), 403

    data = request.json
    equipo = data.get('equipo') or session.get('equipo_defecto')
    mes_base = data.get('mes') # Formato "2026-05"
    new_completados = data.get('objetivos', {}).get('completados', []) # Lista tipo ["1-Presion", "2-Tiro"]

    client = app.gs_client
    NOMBRE_EXCEL = app.gs_name
    SHEET_NAME = "OBJ TACTEC"

    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet(SHEET_NAME)
        agrupados = {}
        for item in new_completados:
            if '-' in str(item):
                dia, obj = str(item).split('-', 1)
                if dia not in agrupados: agrupados[dia] = []
                agrupados[dia].append(obj.strip())
            else:
                # Fallback por si llega sin prefijo (no debería ocurrir en el calendario)
                if "sin_dia" not in agrupados: agrupados["sin_dia"] = []
                agrupados["sin_dia"].append(str(item).strip())

        year, month = mes_base.split('-')
        existing_data = sheet.get_all_values()

        for dia, objetivos in agrupados.items():
            if dia == "sin_dia":
                fecha_full = f"01/{month}/{year}" # Por defecto al día 1 si no hay info
            else:
                fecha_full = f"{dia.zfill(2)}/{month}/{year}"
            
            obj_str = ", ".join(objetivos) # Sin el prefijo 1-
            
            fila_idx = -1
            for i, row in enumerate(existing_data):
                if i == 0: continue # Saltar cabecera
                if len(row) >= 2 and str(row[0]).strip() == fecha_full and str(row[1]).strip().upper() == equipo.strip().upper():
                    fila_idx = i + 1
                    break
            
            if fila_idx != -1:
                # Actualizamos la Columna C (índice 3)
                sheet.update_cell(fila_idx, 3, obj_str)
            else:
                # Nueva fila: Fecha, Equipo, Objetivos Completados, Táctico (vacío), Técnico (vacío)
                nueva_fila = [fecha_full, equipo, obj_str, "", ""]
                sheet.append_row(nueva_fila)

    except Exception as e:
        print(f"Error sincronizando completados (save_all): {e}")
        return jsonify({"status": "error"}), 500
    return jsonify({"status": "success"})

def sanitizar_equipo_archivo(equipo):
    """Normaliza el nombre de un equipo para usarlo de forma segura en un nombre de archivo."""
    s = re.sub(r'[^A-Za-z0-9]+', '_', (equipo or '').strip().upper())
    return s.strip('_') or 'SINEQUIPO'

@app.route('/upload_foto', methods=['POST'])
def upload_foto():
    data = request.json
    equipo = data.get('equipo') or data.get('usuario')
    fecha  = data.get('fecha')   # DD/MM/YYYY  (nuevo)
    dia    = data.get('dia')     # solo número (fallback antiguo)
    clave  = sanitizar_equipo_archivo(equipo)
    try:
        img_data = base64.b64decode(data.get('image').split(",")[1])
        if fecha:
            fp = fecha.split('/')  # ['DD', 'MM', 'YYYY']
            filename = f"entreno_{clave}_{fp[0]}-{fp[1]}-{fp[2]}.jpg"
        else:
            filename = f"entreno_{clave}_{dia}.jpg"
        with open(os.path.join(UPLOAD_FOLDER, filename), "wb") as f:
            f.write(img_data)
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error upload_foto: {e}")
        return jsonify({"status": "error"}), 500
@app.route('/login', methods=['POST'])
def login():
    usuario_ingresado = (request.form.get('usuario') or "").strip()
    password_ingresado = (request.form.get('password') or "").strip()

    version_pref = request.form.get('version', 'WEB').strip().upper()
    if version_pref not in ('WEB', 'MOVIL'):
        version_pref = 'WEB'
    session['_version'] = version_pref

    # 1. Caso especial para admin (Master)
    if usuario_ingresado.lower() == 'admin':
        admin_pwd = os.environ.get('ADMIN_PASSWORD', '')
        if not admin_pwd or password_ingresado != admin_pwd:
            return render_template('login.html', error="Credenciales incorrectas.")
        session['usuario'] = 'admin'
        session['permisos'] = {
            'ENTRENAMIENTOS': 'SI', 'ASISTENCIAS': 'SI', 'FINANCIERO': 'SI', 'D.DEPORTIVA': 'SI', 'USUARIOS': 'SI',
            'CRONOGRAMA': 'SI', 'RRSS': 'SI'
        }
        return redirect(url_for('deportivo_bp.direccion_deportiva'))

    # 2. Otros usuarios - Consultar la pestaña PERFILES (caché 5 min)
    try:
        records = _get_perfiles_records()
        
        # Búsqueda ultra-precisa: normalizamos el target y los registros
        from werkzeug.security import check_password_hash as _chk, generate_password_hash as _gen
        user_data = None
        row_idx_sheet = None
        target_upper = usuario_ingresado.upper()

        for idx, r in enumerate(records):
            # Normalización de llaves del diccionario de la fila
            r_norm = {normalizar_cabecera_universal(k): v for k, v in r.items()}
            if str(r_norm.get('USUARIO', '')).strip().upper() == target_upper:
                user_data = r_norm
                row_idx_sheet = idx + 2  # fila 1 = cabecera
                break

        if user_data:
            stored = str(user_data.get('CONTRASENA', '')).strip()
            _is_hash = stored.startswith(('pbkdf2:', '$2b$', 'scrypt:'))
            if _is_hash:
                _password_ok = _chk(stored, password_ingresado)
            else:
                _password_ok = (stored == password_ingresado)
            if _password_ok:
                session['usuario'] = usuario_ingresado
                
                def check_p(key):
                    k_clean = normalizar_cabecera_universal(key)
                    val = str(user_data.get(k_clean, 'NO')).strip().upper()
                    return 'SI' if val == 'SI' else 'NO'

                perms = {
                    'ENTRENAMIENTOS': check_p('ENTRENAMIENTOS'),
                    'ASISTENCIAS': check_p('ASISTENCIAS'),
                    'FINANCIERO': check_p('FINANCIERO'),
                    'D.DEPORTIVA': check_p('D.DEPORTIVA'),
                    'D.DEP': check_p('D.DEP'),
                    'CRONOGRAMA': check_p('CRONOGRAMA'),
                    'RRSS': check_p('RRSS'),
                    'SEL. EQ.': 'SI' if str(user_data.get('SELEQ', user_data.get('SELEQ.', 'NO'))).strip().upper() == 'SI' else 'NO'
                }
                session['permisos'] = perms
                
                # Guardar equipos asignados al usuario (para filtrar la pantalla de selección)
                equipo_raw = str(user_data.get('EQUIPO', '')).strip()
                session['equipos_permitidos'] = [e.strip() for e in equipo_raw.split(',') if e.strip()] if equipo_raw else []

                # 3. ¿Debe elegir equipo? Buscamos SELEQ (normalizado de SEL. EQ.)
                debe_elegir = user_data.get('SELEQ') or user_data.get('ELEGIRIQUIPO') or user_data.get('SELEQ.') or 'NO'
                if str(debe_elegir).strip().upper() == 'SI':
                    session['equipo_defecto'] = '' # Limpiamos selección previa para forzar la nueva
                    return redirect(url_for('seleccionar_equipo'))

                # Si versión móvil, redirigir directamente a /movil
                if version_pref == 'MOVIL':
                    return redirect(url_for('movil'))

                # Redirección inteligente al primer acceso permitido
                if perms.get('D.DEPORTIVA') == 'SI': return redirect(url_for('deportivo_bp.direccion_deportiva'))
                if perms.get('ENTRENAMIENTOS') == 'SI': return redirect(url_for('deportivo_bp.deportivo'))
                if perms.get('ASISTENCIAS') == 'SI': return redirect(url_for('asistencias'))
                if perms.get('FINANCIERO') == 'SI': return redirect(url_for('financiero.financiero'))
                
                return render_template('login.html', error="Usuario sin secciones asignadas. Contacta al admin.")
            
        return render_template('login.html', error="Usuario o contraseña incorrectos")

    except Exception as e:
        print(f"ERROR CRÍTICO EN LOGIN: {e}")
        import traceback
        traceback.print_exc() # Esto imprimirá el rastro completo del error en la terminal
        return render_template('login.html', error="Error interno del servidor al verificar credenciales. Contacta al administrador.")

@app.route('/delete_foto', methods=['POST']) # Esta ruta estaba duplicada, la he dejado solo una vez
def delete_foto():
    data = request.json
    usuario = data.get('usuario')
    dia = data.get('dia')
    
    filename = f"entreno_{usuario}_{dia}.jpg"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            return jsonify({"status": "success"}), 200
        else:
            return jsonify({"status": "error", "message": "Archivo no encontrado"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/asistencias')
def asistencias():
    usuario = session.get('usuario')
    if not usuario:
        return redirect(url_for('index'))
    
    if session.get('permisos', {}).get('ASISTENCIAS') != 'SI':
        return "No tienes permiso para ver esta sección", 403
    
    # 1. Cargar Jugadores y mapear cualquier columna de equipo a la clave 'EQUIPO'
    datos = []
    try:
        sheet_jug = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        all_values = sheet_jug.get_all_values()
        if all_values:
            headers = [normalizar_cabecera_universal(h) for h in all_values[0]]
            idx_eq_jug = -1
            for col_name in ["EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS", "EQUIPOACTIVO"]:
                if col_name in headers:
                    idx_eq_jug = headers.index(col_name)
                    break

            for row in all_values[1:]:
                if any(row):
                    registro = {}
                    for i, h in enumerate(headers):
                        val = row[i] if i < len(row) else ""
                        key = 'EQUIPO' if (i == idx_eq_jug and idx_eq_jug != -1) else h
                        registro[key] = str(val).strip()
                    if 'EQUIPO' not in registro: registro['EQUIPO'] = ""
                    datos.append(registro)
    except Exception as e:
        print(f"Error al cargar jugadores en asistencias: {e}")

    # 2. Cargar lista de equipos (Fuente: Pestaña EQUIPO o Fallback: JUGADORES)
    equipos = []
    try:
        sheet_eq = client.open(NOMBRE_EXCEL).worksheet("EQUIPO")
        rows_eq = sheet_eq.get_all_values()
        if rows_eq:
            h_eq = [normalizar_cabecera_universal(h) for h in rows_eq[0]]
            i_eq = -1
            for kw in ["EQUIPO", "NOMBRE", "EQUIPOS", "CATEGORIA", "GRUPO", "EQUIPOACTIVO"]:
                if kw in h_eq:
                    i_eq = h_eq.index(kw)
                    break
            s_row = 1 if i_eq != -1 else 0
            if i_eq == -1: i_eq = 0
            equipos = sorted(list(set(str(r[i_eq]).strip() for r in rows_eq[s_row:] if len(r) > i_eq and str(r[i_eq]).strip())))
        else: equipos = []
    except:
        equipos = sorted(list(set(row['EQUIPO'] for row in datos if row.get('EQUIPO'))))
    
    # 3. Determinar equipo activo
    equipo_param = request.args.get('equipo')
    equipo_activo = equipo_param.strip() if equipo_param else session.get('equipo_defecto', '')
    if not equipo_activo and equipos:
        equipo_activo = equipos[0]
    session['equipo_defecto'] = equipo_activo 

    meses = ["Enero 2026", "Febrero 2026", "Marzo 2026", "Abril 2026", "Mayo 2026", "Junio 2026"]
    
    # También cargamos el Staff para saber quiénes son los entrenadores
    try:
        staff_sheet = client.open(NOMBRE_EXCEL).worksheet("STAFF")
        staff_all = staff_sheet.get_all_records()
        # Normalizamos las llaves por si acaso
        staff_datos = []
        for s in staff_all:
            s_norm = {normalizar_cabecera_universal(k): v for k, v in s.items()}
            staff_datos.append({
                "NOMBRE": str(s_norm.get("NOMBRE", "")),
                "EQUIPO": str(s_norm.get("EQUIPO", "")).strip().upper(),
                "TELEFONO": str(s_norm.get("TELEFONO", "")),
                "DIAS ENTRENAMIENTO": str(s_norm.get("DIASENTRENAMIENTO", ""))
            })
    except:
        staff_datos = []

    return render_template('asistencias/lista.html', 
                           usuario=usuario, 
                           equipos=equipos,
                           equipo_defecto=equipo_activo, # Pasar el equipo activo a la plantilla
                           meses=meses, 
                           jugadores_raw=datos,
                           staff_raw=staff_datos)

@app.route('/api/control_balones', methods=['GET', 'POST'])
def api_control_balones():
    if not session.get('usuario'):
        return jsonify({"status": "error", "message": "No autenticado"}), 401
    equipo = request.args.get('equipo', '').strip()
    mes    = request.args.get('mes', '').strip()
    if not equipo or not mes:
        return jsonify({"status": "error", "message": "Faltan parámetros"}), 400
    path = os.path.join(DATA_FOLDER, f'balones_{equipo}_{mes}.json')

    if request.method == 'GET':
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return jsonify(json.load(f))
            return jsonify({})
        except Exception as e:
            print(f"Error GET control_balones: {e}")
            return jsonify({}), 200

    try:
        data = request.get_json(force=True, silent=True) or {}
        dia      = str(data.get('dia', ''))
        tipo     = data.get('tipo', '')
        cantidad = data.get('cantidad', '')
        foto     = data.get('foto')

        if not dia or not tipo or not cantidad:
            return jsonify({"status": "error", "message": "Datos incompletos"}), 400

        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M")

        current_data = {}
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                current_data = json.load(f)

        if dia not in current_data:
            current_data[dia] = {}

        if tipo in current_data[dia] and not isinstance(current_data[dia][tipo], list):
            current_data[dia][tipo] = [current_data[dia][tipo]]

        if tipo not in current_data[dia]:
            current_data[dia][tipo] = []

        nuevo_registro = {"cantidad": cantidad, "timestamp": timestamp}

        if foto and "," in foto:
            eq_safe = re.sub(r'[^A-Za-z0-9]+', '_', equipo)
            foto_filename = f"balones_{eq_safe}_{mes}_{dia}_{tipo}_{len(current_data[dia][tipo])}.jpg"
            foto_path = os.path.join(UPLOAD_FOLDER, foto_filename)
            img_data = base64.b64decode(foto.split(",")[1])
            with open(foto_path, "wb") as f:
                f.write(img_data)
            nuevo_registro["foto_url"] = f"/static/uploads/{foto_filename}"

        current_data[dia][tipo].append(nuevo_registro)

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(current_data, f, ensure_ascii=False)

        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error POST control_balones: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/recuento_balones_analisis')
def api_recuento_balones_analisis():
    from datetime import datetime as dt2, timedelta
    fecha1_str = request.args.get('fecha1', '')  # YYYY-MM-DD
    fecha2_str = request.args.get('fecha2', '')  # YYYY-MM-DD
    equipos_str = request.args.get('equipos', '')
    if not fecha1_str or not fecha2_str:
        return jsonify([])
    equipos = [e.strip() for e in equipos_str.split(',') if e.strip()]
    try:
        f1 = dt2.strptime(fecha1_str, '%Y-%m-%d')
        f2 = dt2.strptime(fecha2_str, '%Y-%m-%d')
    except Exception:
        return jsonify([])
    # Recopilar meses como número simple (igual que el nombre de archivo: balones_EQUIPO_5.json)
    meses = set()
    cur = f1.replace(day=1)
    while cur <= f2:
        meses.add(str(cur.month))
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    meses.add(str(f2.month))
    registros = []
    for equipo in equipos:
        for mes in sorted(meses, key=int):
            path = os.path.join(DATA_FOLDER, f'balones_{equipo}_{mes}.json')
            if not os.path.exists(path):
                continue
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for dia_str, dia_data in data.items():
                for tipo in ['inicio', 'final']:
                    lista = dia_data.get(tipo, [])
                    if not isinstance(lista, list):
                        lista = [lista]
                    for reg in lista:
                        ts = reg.get('timestamp', '')
                        try:
                            try:
                                ts_dt = dt2.strptime(ts, '%d/%m/%Y %H:%M')
                            except ValueError:
                                ts_dt = dt2.strptime(ts, '%d/%m/%Y %H:%M:%S')
                        except Exception:
                            continue
                        fecha_dia = ts_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                        if not (f1 < fecha_dia < f2):
                            continue
                        registros.append({
                            'equipo': equipo,
                            'fecha': ts_dt.strftime('%d/%m/%Y'),
                            'tipo': tipo,
                            'cantidad': reg.get('cantidad'),
                            'timestamp': ts,
                            '_ts_sort': ts_dt.isoformat()
                        })
    registros.sort(key=lambda r: r['_ts_sort'])
    for r in registros:
        del r['_ts_sort']
    return jsonify(registros)

from flask import request, jsonify

# ... (tus otras rutas)

@app.route('/guardar_asistencia_individual', methods=['POST'])
def guardar_asistencia_individual():
    datos = request.json
    nombre = datos.get('nombre')
    dia = datos.get('dia')
    equipo = datos.get('equipo')
    estado = datos.get('estado')

    try:
        # Abrimos la pestaña ASISTENCIAS (como en tu imagen image_9650d5.png)
        sheet = client.open(NOMBRE_EXCEL).worksheet("ASISTENCIAS")
        
        # Creamos la fecha para la columna A
        fecha_asistencia = f"{int(dia):02d}/05/2026" 

        # Añadimos la fila con el orden de tus columnas: 
        # FECHA, EQUIPO, NOMBRE, APELLIDO, ASISTENCIA, OBSERVACIONES
        nueva_fila = [fecha_asistencia, equipo, nombre, "", estado, ""]
        sheet.append_row(nueva_fila)

        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/obtener_asistencias')
def obtener_asistencias():
    equipo_filtro = request.args.get('equipo')
    equipo_filtro_clean = equipo_filtro.strip().lower() if equipo_filtro else ""
    mes_filtro = request.args.get('mes')
    if not mes_filtro:
        cached = _asis_cache.get(equipo_filtro_clean)
        if cached and (time.time() - cached['ts']) < _ASIS_CACHE_TTL:
            return jsonify(cached['data'])
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("ASISTENCIAS")
        # Usamos get_all_values para evitar errores si hay celdas vacías o duplicadas en la cabecera
        todo = sheet.get_all_values()
        
        if not todo:
            return jsonify([])
            
        registros = []
        # Empezamos desde la segunda fila (todo[1:]) para saltar los títulos
        for fila in todo[1:]:
            # Verificamos que la fila tenga datos y coincida con el equipo
            if len(fila) >= 3 and fila[1].strip().lower() == equipo_filtro_clean:
                partes_fecha = fila[0].split('/')
                if len(partes_fecha) < 2: continue
                
                # Normalizamos a string sin ceros a la izquierda para comparar con el frontend
                try:
                    dia_extraido = str(int(partes_fecha[0])) 
                    mes_extraido = str(int(partes_fecha[1]))
                except (ValueError, IndexError):
                    continue
                
                # Si se solicita un mes específico, filtramos
                if mes_filtro and mes_extraido != str(int(mes_filtro)):
                    continue

                registros.append({
                    "nombre": fila[2].strip() if len(fila) > 2 else "",
                    "dia": dia_extraido,
                    "mes": mes_extraido,
                    "estado": fila[4] if len(fila) > 4 else "",
                    "valoracion": fila[5] if len(fila) > 5 else "-",
                    "motivo": fila[6] if len(fila) > 6 else "",
                    "charla": fila[7] if len(fila) > 7 else "NO"
                })
        if not mes_filtro:
            _asis_cache[equipo_filtro_clean] = {'data': registros, 'ts': time.time()}
        return jsonify(registros)
    except Exception as e:
        print(f"Error al leer asistencias: {e}")
        return jsonify([])


@app.route('/api/asistencias_matrix')
def api_asistencias_matrix():
    """Devuelve todas las asistencias de un equipo con fecha completa para la matriz."""
    equipo = (request.args.get('equipo') or '').strip().lower()
    if not equipo:
        return jsonify({'fechas': [], 'jugadores': [], 'datos': {}})
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("ASISTENCIAS")
        todo = sheet.get_all_values()
        if not todo:
            return jsonify({'fechas': [], 'jugadores': [], 'datos': {}})

        fechas_set = set()
        datos = {}  # {jugador: {fecha: estado}}

        for fila in todo[1:]:
            if len(fila) < 3:
                continue
            if fila[1].strip().lower() != equipo:
                continue
            fecha_raw = fila[0].strip()   # "dd/mm/yyyy" o "dd/mm/yy"
            nombre    = fila[2].strip()
            estado     = fila[4].strip() if len(fila) > 4 else ''
            valoracion = fila[5].strip() if len(fila) > 5 else ''
            if not fecha_raw or not nombre:
                continue
            # Normalizar fecha a "dd/mm/yyyy"
            partes = fecha_raw.split('/')
            if len(partes) < 2:
                continue
            try:
                dia = int(partes[0])
                mes = int(partes[1])
                anio = int(partes[2]) if len(partes) > 2 else 2026
                if anio < 100:
                    anio += 2000
                fecha_norm = f"{dia:02d}/{mes:02d}/{anio}"
            except (ValueError, IndexError):
                continue

            fechas_set.add(fecha_norm)
            if nombre not in datos:
                datos[nombre] = {}
            datos[nombre][fecha_norm] = {'estado': estado, 'val': valoracion}

        # Ordenar fechas cronológicamente
        def sort_fecha(f):
            d, m, y = f.split('/')
            return (int(y), int(m), int(d))

        fechas_sorted = sorted(fechas_set, key=sort_fecha)
        jugadores_sorted = sorted(datos.keys())

        return jsonify({
            'fechas': fechas_sorted,
            'jugadores': jugadores_sorted,
            'datos': datos
        })
    except Exception as e:
        print(f"Error asistencias_matrix: {e}")
        return jsonify({'fechas': [], 'jugadores': [], 'datos': {}})


@app.route('/obtener_observaciones_jugadores')
def obtener_observaciones_jugadores():
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        todo = sheet.get_all_values()
        if not todo: return jsonify({})
        headers = [h.strip().upper() for h in todo[0]]
        idx_nom = headers.index("NOMBRE") if "NOMBRE" in headers else -1
        idx_obs = headers.index("OBSERVACIONES") if "OBSERVACIONES" in headers else -1
        idx_baja = headers.index("BAJA_DESDE") if "BAJA_DESDE" in headers else -1
        idx_alta = headers.index("ALTA_DESDE") if "ALTA_DESDE" in headers else -1
        
        resultado = {}
        for fila in todo[1:]:
            nombre = fila[idx_nom].strip() if idx_nom != -1 and len(fila) > idx_nom else ""
            if nombre: # Solo procesamos si hay nombre
                resultado[nombre] = {
                    "obs": fila[idx_obs] if idx_obs != -1 and len(fila) > idx_obs else "",
                    "baja": fila[idx_baja] if idx_baja != -1 and len(fila) > idx_baja else "",
                    "alta": fila[idx_alta] if idx_alta != -1 and len(fila) > idx_alta else ""
                }
        return jsonify(resultado)
    except Exception as e:
        print(f"Error al obtener observaciones: {e}")
        return jsonify({})

@app.route('/guardar_observacion_jugador', methods=['POST'])
def guardar_observacion_jugador():
    datos = request.json
    nombre = datos.get('nombre')
    texto = datos.get('observacion')
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        all_data = sheet.get_all_values()
        if not all_data: return jsonify({"status": "error"}), 404
        headers = [h.strip().upper() for h in all_data[0]]
        
        idx_nom = headers.index("NOMBRE") if "NOMBRE" in headers else 0

        if "OBSERVACIONES" not in headers:
            sheet.update_cell(1, len(headers)+1, "OBSERVACIONES")
            idx_obs = len(headers) + 1
        else:
            idx_obs = headers.index("OBSERVACIONES") + 1
        
        fila_idx = -1
        nombre_clean = nombre.strip().lower()
        for i, fila in enumerate(all_data):
            if len(fila) > idx_nom and fila[idx_nom].strip().lower() == nombre_clean:
                fila_idx = i + 1
                break
        
        if fila_idx != -1:
            sheet.update_cell(fila_idx, idx_obs, texto)
            return jsonify({"status": "success"})
        else:
            return jsonify({"status": "error", "message": "Jugador no encontrado"}), 404
    except Exception as e:
        print(f"Error crítico en guardar_observacion_jugador: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/guardar_asistencia_masiva', methods=['POST'])
def guardar_asistencia_masiva():
    try:
        data = request.get_json()
        cambios = data.get('cambios', [])
        # El mes llega como número de mes desde el selector del frontend
        mes_val = int(data.get('mes', 5))
        
        # IMPORTANTE: Usamos 'client' directamente, que ya lo tienes definido arriba
        sheet = client.open(NOMBRE_EXCEL).worksheet("ASISTENCIAS")
        
        for c in cambios:
            # Refrescamos los valores en cada iteración para evitar duplicados si se pulsa muy rápido
            actuales = sheet.get_all_values()
            
            dia_val = int(c['dia'])
            fecha_full = f"{dia_val:02d}/{mes_val:02d}/2026"
            nombre = c['nombre']
            nombre_lower = nombre.strip().lower()
            equipo_lower = c['equipo'].strip().lower()
            estado = c['estado']
            valoracion = c.get('valoracion', '-') # Captura la flecha (NORMAL, MAL, etc.)
            if not valoracion: valoracion = "-"
            observacion = c.get('motivo', '') 
            charla = c.get('charla', 'NO')

            # Búsqueda robusta: comparamos números de día y mes, no strings exactos
            fila_idx = -1
            for i, fila in enumerate(actuales):
                if i == 0: continue
                if len(fila) >= 3:
                    partes = fila[0].split('/')
                    if len(partes) >= 2:
                        try:
                            f_dia = int(partes[0])
                            f_mes = int(partes[1])
                            if f_dia == dia_val and f_mes == mes_val and fila[1].strip().lower() == equipo_lower and fila[2].strip().lower() == nombre_lower:
                                fila_idx = i + 1
                                break
                        except: continue
            
            if fila_idx != -1:
                # Actualización masiva de la fila (Columnas E, F, G) para mayor velocidad y fiabilidad
                sheet.update(f'E{fila_idx}:H{fila_idx}', [[estado, valoracion, observacion, charla]], value_input_option='USER_ENTERED')
            else:
                # Nueva fila: FECHA, EQUIPO, NOMBRE, APELLIDO, ASISTENCIA, VALORACIÓN, OBSERVACIONES, CHARLA
                sheet.append_row([fecha_full, c['equipo'], nombre, "", estado, valoracion, observacion, charla])

        equipos_mod = {c['equipo'].strip().lower() for c in cambios}
        for eq in equipos_mod:
            _asis_cache.pop(eq, None)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        print(f"Error crítico al guardar: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/anadir_jugador', methods=['POST'])
def anadir_jugador():
    datos = request.json
    nombre = datos.get('nombre')
    equipo = datos.get('equipo')
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        headers = sheet.row_values(1)
        row_to_add = [""] * len(headers)
        for i, h in enumerate(headers):
            h_clean = h.strip().upper()
            if h_clean == "NOMBRE": row_to_add[i] = nombre
            if h_clean == "EQUIPO": row_to_add[i] = equipo
        sheet.append_row(row_to_add)
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error al añadir jugador: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/actualizar_jugador', methods=['POST'])
def actualizar_jugador():
    datos = request.json
    nombre_antiguo = datos.get('nombre_antiguo')
    nombre_nuevo = datos.get('nombre_nuevo')
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        celda = sheet.find(nombre_antiguo.strip(), in_column=1)
        sheet.update_cell(celda.row, 1, nombre_nuevo)
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error en actualizar_jugador: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/eliminar_jugador', methods=['POST'])
def eliminar_jugador():
    nombre = request.json.get('nombre')
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        celda = sheet.find(nombre.strip(), in_column=1)
        sheet.delete_rows(celda.row)
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error en eliminar_jugador: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/marcar_baja', methods=['POST'])
def marcar_baja():
    nombre = request.json.get('nombre')
    fecha_baja = request.json.get('fecha')
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        all_rows = sheet.get_all_values()
        if not all_rows: return jsonify({"status": "error"}), 404
        headers = [h.strip().upper() for h in all_rows[0]]
        
        idx_nom = headers.index("NOMBRE") if "NOMBRE" in headers else 0

        if "BAJA_DESDE" not in headers:
            idx_baja = len(headers) + 1
            sheet.update_cell(1, idx_baja, "BAJA_DESDE")
        else:
            idx_baja = headers.index("BAJA_DESDE") + 1
            
        # Al dar de BAJA, limpiamos la fecha de ALTA si existiera
        if "ALTA_DESDE" in headers:
            idx_alta = headers.index("ALTA_DESDE") + 1
        else:
            idx_alta = -1

        fila_idx = -1
        nombre_clean = nombre.strip().lower()
        for i, fila in enumerate(all_rows):
            if len(fila) > idx_nom and fila[idx_nom].strip().lower() == nombre_clean:
                fila_idx = i + 1
                break
        
        if fila_idx == -1: return jsonify({"status": "error", "message": "No se encontró al jugador"}), 404
        
        sheet.update_cell(fila_idx, idx_baja, fecha_baja)
        if idx_alta != -1:
            sheet.update_cell(fila_idx, idx_alta, "") # Limpiamos el alta

        # --- AUTOMATIZACIÓN DE 'X' EN ASISTENCIAS ---
        try:
            headers_norm = [normalizar_cabecera_universal(h) for h in headers]
            idx_eq_col = next((i for i, h in enumerate(headers_norm) if h in ["EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS"]), -1)
            equipo = all_rows[fila_idx-1][idx_eq_col].strip() if idx_eq_col != -1 else ""
            
            staff_sheet = client.open(NOMBRE_EXCEL).worksheet("STAFF")
            staff_records = staff_sheet.get_all_records()
            dias_prog = []
            target_eq_norm = normalizar_cabecera_universal(equipo)
            for s in staff_records:
                if normalizar_cabecera_universal(s.get('EQUIPO', '')) == target_eq_norm:
                    dias_prog = parse_dias_entreno(s.get('DIAS ENTRENAMIENTO', ''))
                    break
            
            if dias_prog:
                dt_start = datetime.strptime(fecha_baja, "%d/%m/%Y")
                season_end_year = dt_start.year if dt_start.month <= 8 else dt_start.year + 1
                dt_end = datetime(season_end_year, 8, 31)
                nuevas_filas = []
                curr = dt_start
                while curr <= dt_end:
                    if curr.weekday() in dias_prog:
                        nuevas_filas.append([curr.strftime("%d/%m/%Y"), equipo, nombre, "", "X", "-", "BAJA", "NO"])
                    curr += timedelta(days=1)
                if nuevas_filas:
                    client.open(NOMBRE_EXCEL).worksheet("ASISTENCIAS").append_rows(nuevas_filas)
        except Exception as e: print(f"Error auto-baja: {e}")

        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error en marcar_baja: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/marcar_alta', methods=['POST'])
def marcar_alta():
    nombre = request.json.get('nombre')
    fecha_alta = request.json.get('fecha')
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        all_rows = sheet.get_all_values()
        if not all_rows: return jsonify({"status": "error"}), 404
        headers = [h.strip().upper() for h in all_rows[0]]
        
        idx_nom = headers.index("NOMBRE") if "NOMBRE" in headers else 0

        if "ALTA_DESDE" not in headers:
            idx_alta = len(headers) + 1
            sheet.update_cell(1, idx_alta, "ALTA_DESDE")
        else:
            idx_alta = headers.index("ALTA_DESDE") + 1
            
        # Al dar de ALTA, limpiamos la fecha de BAJA para que vuelva a estar activo
        if "BAJA_DESDE" in headers:
            idx_baja = headers.index("BAJA_DESDE") + 1
        else:
            idx_baja = -1

        fila_idx = -1
        nombre_clean = nombre.strip().lower()
        for i, fila in enumerate(all_rows):
            if len(fila) > idx_nom and fila[idx_nom].strip().lower() == nombre_clean:
                fila_idx = i + 1
                break
        
        if fila_idx == -1: return jsonify({"status": "error", "message": "No se encontró al jugador"}), 404
        
        sheet.update_cell(fila_idx, idx_alta, fecha_alta)
        if idx_baja != -1:
            sheet.update_cell(fila_idx, idx_baja, "") # Limpiamos la baja

        # --- AUTOMATIZACIÓN DE ELIMINAR 'X' EN ASISTENCIAS ---
        try:
            headers_norm = [normalizar_cabecera_universal(h) for h in headers]
            idx_eq_col = next((i for i, h in enumerate(headers_norm) if h in ["EQUIPO", "CATEGORIA", "GRUPO", "EQUIPOS"]), -1)
            equipo = all_rows[fila_idx-1][idx_eq_col].strip() if idx_eq_col != -1 else ""
            
            asis_sheet = client.open(NOMBRE_EXCEL).worksheet("ASISTENCIAS")
            all_asis = asis_sheet.get_all_values()
            dt_alta = datetime.strptime(fecha_alta, "%d/%m/%Y")
            
            rows_to_delete = []
            for i, row in enumerate(all_asis):
                if i == 0: continue
                if len(row) >= 5 and row[4].strip().upper() == 'X':
                    try:
                        dt_row = datetime.strptime(row[0].strip(), "%d/%m/%Y")
                        if dt_row >= dt_alta and row[1].strip().upper() == equipo.upper() and row[2].strip().upper() == nombre.upper():
                            rows_to_delete.append(i + 1)
                    except: continue
            
            if rows_to_delete:
                for r_idx in sorted(rows_to_delete, reverse=True): asis_sheet.delete_rows(r_idx)
        except Exception as e: print(f"Error auto-alta: {e}")

        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error en marcar_alta: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/notificaciones/asistencias_pendientes')
def verificar_asistencias_pendientes():
    try:
        hoy = datetime.now()
        mes_actual = hoy.month
        anio_actual = hoy.year
        
        # 1. Obtener Staff
        staff_sheet = client.open(NOMBRE_EXCEL).worksheet("STAFF")
        staff_data = staff_sheet.get_all_records()
        
        # 2. Obtener Asistencias registradas este mes
        asis_sheet = client.open(NOMBRE_EXCEL).worksheet("ASISTENCIAS")
        asis_records = asis_sheet.get_all_values()[1:]
        
        # Crear un set para búsqueda rápida: "05/05/2026-ALEVIN A"
        registros_hechos = set()
        for r in asis_records:
            if len(r) >= 2:
                registros_hechos.add(f"{r[0]}-{r[1].strip().upper()}")

        notificaciones_enviadas = 0
        
        for coach in staff_data:
            nombre = coach.get('NOMBRE')
            tel = str(coach.get('TELEFONO', '')).strip()
            equipo = str(coach.get('EQUIPO', '')).strip().upper()
            dias_raw = coach.get('DIAS ENTRENAMIENTO', '')
            
            if not tel or not equipo or not dias_raw: continue
            
            dias_validos = parse_dias_entreno(dias_raw)
            faltas = []
            
            # Revisar desde el día 1 hasta hoy
            for d in range(1, hoy.day + 1):
                fecha_check = datetime(anio_actual, mes_actual, d)
                if fecha_check.weekday() in dias_validos:
                    fecha_str = fecha_check.strftime("%d/%m/%Y")
                    key = f"{fecha_str}-{equipo}"
                    
                    if key not in registros_hechos:
                        faltas.append(fecha_str)
            
            if faltas:
                es_hoy = hoy.strftime("%d/%m/%Y") in faltas
                total_faltas = len(faltas)
                
                msg = f"Hola {nombre}, recordatorio de Intranet Club. ⚠️\n\n"
                
                if es_hoy:
                    msg += f"Hoy tienes entrenamiento con el {equipo} y aún no has pasado lista."
                
                if total_faltas > 1 or (total_faltas == 1 and not es_hoy):
                    dias_anteriores = [f for f in faltas if f != hoy.strftime("%d/%m/%Y")]
                    if dias_anteriores:
                        msg += f"\nTambién tienes pendientes los siguientes días de este mes: {', '.join(dias_anteriores)}."
                
                msg += "\n\nPor favor, complétalas lo antes posible para mantener el control del club actualizado. ¡Gracias!"
                
                enviar_whatsapp(tel, msg)
                enviar_push(nombre, msg)
                notificaciones_enviadas += 1
                
        return jsonify({
            "status": "success", 
            "mensaje": f"Proceso completado. Se enviaron {notificaciones_enviadas} avisos."
        })
        
    except Exception as e:
        print(f"Error en notificaciones: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/notificaciones/recordatorio_individual', methods=['POST'])
def recordatorio_individual():
    data = request.json
    equipo_target = data.get('equipo', '').strip().upper()
    
    try:
        hoy = datetime.now()
        mes_actual = hoy.month
        anio_actual = hoy.year
        hoy_str = hoy.strftime("%d/%m/%Y")
        
        # 1. Buscar al entrenador de ese equipo
        staff_sheet = client.open(NOMBRE_EXCEL).worksheet("STAFF")
        coaches = staff_sheet.get_all_records()
        coach = next((c for c in coaches if str(c.get('EQUIPO', '')).strip().upper() == equipo_target), None)
        
        if not coach or not coach.get('TELEFONO'):
            return jsonify({"status": "error", "message": "No se encontró entrenador o teléfono para este equipo"}), 404

        # 2. Calcular qué días le faltan
        asis_sheet = client.open(NOMBRE_EXCEL).worksheet("ASISTENCIAS")
        asis_records = asis_sheet.get_all_values()[1:]
        registros_hechos = set(f"{r[0]}-{r[1].strip().upper()}" for r in asis_records if len(r) >= 2)

        dias_validos = parse_dias_entreno(coach.get('DIAS ENTRENAMIENTO', ''))
        faltas = []
        for d in range(1, hoy.day + 1):
            fecha_check = datetime(anio_actual, mes_actual, d)
            if fecha_check.weekday() in dias_validos:
                f_str = fecha_check.strftime("%d/%m/%Y")
                if f"{f_str}-{equipo_target}" not in registros_hechos:
                    faltas.append(f_str)

        # 3. Construir mensaje personalizado y muy preciso
        es_dia_entreno_hoy = hoy.weekday() in dias_validos
        asis_hoy_hecha = f"{hoy_str}-{equipo_target}" in registros_hechos
        
        msg = f"Hola {coach['NOMBRE']}, recordatorio de Intranet Club para el equipo {equipo_target}. ⚠️\n\n"
        
        if es_dia_entreno_hoy:
            if asis_hoy_hecha:
                msg += f"✅ Hoy ({hoy_str}) ya has registrado la asistencia correctamente."
            else:
                msg += f"❌ Hoy ({hoy_str}) tienes entrenamiento y AÚN NO has registrado la asistencia."
        else:
            msg += f"ℹ️ Hoy ({hoy_str}) no es día de entrenamiento programado para tu equipo."

        anteriores = [f for f in faltas if f != hoy_str]
        if anteriores:
            msg += f"\n\n⚠️ Además, tienes pendientes estos días de este mes: {', '.join(anteriores)}."
        
        if not asis_hoy_hecha or anteriores:
            msg += "\n\nPor favor, complétalas lo antes posible a través de la intranet. ¡Gracias!"
        else:
            msg += "\n\n¡Felicidades! Estás al día con todas tus asistencias del mes. Buen trabajo. ✅"

        # Hemos desactivado WhatsApp para centrarnos únicamente en OneSignal (Push)
        enviar_push(coach['NOMBRE'].lower().strip(), msg) # Enviamos al ID en minúsculas
        
        return jsonify({"status": "success", "message": f"Aviso enviado a {coach['NOMBRE']}"})
    except Exception as e:
        print(f"Error recordatorio_individual: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/cronograma')
def cronograma():
    if not session.get('usuario'): return redirect(url_for('index'))
    
    # Obtenemos la persona de la URL (prioridad), de la sesión o por defecto
    view = request.args.get('view', 'semanal')
    persona_raw = request.args.get('user') or request.args.get('persona') or session.get('usuario') or 'ADMIN'
    # Normalizamos para mostrar en el título (ej: RUBEN)
    persona_display = str(persona_raw).strip().upper()
    
    # Lógica para generar los meses de la temporada (Sep a Ago)
    hoy = datetime.now()
    anio_inicio = hoy.year if hoy.month >= 9 else hoy.year - 1
    meses_anual = []
    meses_nombres = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    
    # Generamos de Septiembre a Agosto
    for i in range(12):
        m = (8 + i) % 12 + 1
        y = anio_inicio + (1 if (8 + i) >= 12 else 0)
        meses_anual.append({
            'num': m,
            'anio': y,
            'label': f"{meses_nombres[m-1]} {y}"
        })

    # Lógica para la semana actual
    start_of_week = hoy - timedelta(days=hoy.weekday())
    semana_actual = []
    dias_nombres = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]
    for i in range(7):
        d = start_of_week + timedelta(days=i)
        semana_actual.append({
            'nombre': dias_nombres[i],
            'fecha_full': d.strftime("%d/%m/%Y"),
            'fecha_iso': d.strftime("%Y-%m-%d")
        })

    return render_template('cronograma_full.html', 
                           meses_anual=meses_anual, 
                           semana_actual=semana_actual, 
                           usuario_sesion=session.get('usuario'),
                           persona_seleccionada=persona_display,
                           view=view)

@app.route('/api/cronograma_data')
def api_cronograma_data():
    # Capturamos parámetros de búsqueda con normalización agresiva
    raw_u = request.args.get('user') or request.args.get('usuario') or session.get('usuario', 'admin')
    user_req = normalizar_id(raw_u)
    raw_view = request.args.get('view') or request.args.get('vista')
    view_req = normalizar_id(raw_view) if raw_view else None

    try:
        client = app.gs_client
        nombre_excel = app.gs_name
        spreadsheet = client.open(nombre_excel)

        todas_hojas = {s.title.upper().strip(): s for s in spreadsheet.worksheets()}
        if "TAREAS CRONOGRAMA" not in todas_hojas:
            return jsonify([])
            
        sheet = todas_hojas["TAREAS CRONOGRAMA"]
        all_v = sheet.get_all_values()
        if not all_v or len(all_v) < 1:
            return jsonify([])

        raw_headers = all_v[0]
        date_headers_list_mode = ['DIAMES', 'FECHAISO', 'FECHA', 'DATE', 'START', 'DIA', 'MES', 'DIA/MES'] + DIAS_MESES_IDENT
        # Buscamos qué cabeceras originales del Excel mapean a estos alias de fecha
        date_keys_found = [h for h in raw_headers if normalizar_cabecera_universal(h) in date_headers_list_mode]

        records = []
        for row in all_v[1:]:
            if not any(str(c).strip() for c in row): continue
            
            item = {}
            for i, h in enumerate(raw_headers):
                val = str(row[i]).strip() if i < len(row) else ""
                h_norm = normalizar_cabecera_universal(h)
                
                if h_norm in date_headers_list_mode and val:
                    item['FECHA_ISO'] = val
                    item['fecha'] = val
                    item['start'] = val
                elif h_norm in ['TAREA', 'TAREAS', 'TITLE', 'TITULO', 'NOMBRE', 'ACTIVIDAD', 'EVENTO', 'DESCRIPCION', 'DESCRIPTION', 'CONTENIDO', 'TEXT', 'CONTENT', 'TAREASCRONOGRAMA'] and val:
                    item['TAREA'] = val
                    item['tarea'] = val
                    item['title'] = val
                    item['titulo'] = val
                elif h_norm in ['SEMANALANUAL', 'VISTA', 'VIEW'] and val:
                    item['VISTA'] = val
                    item['vista'] = val
                elif h_norm in ['PERSONA', 'RESPONSABLE', 'USER', 'COACH', 'ENTRENADOR'] and val:
                    item['RESPONSABLE'] = val
                    item['responsable'] = val
                elif h_norm in ['FRECUENCIA', 'REPETICION', 'REPEAT'] and val:
                    item['FRECUENCIA'] = val
                    item['frecuencia'] = val
                elif h_norm in ['CATEGORIA', 'TIPO', 'CAT', 'DIRECCIONDEPORTIVA'] and val:
                    item['TIPO'] = val
                    item['tipo'] = val
                    item['categoria'] = val
                elif h_norm in ['ESTADO', 'STATUS', 'COMPLETADO'] and val:
                    item['ESTADO'] = val
                    item['estado'] = val
                
                # Mantenemos la clave original por compatibilidad con cualquier otra lógica
                item[h] = val

            # Lógica corregida: Grid (Lunes, Martes...) vs Columna Única (Fecha)
            grid_days = []
            val_f_extracted = ""
            dias_ident = DIAS_MESES_IDENT

            vista_val = item.get('VISTA') or item.get('vista') or 'SEMANAL'
            for dk in date_keys_found:
                h_n = normalizar_cabecera_universal(dk)
                v = item.get(dk, "").strip().upper()
                if not v: continue
                
                # Si la columna es un día y el valor es un marcador o algo que no es un día desdoblado
                if h_n in dias_ident and (v in ['X', 'SI', 'OK', 'V'] or not es_dia_valido_crono(v)):
                    # Normalizamos el nombre del día de la columna inmediatamente
                    grid_days.append(normalizar_dia_cronograma(h_n, vista=vista_val))
                else:
                    if not val_f_extracted: val_f_extracted = v

            val_f = ",".join(grid_days) if grid_days else val_f_extracted

            # LÓGICA DE DESDOBLAMIENTO: Soporte para comas, punto y coma, 'y', o espacios (lunes martes)
            s_f = val_f.replace(';', ',').replace(' y ', ',')
            partes = [x.strip() for x in s_f.split(',') if x.strip()]
            
            if len(partes) == 1:
                tokens = [tk.strip() for tk in partes[0].split() if tk.strip()]
                if tokens and all(es_dia_valido_crono(t) for t in tokens):
                    partes = tokens

            # Procesamos cada parte para generar registros individuales limpios
            if not partes and val_f: partes = [val_f]
            
            for p in partes:
                p_norm = normalizar_dia_cronograma(p, vista=vista_val)
                v_item = item.copy()
                
                # REFUERZO DE ALIAS: Sincronizamos todas las llaves que el frontend JS consulta para pintar el calendario
                v_item['FECHA_ISO'] = p_norm
                v_item['fecha'] = p_norm
                v_item['start'] = p_norm
                v_item['dia'] = p_norm
                v_item['dia/mes'] = p_norm
                v_item['DIA/MES'] = p_norm
                v_item['DIAMES'] = p_norm
                
                # Sincronizamos también los nombres originales de las columnas del Excel
                for dk in date_keys_found:
                    v_item[dk] = p_norm
                
                # Aseguramos que Responsable y Vista existan con valores sólidos para el filtrado posterior
                resp_val = v_item.get('RESPONSABLE') or v_item.get('responsable') or v_item.get('PERSONA') or v_item.get('persona') or ""
                v_item['RESPONSABLE'] = resp_val
                
                vista_val = v_item.get('VISTA') or v_item.get('vista') or v_item.get('SEMANAL/ANUAL') or 'SEMANAL'
                v_item['VISTA'] = vista_val
                
                records.append(v_item)

        # FILTRADO PROFESIONAL: Por Persona y por Vista (Semanal/Anual)
        # Esto garantiza que si estás en el cronograma semanal de Ruben, solo veas sus tareas semanales.
        filtered = []
        user_req_norm = normalizar_id(user_req)
        for r in records:
            resp_val = r.get('RESPONSABLE') or r.get('responsable') or r.get('PERSONA') or r.get('persona') or ""
            match_user = normalizar_id(resp_val) == user_req_norm
            match_view = True
            if view_req:
                v_val = r.get('VISTA') or r.get('vista') or r.get('SEMANAL/ANUAL') or 'SEMANAL'
                match_view = normalizar_id(v_val) == view_req
            
            if match_user and match_view:
                filtered.append(r)

        resp = jsonify(filtered)
        
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return resp
    except Exception as e:
        print(f"Error en api_cronograma_data: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/cronograma_save', methods=['POST'])
def api_cronograma_save():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No se recibieron datos"}), 400

    client = app.gs_client
    nombre_excel = app.gs_name
    try:
        spreadsheet = client.open(nombre_excel)
        # Búsqueda robusta de la pestaña TAREAS (evita duplicados por mayúsculas/minúsculas)
        todas_hojas = {s.title.upper().strip(): s for s in spreadsheet.worksheets()}
        
        if "TAREAS CRONOGRAMA" in todas_hojas:
            sheet = todas_hojas["TAREAS CRONOGRAMA"]
        else:
            # Creamos la pestaña con el orden solicitado
            sheet = spreadsheet.add_worksheet(title="TAREAS CRONOGRAMA", rows="1000", cols="7")
            sheet.append_row(["SEMANAL/ANUAL", "PERSONA", "DIA/MES", "CATEGORIA", "TAREA", "FRECUENCIA", "ESTADO"])

        all_v = sheet.get_all_values()
        if not all_v:
            headers_list = ["SEMANAL/ANUAL", "PERSONA", "DIA/MES", "CATEGORIA", "TAREA", "FRECUENCIA", "ESTADO"]
            sheet.append_row(headers_list)
            norm_headers = [normalizar_cabecera_universal(h) for h in headers_list]
        else:
            raw_headers = all_v[0]
            norm_headers = [normalizar_cabecera_universal(h) for h in raw_headers]

        # Prioridad de alias: campos de usuario (fecha, dia) sobre campos de sistema (start, date)
        val_fecha_raw = data.get('fecha') or data.get('dia/mes') or data.get('dia') or data.get('mes') or data.get('fecha_iso') or data.get('start') or data.get('date') or ""
        val_tarea = data.get('tarea') or data.get('title') or data.get('nombre') or data.get('titulo') or data.get('actividad') or data.get('descripcion') or data.get('description') or data.get('text') or data.get('content') or ""
        val_resp_raw = data.get('responsable') or data.get('persona') or data.get('user') or session.get('usuario', 'admin')
        # Soporte para múltiples responsables (separados por coma)
        responsables = [r.strip() for r in str(val_resp_raw).replace(';', ',').split(',') if r.strip()]
        if not responsables: responsables = [session.get('usuario', 'admin')]

        val_tipo = data.get('categoria') or data.get('tipo') or data.get('cat') or ""
        val_vista = data.get('vista') or data.get('semanal/anual') or data.get('view') or "SEMANAL"
        val_estado = data.get('estado') or data.get('status') or data.get('completado') or "PENDIENTE"

        if not val_fecha_raw or not val_tarea:
            return jsonify({"status": "error", "message": "Fecha y Tarea son campos obligatorios"}), 400

        # GENERAR VARIOS ASIENTOS: Soporte para múltiples días/fechas
        s_f = str(val_fecha_raw).replace(';', ',').replace(' y ', ',')
        instancias = [x.strip() for x in s_f.split(',') if x.strip()]
        
        if len(instancias) == 1:
            tokens = [tk.strip() for tk in instancias[0].split() if tk.strip()]
            if tokens and all(es_dia_valido_crono(t) for t in tokens):
                instancias = tokens

        # Soporte para "Seleccionar Todos"
        instancias_final = []
        for f_inst in instancias:
            if f_inst.upper() in ["TODOS", "TODO", "TOTAL", "ALL", "*", "SELECCIONAR TODOS"]:
                if str(val_vista).upper() == "ANUAL":
                    instancias_final.extend(["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"])
                else:
                    instancias_final.extend(["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"])
            else:
                instancias_final.append(f_inst)

        for resp_name in responsables:
            for f_inst in instancias_final:
                f_inst_norm = normalizar_dia_cronograma(f_inst, vista=val_vista)
                row_map = {
                    "DIAMES": f_inst_norm, "FECHA": f_inst_norm, "FECHAISO": f_inst_norm, "DATE": f_inst_norm, "START": f_inst_norm, "DIA": f_inst_norm, "MES": f_inst_norm, "DIA/MES": f_inst_norm, "LUNES": f_inst_norm, "MARTES": f_inst_norm, "MIERCOLES": f_inst_norm, "JUEVES": f_inst_norm, "VIERNES": f_inst_norm, "SABADO": f_inst_norm, "DOMINGO": f_inst_norm,
                    "TAREAS": str(val_tarea).strip(), "TAREA": str(val_tarea).strip(), "TITLE": str(val_tarea).strip(), "TITULO": str(val_tarea).strip(), "NOMBRE": str(val_tarea).strip(), "ACTIVIDAD": str(val_tarea).strip(), "TAREASCRONOGRAMA": str(val_tarea).strip(),
                    "CATEGORIA": str(val_tipo).strip(), "TIPO": str(val_tipo).strip(), "CAT": str(val_tipo).strip(), "DIRECCIONDEPORTIVA": str(val_tipo).strip(),
                    "PERSONA": str(resp_name).strip(), "RESPONSABLE": str(resp_name).strip(), "USER": str(resp_name).strip(), "COACH": str(resp_name).strip(), "ENTRENADOR": str(resp_name).strip(),
                    "SEMANALANUAL": str(val_vista).strip().upper(), "VISTA": str(val_vista).strip().upper(), "VIEW": str(val_vista).strip().upper(),
                    "FRECUENCIA": str(data.get('frecuencia') or "").strip(), "REPETICION": str(data.get('frecuencia') or "").strip(), "REPEAT": str(data.get('frecuencia') or "").strip(),
                    "ESTADO": str(val_estado).strip().upper(), "COMPLETADO": str(val_estado).strip().upper(), "STATUS": str(val_estado).strip().upper()
                }
                nueva_fila = [row_map.get(h_norm, "") for h_norm in norm_headers]
                sheet.append_row(nueva_fila, value_input_option='USER_ENTERED')

        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error guardando en TAREAS: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/cronograma_update_status', methods=['POST'])
def api_cronograma_update_status():
    data = request.json
    if not data: return jsonify({"status": "error"}), 400
    
    user_norm = normalizar_id(data.get('user'))
    fecha_norm_req = normalizar_crono_busqueda(data.get('fecha'))
    tarea_norm_req = normalizar_crono_busqueda(data.get('tarea'))
    nuevo_estado = str(data.get('estado', 'PENDIENTE')).upper()

    try:
        client = app.gs_client
        spreadsheet = client.open(app.gs_name)
        sheet = spreadsheet.worksheet("TAREAS CRONOGRAMA")
        all_v = sheet.get_all_values()
        if not all_v: return jsonify({"status": "error"}), 404

        headers = [normalizar_cabecera_universal(h) for h in all_v[0]]
        
        def find_col(aliases):
            return next((headers.index(a) for a in aliases if a in headers), -1)

        idx_user = find_col(['PERSONA', 'RESPONSABLE', 'USER', 'COACH', 'ENTRENADOR'])
        idx_fecha = find_col(['DIAMES', 'FECHAISO', 'FECHA', 'DATE', 'START', 'DIA', 'MES', 'DIA/MES'] + DIAS_MESES_IDENT)
        idx_tarea = find_col(['TAREA', 'TAREAS', 'TITLE', 'TITULO', 'NOMBRE', 'ACTIVIDAD', 'EVENTO', 'DESCRIPCION', 'DESCRIPTION', 'CONTENIDO', 'TEXT', 'CONTENT'])
        idx_vista = find_col(['SEMANALANUAL', 'VISTA', 'VIEW'])
        idx_estado = find_col(['COMPLETADO', 'ESTADO', 'STATUS'])
        vista_req = normalizar_id(data.get('vista'))

        if -1 in [idx_user, idx_fecha, idx_tarea, idx_estado]:
            return jsonify({"status": "error", "message": "Columnas no encontradas"}), 500

        fila_idx = -1
        for i, row in enumerate(all_v):
            if i == 0: continue
            if len(row) > max(idx_user, idx_fecha, idx_tarea, idx_vista):
                u = normalizar_id(row[idx_user])
                f_raw = str(row[idx_fecha]).replace(';', ',').replace(' y ', ',')
                parts_row = [normalizar_crono_busqueda(x) for x in f_raw.split(',') if x.strip()]
                if len(parts_row) == 1 and ' ' in parts_row[0]:
                    parts_row = [x.strip() for x in parts_row[0].split() if x.strip()]
                
                match_f = (fecha_norm_req in parts_row)
                t = normalizar_crono_busqueda(row[idx_tarea])
                v = normalizar_id(row[idx_vista]) if idx_vista != -1 else ""

                if u == user_norm and match_f and t == tarea_norm_req and (not vista_req or v == vista_req):
                    fila_idx = i + 1
                    break
        
        if fila_idx != -1:
            sheet.update_cell(fila_idx, idx_estado + 1, nuevo_estado)
            return jsonify({"status": "success"})
        
        return jsonify({"status": "error", "message": "Tarea no encontrada"}), 404
    except Exception as e:
        print(f"Error actualizando estado cronograma: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/cronograma_delete', methods=['POST'])
def api_cronograma_delete():
    data = request.json
    if not data: return jsonify({"status": "error"}), 400
    
    user_req = normalizar_id(data.get('user'))
    fecha_norm_req = normalizar_crono_busqueda(data.get('fecha'))
    tarea_norm_req = normalizar_crono_busqueda(data.get('tarea'))

    try:
        client = app.gs_client
        spreadsheet = client.open(app.gs_name)
        sheet = spreadsheet.worksheet("TAREAS CRONOGRAMA")
        all_v = sheet.get_all_values()
        if not all_v: return jsonify({"status": "error"}), 404

        headers = [normalizar_cabecera_universal(h) for h in all_v[0]]
        
        def find_col(aliases):
            return next((headers.index(a) for a in aliases if a in headers), -1)

        idx_user = find_col(['PERSONA', 'RESPONSABLE', 'USER', 'COACH', 'ENTRENADOR'])
        idx_fecha = find_col(['DIAMES', 'FECHAISO', 'FECHA', 'DATE', 'START', 'DIA', 'MES', 'DIA/MES'] + DIAS_MESES_IDENT)
        idx_tarea = find_col(['TAREA', 'TAREAS', 'TITLE', 'TITULO', 'NOMBRE', 'ACTIVIDAD', 'EVENTO', 'DESCRIPCION', 'DESCRIPTION', 'CONTENIDO', 'TEXT', 'CONTENT'])
        idx_vista = find_col(['SEMANALANUAL', 'VISTA', 'VIEW'])
        vista_req = normalizar_id(data.get('vista'))

        if -1 in [idx_user, idx_fecha, idx_tarea]:
            return jsonify({"status": "error", "message": "Columnas no encontradas"}), 500

        fila_idx = -1
        for i, row in enumerate(all_v):
            if i == 0: continue
            if len(row) > max(idx_user, idx_fecha, idx_tarea, idx_vista):
                u = normalizar_id(row[idx_user])
                f_raw = str(row[idx_fecha]).replace(';', ',').replace(' y ', ',')
                parts_row = [normalizar_crono_busqueda(x) for x in f_raw.split(',') if x.strip()]
                if len(parts_row) == 1 and ' ' in parts_row[0]:
                    parts_row = [x.strip() for x in parts_row[0].split() if x.strip()]
                
                match_f = (fecha_norm_req in parts_row)
                t = normalizar_crono_busqueda(row[idx_tarea])
                v = normalizar_id(row[idx_vista]) if idx_vista != -1 else ""

                if u == user_req and match_f and t == tarea_norm_req and (not vista_req or v == vista_req):
                    fila_idx = i + 1
                    break
        
        if fila_idx != -1:
            sheet.delete_rows(fila_idx)
            return jsonify({"status": "success"})
        
        return jsonify({"status": "error", "message": "Tarea no encontrada"}), 404
    except Exception as e:
        print(f"Error eliminando tarea cronograma: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/cronograma_edit', methods=['POST'])
def api_cronograma_edit():
    """Permite editar cualquier campo de una tarea existente buscando por sus valores originales."""
    data = request.json
    if not data: return jsonify({"status": "error", "message": "No se recibieron datos"}), 400

    # Valores originales para identificar la fila (ID compuesto)
    old_u = normalizar_id(data.get('old_user') or data.get('user'))
    old_f = normalizar_crono_busqueda(data.get('old_fecha') or data.get('fecha'))
    old_t = normalizar_crono_busqueda(data.get('old_tarea') or data.get('tarea'))
    old_v = normalizar_id(data.get('old_vista') or data.get('vista'))

    try:
        client = app.gs_client
        spreadsheet = client.open(app.gs_name)
        sheet = spreadsheet.worksheet("TAREAS CRONOGRAMA")
        all_v = sheet.get_all_values()
        if not all_v: return jsonify({"status": "error", "message": "Hoja vacía"}), 404

        headers_raw = all_v[0]
        headers = [normalizar_cabecera_universal(h) for h in headers_raw]
        
        def find_col(aliases):
            return next((headers.index(a) for a in aliases if a in headers), -1)

        idx_user = find_col(['PERSONA', 'RESPONSABLE', 'USER', 'COACH', 'ENTRENADOR'])
        idx_fecha = find_col(['DIAMES', 'FECHAISO', 'FECHA', 'DATE', 'START', 'DIA', 'MES', 'DIA/MES'] + DIAS_MESES_IDENT)
        idx_tarea = find_col(['TAREA', 'TAREAS', 'TITLE', 'TITULO', 'NOMBRE', 'ACTIVIDAD', 'TAREASCRONOGRAMA', 'EVENTO', 'DESCRIPCION'])
        idx_vista = find_col(['SEMANALANUAL', 'VISTA', 'VIEW'])
        idx_tipo = find_col(['CATEGORIA', 'TIPO', 'CAT', 'DIRECCIONDEPORTIVA'])
        idx_freq = find_col(['FRECUENCIA', 'REPETICION', 'REPEAT'])
        idx_estado = find_col(['ESTADO', 'COMPLETADO', 'STATUS'])

        if -1 in [idx_user, idx_fecha, idx_tarea]:
            return jsonify({"status": "error", "message": "Columnas de búsqueda no encontradas"}), 500

        fila_idx = -1
        for i, row in enumerate(all_v):
            if i == 0: continue
            if len(row) > max(idx_user, idx_fecha, idx_tarea, idx_vista):
                u = normalizar_id(row[idx_user])
                f_raw = str(row[idx_fecha]).replace(';', ',').replace(' y ', ',')
                parts_row = [normalizar_crono_busqueda(x) for x in f_raw.split(',') if x.strip()]
                if len(parts_row) == 1 and ' ' in parts_row[0]:
                    parts_row = [x.strip() for x in parts_row[0].split() if x.strip()]
                
                match_f = (old_f in parts_row)
                t = normalizar_crono_busqueda(row[idx_tarea])
                v = normalizar_id(row[idx_vista]) if idx_vista != -1 else ""

                if u == old_u and match_f and t == old_t and (not old_v or v == old_v):
                    fila_idx = i + 1
                    break
        
        if fila_idx == -1:
            return jsonify({"status": "error", "message": "Tarea no encontrada para editar"}), 404

        val_fecha_raw = data.get('fecha') or data.get('new_fecha') or data.get('dia/mes') or data.get('dia') or data.get('mes') or data.get('fecha_iso') or data.get('start') or ""
        val_tarea = data.get('tarea') or data.get('new_tarea') or data.get('title') or data.get('nombre') or data.get('titulo') or ""
        val_resp = data.get('responsable') or data.get('persona') or data.get('new_user') or data.get('user') or session.get('usuario', 'admin')
        val_tipo = data.get('categoria') or data.get('tipo') or data.get('cat') or ""
        val_vista = data.get('vista') or data.get('view') or data.get('semanal/anual') or "SEMANAL"
        val_freq = data.get('frecuencia')
        val_estado = data.get('estado') or data.get('status')

        # GENERAR VARIOS ASIENTOS AL EDITAR:
        s_f = str(val_fecha_raw).replace(';', ',').replace(' y ', ',')
        instancias = [x.strip() for x in s_f.split(',') if x.strip()]
        
        if len(instancias) == 1:
            tokens = [tk.strip() for tk in instancias[0].split() if tk.strip()]
            if tokens and all(es_dia_valido_crono(t) for t in tokens):
                instancias = tokens

        if not instancias:
            return jsonify({"status": "error", "message": "Fecha obligatoria"}), 400

        # Soporte para "Seleccionar Todos" en edición
        instancias_final = []
        for f_inst in instancias:
            if f_inst.upper() in ["TODOS", "TODO", "TOTAL", "ALL", "*", "SELECCIONAR TODOS"]:
                if str(val_vista).upper() == "ANUAL":
                    instancias_final.extend(["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"])
                else:
                    instancias_final.extend(["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"])
            else:
                instancias_final.append(f_inst)

        # 1. Actualizamos la fila original con la primera instancia (asiento) de forma masiva
        f_first = normalizar_dia_cronograma(instancias_final[0], vista=val_vista)
        row_update = list(all_v[fila_idx-1]) # Copia del contenido actual de la fila
        
        # Aseguramos que la lista sea lo suficientemente larga para evitar IndexError si hay celdas vacías al final
        max_needed_idx = max(idx_user, idx_fecha, idx_tarea, idx_vista, idx_tipo, idx_freq, idx_estado)
        if len(row_update) <= max_needed_idx:
            row_update.extend([""] * (max_needed_idx - len(row_update) + 1))
        
        for i, h_raw in enumerate(headers_raw):
            h = normalizar_cabecera_universal(h_raw)
            if h in ['PERSONA', 'RESPONSABLE', 'USER', 'COACH', 'ENTRENADOR'] and val_resp is not None: row_update[i] = str(val_resp).strip()
            elif normalizar_cabecera_universal(h_raw) in (['DIAMES', 'FECHAISO', 'FECHA', 'DATE', 'START', 'DIA', 'MES', 'DIA/MES'] + DIAS_MESES_IDENT) and f_first is not None: row_update[i] = f_first
            elif h in ['TAREA', 'TAREAS', 'TITLE', 'TITULO', 'NOMBRE', 'ACTIVIDAD', 'TAREASCRONOGRAMA'] and val_tarea is not None: row_update[i] = str(val_tarea).strip()
            elif h in ['SEMANALANUAL', 'VISTA', 'VIEW'] and val_vista is not None: row_update[i] = str(val_vista).strip().upper()
            elif h in ['CATEGORIA', 'TIPO', 'CAT', 'DIRECCIONDEPORTIVA'] and val_tipo is not None: row_update[i] = str(val_tipo).strip()
            elif h in ['FRECUENCIA', 'REPETICION', 'REPEAT'] and val_freq is not None: row_update[i] = str(val_freq).strip()
            elif h in ['ESTADO', 'STATUS', 'COMPLETADO'] and val_estado is not None: row_update[i] = str(val_estado).strip().upper()

        sheet.update(range_name=f'A{fila_idx}', values=[row_update], value_input_option='USER_ENTERED')

        # 2. Si se añadieron más días/opciones, generamos nuevos "asientos" (filas)
        if len(instancias_final) > 1:
            for f_extra in instancias_final[1:]:
                f_norm_ex = normalizar_dia_cronograma(f_extra, vista=val_vista)
                row_map = {
                    "DIAMES": f_norm_ex, "FECHA": f_norm_ex, "FECHAISO": f_norm_ex, "DATE": f_norm_ex, "START": f_norm_ex, "DIA": f_norm_ex, "MES": f_norm_ex, "DIA/MES": f_norm_ex, "LUNES": f_norm_ex, "MARTES": f_norm_ex, "MIERCOLES": f_norm_ex, "JUEVES": f_norm_ex, "VIERNES": f_norm_ex, "SABADO": f_norm_ex, "DOMINGO": f_norm_ex,
                    "TAREAS": str(val_tarea).strip(), "TAREA": str(val_tarea).strip(), "TITLE": str(val_tarea).strip(), "TITULO": str(val_tarea).strip(), "NOMBRE": str(val_tarea).strip(), "ACTIVIDAD": str(val_tarea).strip(), "TAREASCRONOGRAMA": str(val_tarea).strip(),
                    "CATEGORIA": str(val_tipo).strip(), "TIPO": str(val_tipo).strip(), "CAT": str(val_tipo).strip(), "DIRECCIONDEPORTIVA": str(val_tipo).strip(),
                    "PERSONA": str(val_resp).strip(), "RESPONSABLE": str(val_resp).strip(), "USER": str(val_resp).strip(), "COACH": str(val_resp).strip(), "ENTRENADOR": str(val_resp).strip(),
                    "SEMANALANUAL": str(val_vista).strip().upper(), "VISTA": str(val_vista).strip().upper(), "VIEW": str(val_vista).strip().upper(),
                    "FRECUENCIA": str(val_freq or "").strip(), "REPETICION": str(val_freq or "").strip(), "REPEAT": str(val_freq or "").strip(),
                    "ESTADO": str(val_estado).strip().upper(), "COMPLETADO": str(val_estado).strip().upper(), "STATUS": str(val_estado).strip().upper()
                }
                nueva_fila = [row_map.get(h_norm, "") for h_norm in headers]
                sheet.append_row(nueva_fila, value_input_option='USER_ENTERED')

        return jsonify({"status": "success", "message": "Tarea actualizada correctamente"})
    except Exception as e:
        print(f"Error en api_cronograma_edit: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# --- ALQUILERES DE CAMPO (CRONOGRAMA - VISTA ANUAL) ---
ALQUILERES_HEADERS = ["ANIO", "MES", "DIA", "NOMBRE", "HORARIO", "TELEFONO", "CAMPO", "COMENTARIO", "PAGADO", "IMPORTE"]
ALQUILERES_CAMPOS = ["CAMPO F7", "1/2 CAMPO F11 - 1", "1/2 CAMPO F11 - 2", "CAMPO F11 ENTERO"]

def _leer_alquileres_rows():
    client = app.gs_client
    nombre_excel = app.gs_name
    spreadsheet = client.open(nombre_excel)
    todas_hojas = {s.title.upper().strip(): s for s in spreadsheet.worksheets()}
    if "ALQUILERES" not in todas_hojas:
        return None, []
    sheet = todas_hojas["ALQUILERES"]
    all_v = sheet.get_all_values()
    if len(all_v) < 2:
        return sheet, []
    headers = [normalizar_cabecera_universal(h) for h in all_v[0]]
    def idx(name):
        return headers.index(name) if name in headers else -1
    cols = {k: idx(k) for k in ['ANIO', 'MES', 'DIA', 'NOMBRE', 'HORARIO', 'TELEFONO', 'CAMPO', 'COMENTARIO', 'PAGADO', 'IMPORTE']}

    out = []
    for row_num, row in enumerate(all_v[1:], start=2):
        if not any(str(c).strip() for c in row): continue
        def get(key):
            i = cols[key]
            return row[i] if i != -1 and i < len(row) else ""
        out.append({
            "row_id": row_num, "anio": get('ANIO'), "mes": get('MES'), "dia": get('DIA'),
            "nombre": get('NOMBRE'), "horario": get('HORARIO'), "telefono": get('TELEFONO'),
            "campo": get('CAMPO'), "comentario": get('COMENTARIO'),
            "pagado": get('PAGADO'), "importe": get('IMPORTE'),
        })
    return sheet, out

@app.route('/api/alquileres_data')
def api_alquileres_data():
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    anio = request.args.get('anio')
    mes = request.args.get('mes')
    try:
        _, rows = _leer_alquileres_rows()
        out = [r for r in rows if (not anio or str(r['anio']).strip() == str(anio).strip())
               and (not mes or str(r['mes']).strip() == str(mes).strip())]
        return jsonify(out)
    except Exception as e:
        print(f"Error en api_alquileres_data: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/discrepancias_jugadores')
def api_discrepancias_jugadores():
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    if session.get('usuario', '').lower() != 'admin':
        return jsonify({"status": "error", "message": "No autorizado"}), 403
    from jugadores_correlacion import intentar_recalcular_discrepancias_background, discrepancias_status
    intentar_recalcular_discrepancias_background(client, NOMBRE_EXCEL)
    estado = discrepancias_status()
    return jsonify({
        "running": estado.get('running', False),
        "error": estado.get('error'),
        "finished_at": estado.get('finished_at'),
        "avisos": estado.get('resultado', [])
    })

@app.route('/api/discrepancias_jugadores/confirmar', methods=['POST'])
def api_discrepancias_jugadores_confirmar():
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    if session.get('usuario', '').lower() != 'admin':
        return jsonify({"status": "error", "message": "No autorizado"}), 403
    data = request.json or {}
    equipo = data.get('equipo', '').strip()
    listado = data.get('listado', '').strip()
    nombre = data.get('nombre', '').strip()
    if not equipo or not listado or not nombre:
        return jsonify({"status": "error", "message": "Faltan datos"}), 400
    try:
        from jugadores_correlacion import aplicar_confirmar
        aplicar_confirmar(client, NOMBRE_EXCEL, equipo, listado, nombre)
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error en api_discrepancias_jugadores_confirmar: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/discrepancias_jugadores/declinar', methods=['POST'])
def api_discrepancias_jugadores_declinar():
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    if session.get('usuario', '').lower() != 'admin':
        return jsonify({"status": "error", "message": "No autorizado"}), 403
    data = request.json or {}
    equipo = data.get('equipo', '').strip()
    listado = data.get('listado', '').strip()
    nombre = data.get('nombre', '').strip()
    if not equipo or not listado or not nombre:
        return jsonify({"status": "error", "message": "Faltan datos"}), 400
    try:
        from jugadores_correlacion import aplicar_declinar
        aplicar_declinar(equipo, listado, nombre)
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error en api_discrepancias_jugadores_declinar: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/ausencias/alertas')
def api_ausencias_alertas():
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    from ausencias_monitor import intentar_recalcular_ausencias_background, ausencias_status
    intentar_recalcular_ausencias_background(client, NOMBRE_EXCEL)
    estado = ausencias_status()
    return jsonify({
        "running": estado.get('running', False),
        "error": estado.get('error'),
        "finished_at": estado.get('finished_at').strftime('%d/%m/%Y %H:%M') if estado.get('finished_at') else None,
        "alertas": estado.get('resultado', []),
    })


@app.route('/api/ausencias/motivo', methods=['POST'])
def api_ausencias_motivo():
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    data = request.json or {}
    tipo = data.get('tipo', '').strip()
    equipo = data.get('equipo', '').strip()
    nombre = data.get('nombre', '').strip()
    fechas = data.get('fechas', [])
    motivo = data.get('motivo', '').strip()
    if not tipo or not equipo or not nombre or not fechas or not motivo:
        return jsonify({"status": "error", "message": "Faltan datos"}), 400
    try:
        from ausencias_monitor import guardar_motivo, intentar_recalcular_ausencias_background, _state, _lock
        resultado = guardar_motivo(tipo, equipo, nombre, fechas, motivo, session['usuario'])
        # Forzar recálculo en background
        with _lock:
            _state['finished_at'] = None
        intentar_recalcular_ausencias_background(client, NOMBRE_EXCEL)
        return jsonify({"status": "ok", "guardado": resultado})
    except Exception as e:
        print(f"Error en api_ausencias_motivo: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


EQUIPOS_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'equipos_config.json')
HISTORICO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'historico_jugadores.json')

@app.route('/api/equipos_lista')
def api_equipos_lista():
    """Devuelve todos los equipos presentes en JUGADORES, MI EQUIPO y ASISTENCIAS."""
    if not session.get('usuario'): return jsonify({"status": "error"}), 401
    try:
        wb = client.open(NOMBRE_EXCEL)
        col_keys = ["EQUIPO", "EQUIPOS", "CATEGORIA", "GRUPO"]
        equipos = set()
        for sheet_name in ["JUGADORES", "MI EQUIPO", "ASISTENCIAS"]:
            try:
                all_v = wb.worksheet(sheet_name).get_all_values()
                if not all_v:
                    continue
                headers = [normalizar_cabecera_universal(h) for h in all_v[0]]
                idx_eq = next((headers.index(kw) for kw in col_keys if kw in headers), -1)
                if idx_eq == -1:
                    continue
                for r in all_v[1:]:
                    val = str(r[idx_eq]).strip() if len(r) > idx_eq else ''
                    if val:
                        equipos.add(val)
            except Exception:
                pass
        return jsonify({"equipos": sorted(equipos)})
    except Exception as e:
        return jsonify({"equipos": [], "error": str(e)})


@app.route('/api/equipos_config', methods=['GET', 'POST'])
def api_equipos_config():
    if not session.get('usuario'): return jsonify({"status": "error"}), 401
    if session.get('usuario', '').lower() != 'admin': return jsonify({"status": "error", "message": "No autorizado"}), 403
    if request.method == 'POST':
        data = request.json or {}
        os.makedirs(os.path.dirname(EQUIPOS_CONFIG_FILE), exist_ok=True)
        with open(EQUIPOS_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({"status": "ok"})
    try:
        with open(EQUIPOS_CONFIG_FILE, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"temporada": "", "equipos": []})


PRESUPUESTO_OBJETIVO_FILE = 'static/data/presupuesto_objetivo.json'

@app.route('/api/presupuesto_objetivo', methods=['GET', 'POST'])
def api_presupuesto_objetivo():
    if not session.get('usuario'): return jsonify({"status": "error"}), 401
    if request.method == 'POST':
        data = request.json or {}
        os.makedirs(os.path.dirname(PRESUPUESTO_OBJETIVO_FILE), exist_ok=True)
        with open(PRESUPUESTO_OBJETIVO_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({"status": "ok"})
    try:
        with open(PRESUPUESTO_OBJETIVO_FILE, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"ingresos_estimados": 0, "departamentos": {}})


@app.route('/api/historico_jugadores')
def api_historico_jugadores():
    """Devuelve el histórico archivado + la temporada actual desde DATOS JUGADORES."""
    if not session.get('usuario'): return jsonify({"status": "error"}), 401
    try:
        try:
            with open(HISTORICO_FILE, 'r', encoding='utf-8') as f:
                historico = json.load(f)
        except FileNotFoundError:
            historico = {"temporadas": {}}

        # Nombre de la temporada actual
        try:
            with open(EQUIPOS_CONFIG_FILE, 'r', encoding='utf-8') as f:
                ec = json.load(f)
            temporada_actual = (ec.get('temporada') or '').strip() or 'Temporada actual'
        except Exception:
            temporada_actual = 'Temporada actual'

        # Leer DATOS JUGADORES en vivo
        try:
            wb = client.open(NOMBRE_EXCEL)
            sheet = wb.worksheet("DATOS JUGADORES")
            records = sheet.get_all_records()
            jugadores_actuales = []
            for r in records:
                fecha_nac = str(r.get('JUGADOR_FECHA_NACIMIENTO', '')).strip()
                año_nac = None
                if fecha_nac:
                    m = re.search(r'\b(19|20)\d{2}\b', fecha_nac)
                    if m:
                        año_nac = int(m.group(0))
                jugadores_actuales.append({
                    **{k: str(v).strip() for k, v in r.items()},
                    'AÑO_NACIMIENTO': año_nac
                })
            if jugadores_actuales:
                historico['temporadas'][temporada_actual] = {
                    'fecha_cierre': None,
                    'jugadores': jugadores_actuales
                }
        except Exception:
            pass

        return jsonify(historico)
    except Exception as e:
        return jsonify({"temporadas": {}, "error": str(e)})


@app.route('/api/cerrar_temporada', methods=['POST'])
def api_cerrar_temporada():
    """Archiva la temporada actual y limpia todos los datos de la intranet."""
    if not session.get('usuario'): return jsonify({"status": "error"}), 401
    if session.get('usuario', '').lower() != 'admin':
        return jsonify({"status": "error", "message": "No autorizado"}), 403
    try:
        # 1. Leer temporada actual
        try:
            with open(EQUIPOS_CONFIG_FILE, 'r', encoding='utf-8') as f:
                ec = json.load(f)
        except Exception:
            ec = {"temporada": "", "equipos": []}
        temporada = (ec.get('temporada') or '').strip() or 'sin-temporada'

        # 2. Leer DATOS JUGADORES y archivar
        wb = client.open(NOMBRE_EXCEL)
        try:
            sheet_datos = wb.worksheet("DATOS JUGADORES")
            records = sheet_datos.get_all_records()
        except Exception:
            records = []

        jugadores_archivo = []
        for r in records:
            fecha_nac = str(r.get('JUGADOR_FECHA_NACIMIENTO', '')).strip()
            año_nac = None
            if fecha_nac:
                m = re.search(r'\b(19|20)\d{2}\b', fecha_nac)
                if m:
                    año_nac = int(m.group(0))
            jugadores_archivo.append({**{k: str(v).strip() for k, v in r.items()}, 'AÑO_NACIMIENTO': año_nac})

        # 3. Guardar en historico
        try:
            with open(HISTORICO_FILE, 'r', encoding='utf-8') as f:
                historico = json.load(f)
        except Exception:
            historico = {"temporadas": {}}
        if 'temporadas' not in historico:
            historico['temporadas'] = {}
        historico['temporadas'][temporada] = {
            'fecha_cierre': datetime.now().strftime('%Y-%m-%d'),
            'jugadores': jugadores_archivo
        }
        os.makedirs(os.path.dirname(HISTORICO_FILE), exist_ok=True)
        with open(HISTORICO_FILE, 'w', encoding='utf-8') as f:
            json.dump(historico, f, ensure_ascii=False, indent=2)

        # 4. Limpiar hojas de Google Sheets (mantener fila de cabeceras)
        errores_hojas = []
        for hoja_nombre in ["JUGADORES", "MI EQUIPO", "ASISTENCIAS", "DATOS JUGADORES", "GPS_POSICIONES", "PAGOS JUGADORES", "COORDINACION"]:
            try:
                hoja = wb.worksheet(hoja_nombre)
                all_vals = hoja.get_all_values()
                if len(all_vals) > 1:
                    hoja.delete_rows(2, len(all_vals))
            except Exception as e:
                errores_hojas.append(f"{hoja_nombre}: {e}")

        # 5. Limpiar JSON de formaciones
        for fpath in [FORMACIONES_FILE, FORMACIONES_CONFIG_FILE]:
            try:
                with open(fpath, 'w', encoding='utf-8') as f:
                    json.dump({}, f)
            except Exception:
                pass

        # 6. Establecer nueva temporada (mantener equipos configurados)
        nueva_temporada = (request.json or {}).get('nueva_temporada', '').strip()
        ec['temporada'] = nueva_temporada
        with open(EQUIPOS_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(ec, f, ensure_ascii=False, indent=2)

        return jsonify({"status": "ok", "temporada": temporada, "jugadores_archivados": len(jugadores_archivo), "errores": errores_hojas})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/ausencias/motivo_dia', methods=['POST'])
def api_ausencias_motivo_dia():
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    data = request.json or {}
    tipo = data.get('tipo', '').strip()
    equipo = data.get('equipo', '').strip()
    nombre = data.get('nombre', '').strip()
    dias = data.get('dias', [])
    if not tipo or not equipo or not nombre or not dias:
        return jsonify({"status": "error", "message": "Faltan datos"}), 400
    try:
        from ausencias_monitor import guardar_motivo_dia, intentar_recalcular_ausencias_background, _state, _lock
        for d in dias:
            fecha = d.get('fecha', '').strip()
            motivo = d.get('motivo', '').strip()
            if fecha and motivo:
                guardar_motivo_dia(tipo, equipo, nombre, fecha, motivo, session['usuario'])
        with _lock:
            _state['finished_at'] = None
        intentar_recalcular_ausencias_background(client, NOMBRE_EXCEL)
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"Error en api_ausencias_motivo_dia: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


FORMACIONES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'formaciones_indiv.json')
FORMACIONES_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'formaciones_config.json')

@app.route('/api/formaciones_config', methods=['GET', 'POST'])
def api_formaciones_config():
    if not session.get('usuario'): return jsonify({"status": "error"}), 401
    if request.method == 'POST':
        data = request.json or {}
        os.makedirs(os.path.dirname(FORMACIONES_CONFIG_FILE), exist_ok=True)
        with open(FORMACIONES_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({"status": "ok"})
    try:
        with open(FORMACIONES_CONFIG_FILE, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({})

@app.route('/api/formaciones_indiv', methods=['GET', 'POST'])
def api_formaciones_indiv():
    if not session.get('usuario'): return jsonify({"status": "error"}), 401
    if request.method == 'POST':
        data = request.json or {}
        os.makedirs(os.path.dirname(FORMACIONES_FILE), exist_ok=True)
        with open(FORMACIONES_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({"status": "ok"})
    try:
        with open(FORMACIONES_FILE, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({})


@app.route('/api/formaciones_indiv/jugadores_posicion')
def api_formaciones_jugadores_posicion():
    """Devuelve jugadores de un equipo agrupados por posición usando nombres oficiales de JUGADORES."""
    if not session.get('usuario'): return jsonify({"status": "error"}), 401
    equipo = request.args.get('equipo', '').strip()
    if not equipo: return jsonify({"status": "error", "message": "Falta equipo"}), 400
    try:
        # 1. Obtener nombres oficiales de la hoja JUGADORES
        jugadores_oficiales = []
        try:
            sheet_jug = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
            all_v = sheet_jug.get_all_values()
            if all_v:
                headers = [normalizar_cabecera_universal(h) for h in all_v[0]]
                idx_nom = next((headers.index(k) for k in ["NOMBRE"] if k in headers), -1)
                idx_eq  = next((headers.index(k) for k in ["EQUIPO","CATEGORIA","GRUPO","EQUIPOS","EQUIPOACTIVO"] if k in headers), -1)
                idx_baja = next((headers.index(k) for k in ["BAJA_DESDE","BAJA"] if k in headers), -1)
                if idx_nom != -1 and idx_eq != -1:
                    for row in all_v[1:]:
                        eq_val = row[idx_eq].strip() if len(row) > idx_eq else ''
                        if normalizar_cabecera_universal(eq_val) != normalizar_cabecera_universal(equipo):
                            continue
                        if idx_baja != -1 and len(row) > idx_baja and row[idx_baja].strip():
                            continue  # jugador de baja
                        nom = row[idx_nom].strip() if len(row) > idx_nom else ''
                        if nom:
                            jugadores_oficiales.append(nom)
        except Exception as e:
            print(f"[formaciones] Error leyendo JUGADORES: {e}")

        # 2. Obtener posiciones desde MI EQUIPO sheet — cargamos TODOS los jugadores
        #    y dejamos que el cruce con jugadores_oficiales filtre por equipo.
        def _norm(s): return ''.join(s.upper().split())
        pos_lookup = {}        # norm_name -> posicion
        pos_lookup_orig = {}   # norm_name -> nombre original
        me_equipo_names = set() # norm_names that belong to this equipo in MI EQUIPO
        try:
            sheet_me = client.open(NOMBRE_EXCEL).worksheet("MI EQUIPO")
            all_me = sheet_me.get_all_values()
            if all_me:
                h_me = [normalizar_cabecera_universal(h) for h in all_me[0]]
                idx_nom_me = next((h_me.index(k) for k in ["NOMBRE"] if k in h_me), -1)
                idx_eq_me  = next((h_me.index(k) for k in ["EQUIPO","CATEGORIA","GRUPO","EQUIPOS"] if k in h_me), -1)
                idx_pos_me = next((h_me.index(k) for k in ["POSICION"] if k in h_me), -1)
                if idx_nom_me != -1 and idx_pos_me != -1:
                    for row in all_me[1:]:
                        nom_r = row[idx_nom_me].strip() if len(row) > idx_nom_me else ''
                        pos_r = row[idx_pos_me].strip() if len(row) > idx_pos_me else ''
                        eq_r  = row[idx_eq_me].strip() if idx_eq_me != -1 and len(row) > idx_eq_me else ''
                        if nom_r:
                            nk = _norm(nom_r)
                            pos_lookup[nk] = pos_r
                            pos_lookup_orig[nk] = nom_r
                            if _norm(eq_r) == _norm(equipo):
                                me_equipo_names.add(nk)
                    print(f"[formaciones] MI EQUIPO: {len(pos_lookup)} jugadores, {len(me_equipo_names)} en equipo '{equipo}'")
        except Exception as e:
            print(f"[formaciones] Error leyendo MI EQUIPO para posiciones: {e}")

        print(f"[formaciones] equipo={equipo!r}, jugadores_oficiales={len(jugadores_oficiales)}, pos_lookup={len(pos_lookup)}")

        # Fallback: GPS_POSICIONES si MI EQUIPO no tiene posiciones
        if not any(pos_lookup.values()):
            try:
                from gps import obtener_info_jugadores_equipo
                gps_info = obtener_info_jugadores_equipo(equipo)
                for k, v in gps_info.items():
                    pos_lookup.setdefault(_norm(k), v.get('posicion', ''))
                    pos_lookup_orig.setdefault(_norm(k), k)
            except Exception:
                pass

        # 3. Calcular contadores desde formaciones_indiv.json
        contadores = {}
        try:
            with open(FORMACIONES_FILE, 'r', encoding='utf-8') as f:
                fdata = json.load(f)
            for mes_data in fdata.values():
                for semana_data in mes_data.values():
                    for dia_slots in semana_data.values():
                        for slot in (dia_slots if isinstance(dia_slots, list) else []):
                            if slot.get('equipo', '').upper() != equipo.upper():
                                continue
                            jugs = slot.get('jugadores') or ([slot['jugador']] if slot.get('jugador') else [])
                            for jug in jugs:
                                if jug:
                                    contadores[jug] = contadores.get(jug, 0) + 1
        except Exception:
            pass

        # 4. Agrupar por posición usando nombres oficiales de JUGADORES
        result = {}
        for nombre in jugadores_oficiales:
            pos = pos_lookup.get(_norm(nombre), '') or 'Sin posición'
            result.setdefault(pos, []).append({"nombre": nombre, "count": contadores.get(nombre, 0)})

        # Fallback: si JUGADORES no dio resultados, usar directamente MI EQUIPO
        if not result:
            # Filtrar por equipo si tenemos esa info; si no, mostrar todos
            candidates = me_equipo_names if me_equipo_names else set(pos_lookup.keys())
            for nk in candidates:
                nombre_orig = pos_lookup_orig.get(nk, nk)
                pos = pos_lookup.get(nk, '') or 'Sin posición'
                result.setdefault(pos, []).append({"nombre": nombre_orig, "count": contadores.get(nombre_orig, 0)})

        for pos in result:
            result[pos].sort(key=lambda x: x['count'])
        return jsonify({"status": "ok", "posiciones": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/cumpleanos')
def api_cumpleanos():
    if not session.get('usuario'): return jsonify({"cumpleanos": []}), 401
    from datetime import date, timedelta
    def _parse_dia_mes(s):
        if not s: return None
        m = re.match(r'^(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})$', s.strip())
        if m: return int(m.group(1)), int(m.group(2))
        m = re.match(r'^(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})$', s.strip())
        if m: return int(m.group(3)), int(m.group(2))
        return None
    try:
        hoy = date.today()
        dias_obj = [hoy + timedelta(days=i) for i in range(3)]
        sheet = client.open(NOMBRE_EXCEL).worksheet("DATOS JUGADORES")
        all_vals = sheet.get_all_values()
        if not all_vals: return jsonify({'cumpleanos': []})
        headers = [h.strip().upper() for h in all_vals[0]]
        def idx(k): return headers.index(k) if k in headers else -1
        i_nom = idx('JUGADOR_NOMBRE'); i_ape = idx('JUGADOR_APELLIDOS')
        i_fec = idx('JUGADOR_FECHA_NACIMIENTO'); i_eq = idx('JUGADOR_EQUIPO_LETRA')
        if i_fec == -1: return jsonify({'cumpleanos': []})
        grupos = {d: [] for d in dias_obj}
        for row in all_vals[1:]:
            def g(i): return row[i].strip() if i != -1 and len(row) > i else ''
            nom = (g(i_nom) + (' ' + g(i_ape) if i_ape != -1 else '')).strip()
            if not nom: continue
            dm = _parse_dia_mes(g(i_fec))
            if not dm: continue
            dia, mes = dm
            for d in dias_obj:
                if d.day == dia and d.month == mes:
                    grupos[d].append({'nombre': nom, 'equipo': g(i_eq)})
        resultado = []
        for d in dias_obj:
            if grupos[d]:
                resultado.append({'fecha': d.strftime('%d/%m'), 'dia': d.day, 'mes': d.month,
                                   'es_hoy': d == hoy, 'jugadores': grupos[d]})
        return jsonify({'cumpleanos': resultado})
    except Exception as e:
        print(f"[api_cumpleanos] {e}")
        return jsonify({'cumpleanos': []})


@app.route('/api/alquileres_proximos')
def api_alquileres_proximos():
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    if session.get('usuario', '').lower() != 'admin':
        return jsonify({"status": "error", "message": "No autorizado"}), 403
    try:
        _, rows = _leer_alquileres_rows()
        hoy = datetime.now().date()
        limite = hoy + timedelta(days=7)
        out = []
        for r in rows:
            try:
                f = datetime(int(r['anio']), int(r['mes']), int(r['dia'])).date()
            except (ValueError, TypeError):
                continue
            if hoy <= f <= limite and str(r.get('pagado', '')).strip().upper() != 'SI':
                r2 = dict(r)
                r2['fecha_iso'] = f.isoformat()
                out.append(r2)
        out.sort(key=lambda r: r['fecha_iso'])
        return jsonify(out)
    except Exception as e:
        print(f"Error en api_alquileres_proximos: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/alquileres_rango')
def api_alquileres_rango():
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    desde = request.args.get('desde')  # YYYY-MM-DD
    hasta = request.args.get('hasta')  # YYYY-MM-DD
    if not desde or not hasta:
        return jsonify({"status": "error", "message": "Faltan fechas (desde/hasta)"}), 400
    try:
        d1 = datetime.strptime(desde, '%Y-%m-%d').date()
        d2 = datetime.strptime(hasta, '%Y-%m-%d').date()
        _, rows = _leer_alquileres_rows()
        out = []
        for r in rows:
            try:
                f = datetime(int(r['anio']), int(r['mes']), int(r['dia'])).date()
            except (ValueError, TypeError):
                continue
            if d1 <= f <= d2:
                r2 = dict(r)
                r2['fecha_iso'] = f.isoformat()
                out.append(r2)
        out.sort(key=lambda r: (r['fecha_iso'], r.get('horario', '')))
        return jsonify(out)
    except Exception as e:
        print(f"Error en api_alquileres_rango: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/alquiler_save', methods=['POST'])
def api_alquiler_save():
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    data = request.json or {}
    anio = str(data.get('anio', '')).strip()
    mes = str(data.get('mes', '')).strip()
    dia = str(data.get('dia', '')).strip()
    if not anio or not mes or not dia:
        return jsonify({"status": "error", "message": "Año, mes y día son obligatorios"}), 400
    try:
        client = app.gs_client
        nombre_excel = app.gs_name
        spreadsheet = client.open(nombre_excel)
        todas_hojas = {s.title.upper().strip(): s for s in spreadsheet.worksheets()}
        if "ALQUILERES" in todas_hojas:
            sheet = todas_hojas["ALQUILERES"]
        else:
            sheet = spreadsheet.add_worksheet(title="ALQUILERES", rows="1000", cols=str(len(ALQUILERES_HEADERS)))
            sheet.append_row(ALQUILERES_HEADERS)

        fila = [anio, mes, dia, str(data.get('nombre', '')).strip(), str(data.get('horario', '')).strip(),
                str(data.get('telefono', '')).strip(), str(data.get('campo', '')).strip(),
                str(data.get('comentario', '')).strip(), 'SI' if data.get('pagado') else 'NO',
                str(data.get('importe', '')).strip()]
        sheet.append_row(fila, value_input_option='USER_ENTERED')
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error guardando alquiler: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/alquiler_edit', methods=['POST'])
def api_alquiler_edit():
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    data = request.json or {}
    row_id = data.get('row_id')
    if not row_id:
        return jsonify({"status": "error", "message": "Falta row_id"}), 400
    try:
        client = app.gs_client
        nombre_excel = app.gs_name
        sheet = client.open(nombre_excel).worksheet("ALQUILERES")
        fila = [str(data.get('anio', '')).strip(), str(data.get('mes', '')).strip(), str(data.get('dia', '')).strip(),
                str(data.get('nombre', '')).strip(), str(data.get('horario', '')).strip(),
                str(data.get('telefono', '')).strip(), str(data.get('campo', '')).strip(),
                str(data.get('comentario', '')).strip(), 'SI' if data.get('pagado') else 'NO',
                str(data.get('importe', '')).strip()]
        sheet.update(range_name=f"A{row_id}:J{row_id}", values=[fila], value_input_option='USER_ENTERED')
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error editando alquiler: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/alquiler_pagado', methods=['POST'])
def api_alquiler_pagado():
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    data = request.json or {}
    row_id = data.get('row_id')
    if not row_id:
        return jsonify({"status": "error", "message": "Falta row_id"}), 400
    try:
        client = app.gs_client
        nombre_excel = app.gs_name
        sheet = client.open(nombre_excel).worksheet("ALQUILERES")
        headers = [normalizar_cabecera_universal(h) for h in sheet.row_values(1)]
        i_pag = headers.index('PAGADO') if 'PAGADO' in headers else -1
        i_imp = headers.index('IMPORTE') if 'IMPORTE' in headers else -1
        if i_pag != -1:
            sheet.update_cell(int(row_id), i_pag + 1, 'SI' if data.get('pagado') else 'NO')
        if i_imp != -1 and data.get('importe') is not None:
            sheet.update_cell(int(row_id), i_imp + 1, str(data.get('importe', '')).strip())
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error actualizando pago de alquiler: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/alquiler_delete', methods=['POST'])
def api_alquiler_delete():
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    data = request.json or {}
    row_id = data.get('row_id')
    if not row_id:
        return jsonify({"status": "error", "message": "Falta row_id"}), 400
    try:
        client = app.gs_client
        nombre_excel = app.gs_name
        sheet = client.open(nombre_excel).worksheet("ALQUILERES")
        sheet.delete_rows(int(row_id))
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error eliminando alquiler: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# --- CONFIGURACIÓN DE EMAIL PARA RECIBOS ---
def _get_config_sheet():
    spreadsheet = client.open(NOMBRE_EXCEL)
    todas_hojas = {s.title.upper().strip(): s for s in spreadsheet.worksheets()}
    if "CONFIGURACION" in todas_hojas:
        return todas_hojas["CONFIGURACION"]
    return spreadsheet.add_worksheet(title="CONFIGURACION", rows="100", cols="2")

def _leer_config_email_recibos():
    sheet = _get_config_sheet()
    all_v = sheet.get_all_values()
    for i, row in enumerate(all_v):
        if len(row) > 0 and row[0].strip() == 'EMAIL_RECIBOS':
            try:
                return sheet, i + 1, json.loads(row[1])
            except (json.JSONDecodeError, IndexError):
                return sheet, i + 1, {}
    return sheet, None, {}

@app.route('/api/config_email_recibos', methods=['GET', 'POST'])
def api_config_email_recibos():
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    if session.get('usuario', '').lower() != 'admin':
        return jsonify({"status": "error", "message": "No autorizado"}), 403
    try:
        if request.method == 'GET':
            _, _, valor = _leer_config_email_recibos()
            return jsonify({"email": valor.get('email', ''), "configurado": bool(valor.get('email') and valor.get('app_password'))})

        data = request.json or {}
        email = str(data.get('email', '')).strip()
        app_password = str(data.get('app_password', '')).strip().replace(' ', '')
        sheet, idx_fila, _ = _leer_config_email_recibos()
        valor_json = json.dumps({"email": email, "app_password": app_password})
        if idx_fila is None:
            sheet.append_row(['EMAIL_RECIBOS', valor_json])
        else:
            sheet.update_cell(idx_fila, 2, valor_json)
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error en api_config_email_recibos: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# --- RECIBOS DE PAGO (PDF/HTML imprimible + envío por email) ---
def _enviar_email_recibo(destinatario, asunto, cuerpo_html):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    _, _, config = _leer_config_email_recibos()
    remitente = config.get('email', '')
    app_password = config.get('app_password', '')
    if not remitente or not app_password:
        raise ValueError("El email del club no está configurado. Ve a Usuarios y Contraseñas para configurarlo.")

    msg = MIMEMultipart('alternative')
    msg['Subject'] = asunto
    msg['From'] = remitente
    msg['To'] = destinatario
    msg.attach(MIMEText(cuerpo_html, 'html'))

    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(remitente, app_password)
        server.sendmail(remitente, destinatario, msg.as_string())

def _generar_html_recibo(asiento):
    importe_total = asiento.get('importe', 0)
    es_gasto = importe_total < 0
    titulo = "RECIBO DE PAGO" if not es_gasto else "JUSTIFICANTE DE GASTO"
    color_importe = '#dc2626' if es_gasto else '#15803d'

    conceptos = asiento.get('conceptos') or [{
        'concepto': asiento.get('concepto') or asiento.get('descripcion', '-'),
        'importe': importe_total
    }]
    filas_conceptos = ''.join(
        f'<tr>'
        f'<td style="padding:1.5px 0; font-size:8px; color:#1e293b;">{c["concepto"]}</td>'
        f'<td style="padding:1.5px 0; font-size:8px; text-align:right; font-weight:700; color:#1e293b;">{abs(c["importe"]):.2f}€</td>'
        f'</tr>'
        for c in conceptos
    )

    return f"""
    <div style="width: 9.6cm; height: 7cm; border-radius: 12px; overflow: hidden; font-family: 'Segoe UI', Arial, sans-serif;
                box-shadow: 0 3px 10px rgba(0,0,0,0.12); border: 1px solid #e2e8f0; box-sizing: border-box;
                display: flex; flex-direction: column; background: white;">
        <div style="background: linear-gradient(135deg, #E31E24, #9f1218); color: white; padding: 8px 12px;
                    display: flex; align-items: center; justify-content: space-between;">
            <div>
                <div style="font-weight: 900; font-size: 11px; letter-spacing: 0.4px;">CLUB FUENTELARREYNA</div>
                <div style="font-size: 7.5px; opacity: 0.9; text-transform: uppercase; letter-spacing: 0.6px; margin-top: 1px;">{titulo}</div>
            </div>
            <img src="/static/ESCUDO SIN FONDO.png" style="height: 30px; width: auto; filter: drop-shadow(0 1px 2px rgba(0,0,0,0.25));">
        </div>
        <div style="flex: 1; padding: 8px 12px; font-size: 8.5px; color: #1e293b; overflow: hidden;">
            <table style="width: 100%; border-collapse: collapse;">
                <tr><td style="padding: 1.5px 0; font-weight: 700; color: #94a3b8; width: 32%; text-transform: uppercase; font-size: 7.5px;">Nº Asiento</td><td style="padding: 1.5px 0; font-weight: 700;">#{asiento.get('n_asiento', '-')}</td></tr>
                <tr><td style="padding: 1.5px 0; font-weight: 700; color: #94a3b8; text-transform: uppercase; font-size: 7.5px;">Fecha</td><td style="padding: 1.5px 0;">{asiento.get('fecha', '-')}</td></tr>
                <tr><td style="padding: 1.5px 0; font-weight: 700; color: #94a3b8; text-transform: uppercase; font-size: 7.5px;">Nombre</td><td style="padding: 1.5px 0;">{asiento.get('nombre', '-')}</td></tr>
                <tr><td style="padding: 1.5px 0; font-weight: 700; color: #94a3b8; text-transform: uppercase; font-size: 7.5px;">Equipo</td><td style="padding: 1.5px 0;">{asiento.get('equipo', '-')}</td></tr>
            </table>
            <div style="margin-top: 5px; border-top: 1px dashed #e2e8f0; padding-top: 4px;">
                <table style="width: 100%; border-collapse: collapse;">{filas_conceptos}</table>
            </div>
            <div style="margin-top: 6px; text-align: right; border-top: 1px dashed #e2e8f0; padding-top: 5px;">
                <span style="font-size: 7px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.4px; margin-right: 6px;">Importe total</span>
                <span style="font-size: 16px; font-weight: 900; color: {color_importe};">{abs(importe_total):.2f}€</span>
            </div>
        </div>
        <div style="border-top: 1px solid #f1f5f9; padding: 5px 12px; font-size: 6px; color: #cbd5e1; text-align: center; letter-spacing: 0.2px;">
            DOCUMENTO GENERADO AUTOMÁTICAMENTE · INTRANET CLUB FUENTELARREYNA
        </div>
    </div>
    """

def _obtener_asiento_por_n(n_asiento):
    """Devuelve el asiento solicitado. Si forma parte de un pago repartido (Nº ASIENTO
    con sufijo "_N"), agrega TODAS las filas del mismo grupo (misma base) en una sola
    entrada: lista de conceptos + importe total, en vez de solo la fila individual."""
    from financiero import normalizar_cabeceras, get_friendly_concepto
    sheet_fin = client.open(NOMBRE_EXCEL).worksheet("FINANCIERO")
    all_v = sheet_fin.get_all_values()
    headers_norm = normalizar_cabeceras(all_v[0])
    idx_asi = -1
    for variant in ['N_ASIENTO', 'Nº_ASIENTO', 'ASIENTO']:
        if variant in headers_norm:
            idx_asi = headers_norm.index(variant); break
    if idx_asi == -1:
        return None, None, None

    base_buscado = str(n_asiento).strip().replace('#', '').split('_')[0]

    filas_grupo = []
    for i, row in enumerate(all_v):
        if i == 0: continue
        if len(row) <= idx_asi: continue
        raw_n = str(row[idx_asi]).strip().replace('#', '')
        if raw_n.split('_')[0] != base_buscado:
            continue
        item = {}
        for j, h in enumerate(headers_norm):
            if j < len(row):
                item[h] = row[j]
        try:
            importe_val = float(str(item.get('IMPORTE', '0')).replace(',', '.').replace('€', '').strip() or 0)
        except ValueError:
            importe_val = 0
        filas_grupo.append({'item': item, 'fila': i + 1, 'raw_n': raw_n, 'importe': importe_val})

    if not filas_grupo:
        return sheet_fin, headers_norm, None

    primero = filas_grupo[0]['item']
    total_importe = sum(f['importe'] for f in filas_grupo)
    conceptos = [{
        'concepto': get_friendly_concepto(f['item'].get('CONCEPTO', '')) or f['item'].get('CONCEPTO', ''),
        'importe': f['importe']
    } for f in filas_grupo]

    return sheet_fin, headers_norm, {
        'n_asiento': base_buscado, 'fecha': primero.get('FECHA', ''),
        'nombre': primero.get('NOMBRE', ''), 'equipo': primero.get('EQUIPO', ''),
        'concepto': primero.get('CONCEPTO', ''), 'descripcion': primero.get('DESCRIPCION', ''),
        'conceptos': conceptos, 'importe': total_importe,
        'filas': [f['fila'] for f in filas_grupo],
        'justificante_enviado': primero.get('JUSTIFICANTE_ENVIADO', '')
    }

@app.route('/api/recibo/<n_asiento>')
def api_recibo_obtener(n_asiento):
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    try:
        _, _, asiento = _obtener_asiento_por_n(n_asiento)
        if not asiento:
            return jsonify({"status": "error", "message": "Asiento no encontrado"}), 404
        return jsonify({"status": "success", "asiento": asiento, "html": _generar_html_recibo(asiento)})
    except Exception as e:
        print(f"Error en api_recibo_obtener: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/recibo/enviar', methods=['POST'])
def api_recibo_enviar():
    if not session.get('usuario'): return jsonify({"status": "error", "message": "No session"}), 401
    data = request.json or {}
    n_asiento = str(data.get('n_asiento', '')).strip()
    email_destino = str(data.get('email', '')).strip()
    if not n_asiento or not email_destino:
        return jsonify({"status": "error", "message": "Falta el asiento o el email"}), 400
    try:
        sheet_fin, headers_norm, asiento = _obtener_asiento_por_n(n_asiento)
        if not asiento:
            return jsonify({"status": "error", "message": "Asiento no encontrado"}), 404

        html_recibo = _generar_html_recibo(asiento)
        _enviar_email_recibo(email_destino, f"Recibo Club Fuentelarreyna - Asiento #{n_asiento}", html_recibo)

        fecha_envio = datetime.now().strftime('%d/%m/%Y')
        if 'JUSTIFICANTE_ENVIADO' not in headers_norm:
            sheet_fin.update_cell(1, len(headers_norm) + 1, "JUSTIFICANTE_ENVIADO")
            headers_norm.append('JUSTIFICANTE_ENVIADO')
        idx_just = headers_norm.index('JUSTIFICANTE_ENVIADO')
        for fila_num in asiento.get('filas', [asiento.get('fila')]):
            if fila_num:
                sheet_fin.update_cell(fila_num, idx_just + 1, fecha_envio)

        return jsonify({"status": "success", "fecha_envio": fecha_envio})
    except ValueError as ve:
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        print(f"Error en api_recibo_enviar: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# --- PUSH DE AVISO + COMENTARIO (INFORMES) ---
@app.route('/api/informes/enviar_push_equipacion', methods=['POST'])
def api_enviar_push_equipacion():
    if not session.get('usuario'):
        return jsonify({"status": "error", "message": "No session"}), 401
    data = request.json or {}
    destinatario = (data.get('usuario') or '').strip()
    comentario = (data.get('comentario') or '').strip()
    contexto = (data.get('contexto') or '').strip()
    if not destinatario or not comentario:
        return jsonify({"status": "error", "message": "Falta usuario destinatario o comentario"}), 400
    mensaje = f"{contexto}\n\n{comentario}" if contexto else comentario
    try:
        enviar_push(destinatario, mensaje)
        return jsonify({"status": "success", "message": f"Push enviado a {destinatario}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- ACCESOS RÁPIDOS CONFIGURABLES (buscador/cabecera) ---
ACCESOS_RAPIDOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data')

@app.route('/api/accesos_rapidos', methods=['GET'])
def api_accesos_rapidos_get():
    usuario = session.get('usuario')
    if not usuario:
        return jsonify([])
    path = os.path.join(ACCESOS_RAPIDOS_DIR, f'accesos_rapidos_{usuario}.json')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify([])

@app.route('/api/accesos_rapidos', methods=['POST'])
def api_accesos_rapidos_post():
    usuario = session.get('usuario')
    if not usuario:
        return jsonify({"status": "error", "message": "No session"}), 401
    data = request.json or {}
    accesos = data.get('accesos', [])
    os.makedirs(ACCESOS_RAPIDOS_DIR, exist_ok=True)
    path = os.path.join(ACCESOS_RAPIDOS_DIR, f'accesos_rapidos_{usuario}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(accesos, f, ensure_ascii=False, indent=2)
    return jsonify({"status": "success"})

# ============================================================
# GESTOR DE DOCUMENTOS / ARCHIVOS
# ============================================================
def _safe_archivos_path(rel_path):
    """Resuelve rel_path dentro de ARCHIVOS_FOLDER sin escapar."""
    clean = os.path.normpath(rel_path or '').lstrip('/\\')
    full  = os.path.realpath(os.path.join(ARCHIVOS_FOLDER, clean))
    base  = os.path.realpath(ARCHIVOS_FOLDER)
    if not full.startswith(base):
        raise ValueError("Ruta no permitida")
    return full

def _tiene_acceso_archivos():
    u = (session.get('usuario') or '').lower()
    return u == 'admin' or session.get('permisos', {}).get('ARCHIVOS') == 'SI'

@app.route('/archivos')
def archivos_manager():
    if not session.get('usuario'): return redirect(url_for('index'))
    if not _tiene_acceso_archivos(): return "Acceso denegado", 403
    return render_template('archivos.html', usuario=session.get('usuario'),
                           permisos=session.get('permisos', {}))

@app.route('/api/archivos/listar')
def api_archivos_listar():
    if not session.get('usuario'): return jsonify({'error': 'No auth'}), 401
    rel = request.args.get('path', '')
    try:
        full = _safe_archivos_path(rel)
        items = []
        for name in sorted(os.listdir(full), key=lambda n: (not os.path.isdir(os.path.join(full, n)), n.lower())):
            item_path = os.path.join(full, name)
            is_dir = os.path.isdir(item_path)
            rel_item = (rel.rstrip('/') + '/' + name).lstrip('/')
            items.append({
                'nombre': name,
                'tipo': 'carpeta' if is_dir else 'archivo',
                'ruta': rel_item,
                'tamano': 0 if is_dir else os.path.getsize(item_path),
                'fecha': datetime.fromtimestamp(os.path.getmtime(item_path)).strftime('%d/%m/%Y %H:%M'),
                'ext': '' if is_dir else os.path.splitext(name)[1].lower()
            })
        return jsonify({'items': items, 'path': rel})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/archivos/carpeta', methods=['POST'])
def api_archivos_carpeta():
    if not session.get('usuario') or not _tiene_acceso_archivos():
        return jsonify({'error': 'No auth'}), 401
    data = request.get_json() or {}
    nombre = (data.get('nombre') or '').strip()
    rel    = data.get('path', '')
    if not nombre or any(c in nombre for c in r'/\..'):
        return jsonify({'error': 'Nombre inválido'}), 400
    try:
        nueva = os.path.join(_safe_archivos_path(rel), nombre)
        if os.path.exists(nueva): return jsonify({'error': 'Ya existe'}), 409
        os.makedirs(nueva)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/archivos/subir', methods=['POST'])
def api_archivos_subir():
    if not session.get('usuario') or not _tiene_acceso_archivos():
        return jsonify({'error': 'No auth'}), 401
    from werkzeug.utils import secure_filename
    rel   = request.form.get('path', '')
    f     = request.files.get('archivo')
    if not f or not f.filename: return jsonify({'error': 'Sin archivo'}), 400
    try:
        fname = secure_filename(f.filename)
        dest  = os.path.join(_safe_archivos_path(rel), fname)
        f.save(dest)
        return jsonify({'ok': True, 'nombre': fname})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/archivos/eliminar', methods=['POST'])
def api_archivos_eliminar():
    if not session.get('usuario') or not _tiene_acceso_archivos():
        return jsonify({'error': 'No auth'}), 401
    import shutil
    data = request.get_json() or {}
    try:
        full = _safe_archivos_path(data.get('ruta', ''))
        if full == os.path.realpath(ARCHIVOS_FOLDER): return jsonify({'error': 'No puedes borrar la raíz'}), 400
        if os.path.isdir(full):  shutil.rmtree(full)
        elif os.path.isfile(full): os.remove(full)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/archivos/ver/<path:rel_path>')
def archivos_ver(rel_path):
    if not session.get('usuario'): return redirect(url_for('index'))
    from flask import send_file
    try:
        return send_file(_safe_archivos_path(rel_path))
    except Exception as e:
        return str(e), 404

@app.route('/editor-excel')
def editor_excel():
    if not session.get('usuario'): return redirect(url_for('index'))
    if not _tiene_acceso_archivos(): return "Acceso denegado", 403
    return render_template('editor_excel.html', usuario=session.get('usuario'))

@app.route('/api/editor_excel/guardar', methods=['POST'])
def api_editor_excel_guardar():
    if not session.get('usuario') or not _tiene_acceso_archivos():
        return jsonify({'error': 'No auth'}), 401
    import json as json_lib
    data = request.get_json() or {}
    nombre = data.get('nombre', 'documento').strip() or 'documento'
    sheets = data.get('data', [])
    rel_path = data.get('path', '').strip()
    from werkzeug.utils import secure_filename
    try:
        if rel_path:
            full_path = _safe_archivos_path(rel_path)
        else:
            fname = secure_filename(nombre + '.json')
            full_path = os.path.join(ARCHIVOS_FOLDER, fname)
        with open(full_path, 'w', encoding='utf-8') as fh:
            json_lib.dump({'nombre': nombre, 'sheets': sheets}, fh, ensure_ascii=False)
        rel = os.path.relpath(full_path, ARCHIVOS_FOLDER).replace('\\', '/')
        return jsonify({'ok': True, 'path': rel})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

# ============================================================
# DROPBOX SYNC
# ============================================================
_DBX_CONFIG_PATH = os.path.join(BASE_DIR, 'dropbox_config.json')

def _get_dropbox_client():
    import json as _json
    import dropbox
    if not os.path.exists(_DBX_CONFIG_PATH):
        raise ValueError("No hay configuración de Dropbox")
    with open(_DBX_CONFIG_PATH, encoding='utf-8') as f:
        cfg = _json.load(f)
    token = cfg.get('access_token', '')
    if not token:
        raise ValueError("Token de Dropbox vacío")
    return dropbox.Dropbox(token), cfg.get('dropbox_folder', '/IntranetClub')

@app.route('/api/dropbox/estado')
def api_dropbox_estado():
    if not session.get('usuario') or not _tiene_acceso_archivos():
        return jsonify({'error': 'No auth'}), 401
    try:
        dbx, folder = _get_dropbox_client()
        acc = dbx.users_get_current_account()
        return jsonify({'ok': True, 'nombre': acc.name.display_name, 'email': acc.email, 'folder': folder})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/dropbox/subir', methods=['POST'])
def api_dropbox_subir():
    """Sube todos los archivos de static/archivos/ a Dropbox."""
    if not session.get('usuario') or not _tiene_acceso_archivos():
        return jsonify({'error': 'No auth'}), 401
    try:
        import dropbox
        dbx, dbx_folder = _get_dropbox_client()
        subidos, errores = [], []
        base = os.path.realpath(ARCHIVOS_FOLDER)
        for root, dirs, files in os.walk(base):
            for fname in files:
                local_path = os.path.join(root, fname)
                rel = os.path.relpath(local_path, base).replace('\\', '/')
                dbx_path = dbx_folder.rstrip('/') + '/' + rel
                try:
                    with open(local_path, 'rb') as f:
                        dbx.files_upload(f.read(), dbx_path,
                                         mode=dropbox.files.WriteMode.overwrite,
                                         mute=True)
                    subidos.append(rel)
                except Exception as ex:
                    errores.append({'archivo': rel, 'error': str(ex)})
        return jsonify({'ok': True, 'subidos': len(subidos), 'errores': errores})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/dropbox/descargar', methods=['POST'])
def api_dropbox_descargar():
    """Descarga archivos nuevos/actualizados de Dropbox a static/archivos/."""
    if not session.get('usuario') or not _tiene_acceso_archivos():
        return jsonify({'error': 'No auth'}), 401
    try:
        import dropbox
        dbx, dbx_folder = _get_dropbox_client()
        descargados, errores = [], []

        def _listar(path):
            try:
                res = dbx.files_list_folder(path, recursive=True)
                entries = list(res.entries)
                while res.has_more:
                    res = dbx.files_list_folder_continue(res.cursor)
                    entries += res.entries
                return entries
            except dropbox.exceptions.ApiError:
                return []

        entries = _listar(dbx_folder)
        for entry in entries:
            if not isinstance(entry, dropbox.files.FileMetadata):
                continue
            rel = entry.path_display[len(dbx_folder):].lstrip('/')
            local_path = os.path.join(ARCHIVOS_FOLDER, rel.replace('/', os.sep))
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            # Solo descarga si no existe o es más reciente en Dropbox
            if not os.path.exists(local_path) or \
               entry.server_modified.timestamp() > os.path.getmtime(local_path):
                try:
                    _, resp = dbx.files_download(entry.path_display)
                    with open(local_path, 'wb') as f:
                        f.write(resp.content)
                    descargados.append(rel)
                except Exception as ex:
                    errores.append({'archivo': rel, 'error': str(ex)})
        return jsonify({'ok': True, 'descargados': len(descargados), 'errores': errores})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

# ============================================================
# ─── ADJUNTOS DE TAREAS ────────────────────────────────────────────────────────

_TAREAS_ADJ_DIR  = os.path.join(BASE_DIR, 'static', 'archivos', '_tareas_adjuntos')
_TAREAS_ADJ_JSON = os.path.join(BASE_DIR, 'static', 'data', 'tareas_adjuntos.json')

def _leer_tareas_adj():
    import json as _j
    try:
        with open(_TAREAS_ADJ_JSON, encoding='utf-8') as f:
            return _j.load(f)
    except Exception:
        return {}

def _guardar_tareas_adj(data):
    import json as _j
    os.makedirs(os.path.dirname(_TAREAS_ADJ_JSON), exist_ok=True)
    with open(_TAREAS_ADJ_JSON, 'w', encoding='utf-8') as f:
        _j.dump(data, f, ensure_ascii=False, indent=2)

import re as _re

def _key_to_folder(key):
    """Convierte una clave de adjunto a nombre de carpeta válido en Windows."""
    return _re.sub(r'[^\w\-.]', '_', key)

@app.route('/api/tareas/adjuntos/todas')
def api_tareas_adjuntos_todas():
    if not session.get('usuario'):
        return jsonify({'error': 'No auth'}), 401
    data = _leer_tareas_adj()
    keys = [k for k, v in data.items() if v]
    return jsonify({'keys': keys})

@app.route('/api/tareas/adjuntos')
def api_tareas_adjuntos_listar():
    if not session.get('usuario'):
        return jsonify({'error': 'No auth'}), 401
    key = request.args.get('key', '')
    data = _leer_tareas_adj()
    archivos = []
    folder = _key_to_folder(key)
    for nombre in data.get(key, []):
        url = '/static/archivos/_tareas_adjuntos/' + folder + '/' + nombre
        archivos.append({'nombre': nombre, 'url': url})
    return jsonify({'archivos': archivos})

@app.route('/api/tareas/adjuntos/subir', methods=['POST'])
def api_tareas_adjuntos_subir():
    if not session.get('usuario'):
        return jsonify({'ok': False, 'error': 'No auth'}), 401
    try:
        key = request.form.get('key', '').strip()
        f   = request.files.get('archivo')
        if not key or not f:
            return jsonify({'ok': False, 'error': 'Faltan datos'})
        from werkzeug.utils import secure_filename
        nombre  = secure_filename(f.filename)
        folder  = _key_to_folder(key)
        carpeta = os.path.join(_TAREAS_ADJ_DIR, folder)
        os.makedirs(carpeta, exist_ok=True)
        f.save(os.path.join(carpeta, nombre))
        data  = _leer_tareas_adj()
        lista = data.setdefault(key, [])
        if nombre not in lista:
            lista.append(nombre)
        _guardar_tareas_adj(data)
        return jsonify({'ok': True, 'nombre': nombre})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/tareas/adjuntos/eliminar', methods=['POST'])
def api_tareas_adjuntos_eliminar():
    if not session.get('usuario'):
        return jsonify({'ok': False, 'error': 'No auth'}), 401
    try:
        body   = request.get_json(force=True) or {}
        key    = body.get('key', '').strip()
        nombre = body.get('nombre', '').strip()
        if not key or not nombre:
            return jsonify({'ok': False, 'error': 'Faltan datos'})
        from werkzeug.utils import secure_filename
        nombre_safe = secure_filename(nombre)
        folder = _key_to_folder(key)
        ruta   = os.path.join(_TAREAS_ADJ_DIR, folder, nombre_safe)
        if os.path.exists(ruta):
            os.remove(ruta)
        data = _leer_tareas_adj()
        if key in data and nombre_safe in data[key]:
            data[key].remove(nombre_safe)
        _guardar_tareas_adj(data)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

# ───────────────────────────────────────────────────────────────────────────────

# --- REGISTRO DE BLUEPRINTS ---
from financiero import financiero_bp
from deportivo import deportivo_bp
from jugadores_datos import jugadores_datos_bp
from perfiles import perfiles_bp
from jugadores_detalles import jugadores_detalles_bp # Importar el nuevo blueprint
from ropa import ropa_bp
from stock_ropa import stock_ropa_bp
from mi_equipo import mi_equipo_bp
from formulario_partido import formulario_partido_bp # Importar el nuevo blueprint
from inscripcion import inscripcion_bp # Importar el blueprint de inscripción
from asistente_ia import asistente_bp # Importar el blueprint del asistente IA
from horarios import horarios_bp # Importar el blueprint de horarios (RFFM)
from videoanalisis import videoanalisis_bp
from gps import gps_bp
from fisioterapia import fisio_bp
from rrss import rrss_bp
from loteria import loteria_bp
app.register_blueprint(financiero_bp)
app.register_blueprint(loteria_bp)
app.register_blueprint(deportivo_bp)
app.register_blueprint(jugadores_datos_bp)
app.register_blueprint(perfiles_bp)
app.register_blueprint(ropa_bp)
app.register_blueprint(stock_ropa_bp)
app.register_blueprint(jugadores_detalles_bp) # Registrar el nuevo blueprint
app.register_blueprint(mi_equipo_bp)
app.register_blueprint(formulario_partido_bp)
app.register_blueprint(inscripcion_bp) # Registrar el blueprint de inscripción
app.register_blueprint(asistente_bp) # Registrar el blueprint del asistente IA
app.register_blueprint(horarios_bp) # Registrar el blueprint de horarios (RFFM)
app.register_blueprint(videoanalisis_bp)
app.register_blueprint(gps_bp)
app.register_blueprint(fisio_bp)
app.register_blueprint(rrss_bp)

# --- SEGUIMIENTO COORD ENTRENAMIENTOS ---
_SEG_COORD_FILE = os.path.join('static', 'data', 'seguimiento_coord_comentarios.json')

def _seg_coord_leer():
    if not os.path.exists(_SEG_COORD_FILE):
        return {}
    with open(_SEG_COORD_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def _seg_coord_guardar(data):
    with open(_SEG_COORD_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

_SEG_COORD_CATS = ['ENTRENAMIENTOS','PARTIDOS','TECNICO','TACTICO','FISICO','EXTRADEPORTIVO']

def _seg_coord_equipo_init(data, equipo):
    if equipo not in data:
        data[equipo] = {}
    for cat in _SEG_COORD_CATS:
        if cat not in data[equipo]:
            data[equipo][cat] = []

def _abreviar_equipo(nombre):
    """BENJAMIN A → BEN A, INFANTIL C → INF C, ALEVIN FEMENINO → ALE FEM, etc."""
    n = (nombre or '').strip().upper()
    mapa = [
        ('PREBENJAMIN', 'PRE'), ('PREBENJAMÍN', 'PRE'),
        ('BENJAMIN', 'BEN'), ('BENJAMÍN', 'BEN'),
        ('ALEVIN', 'ALE'), ('ALEVÍN', 'ALE'),
        ('INFANTIL', 'INF'),
        ('CADETE', 'CDT'),
        ('JUVENIL', 'JUV'),
    ]
    for full, abbr in mapa:
        if n.startswith(full):
            resto = n[len(full):].strip().replace('FEMENINO', 'FEM').replace('FEMENINA', 'FEM')
            return (abbr + ' ' + resto).strip() if resto else abbr
    return n

def _buscar_clave_seg_coord(data, equipo):
    """Busca en data por nombre completo o abreviado, insensible a mayúsculas."""
    if equipo in data:
        return equipo
    abr = _abreviar_equipo(equipo)
    if abr in data:
        return abr
    eq_up, abr_up = equipo.upper(), abr.upper()
    for k in data:
        if k.upper() in (eq_up, abr_up):
            return k
    return equipo

@app.route('/api/seguimiento_coord/<equipo>')
def api_seguimiento_coord_get(equipo):
    if 'usuario' not in session:
        return jsonify({'error': 'no auth'}), 401
    data = _seg_coord_leer()
    clave = _buscar_clave_seg_coord(data, equipo)
    _seg_coord_equipo_init(data, clave)
    return jsonify({'categorias': data[clave]})

@app.route('/api/seguimiento_coord', methods=['POST'])
def api_seguimiento_coord_post():
    if 'usuario' not in session:
        return jsonify({'error': 'no auth'}), 401
    body = request.get_json() or {}
    equipo    = (body.get('equipo')    or '').strip()
    categoria = (body.get('categoria') or '').strip().upper()
    texto     = (body.get('texto')     or '').strip()
    if not equipo or not texto or categoria not in _SEG_COORD_CATS:
        return jsonify({'error': 'missing'}), 400
    data = _seg_coord_leer()
    clave = _buscar_clave_seg_coord(data, equipo)
    _seg_coord_equipo_init(data, clave)
    data[clave][categoria].append({'fecha': datetime.now().strftime('%d/%m/%Y'), 'texto': texto})
    _seg_coord_guardar(data)
    return jsonify({'categorias': data[clave]})

@app.route('/api/obs_comentario_ia', methods=['POST'])
def obs_comentario_ia():
    """Genera un comentario breve con Gemini sobre el rendimiento de un equipo en el seguimiento de temporada."""
    if not session.get('usuario'):
        return jsonify({"status": "error", "message": "No autenticado"}), 401
    try:
        import google.generativeai as genai
        with open('secretos.json', encoding='utf-8') as f:
            cfg = json.load(f)
        api_key = cfg.get('gemini_api_key') or cfg.get('GEMINI_API_KEY', '')
        if not api_key:
            return jsonify({"status": "error", "message": "No hay clave Gemini configurada"}), 500
        genai.configure(api_key=api_key)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error IA: {e}"}), 500

    body = request.get_json() or {}
    equipo = (body.get('equipo') or '').strip()
    jornadas = body.get('jornadas') or []
    pct = body.get('pct', 0)
    verde = body.get('verde', 0)
    rojo = body.get('rojo', 0)

    if not equipo or not jornadas:
        return jsonify({"status": "error", "message": "Datos insuficientes"}), 400

    # Calcular racha actual de rojos al final de la serie
    racha_actual = 0
    for j in reversed(jornadas):
        if j.get('cumplido') is False:
            racha_actual += 1
        elif j.get('cumplido') is True:
            break

    resumen_jornadas = "\n".join(
        f"- J{j['jornada']} vs {j.get('rival','?')}: {j.get('resultado','-')} | Obj: {j.get('objetivo','-')} | {'✅ Cumplido' if j.get('cumplido') else ('❌ No cumplido' if j.get('cumplido') is False else '⬜ Sin dato')}"
        for j in jornadas if j.get('rival')
    )

    prompt = f"""Eres el analista de rendimiento de la Escuela de Fútbol Club Fuentelarreyna.
Analiza los datos del siguiente equipo y genera un comentario breve (máximo 2 frases) en español sobre su rendimiento esta temporada.
Sé directo, específico y con tono constructivo. No repitas el nombre del equipo ni el porcentaje textualmente.

Equipo: {equipo}
Cumplimiento global: {pct}% ({verde} cumplidos, {rojo} no cumplidos)
Racha actual de objetivos no cumplidos: {racha_actual}
Últimas jornadas:
{resumen_jornadas}

Responde SOLO el comentario, sin introducción ni explicación."""

    try:
        # Seleccionar mejor modelo disponible dinámicamente
        try:
            modelos = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            modelo_nombre = next((m for m in modelos if 'gemini-2.0-flash' in m),
                           next((m for m in modelos if 'gemini-1.5-flash' in m),
                           next((m for m in modelos if 'flash' in m),
                           next((m for m in modelos if 'gemini' in m), 'models/gemini-2.0-flash'))))
        except Exception:
            modelo_nombre = 'models/gemini-2.0-flash'
        model = genai.GenerativeModel(modelo_nombre)
        response = model.generate_content(prompt)
        comentario = (response.text or '').strip()
        return jsonify({"status": "ok", "comentario": comentario})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# --- PUSH SCHEDULE CRUD ---
@app.route('/api/push_schedule', methods=['GET'])
def api_push_schedule_list():
    if not session.get('usuario'):
        return jsonify([]), 401
    import push_scheduler as _ps
    return jsonify(_ps.load_schedules())

@app.route('/api/push_schedule', methods=['POST'])
def api_push_schedule_create():
    if not session.get('usuario'):
        return jsonify({}), 401
    import push_scheduler as _ps
    data = request.json or {}
    s = {
        'id': str(uuid.uuid4()),
        'nombre': data.get('nombre', 'Sin nombre'),
        'tipo': data.get('tipo', 'asistencias'),
        'modo': data.get('modo', 'diario'),
        'dia_semana': data.get('dia_semana'),
        'dia_mes': data.get('dia_mes'),
        'hora': data.get('hora', '17:00'),
        'equipos': data.get('equipos', []),
        'texto': data.get('texto', ''),
        'link': data.get('link', 'https://intranet.clubfuentelarreyna.com/movil'),
        'activo': False,
        'ultima_ejecucion': None,
        'creado_en': datetime.now().isoformat(),
    }
    schedules = _ps.load_schedules()
    schedules.append(s)
    _ps.save_schedules(schedules)
    return jsonify(s)

@app.route('/api/push_schedule/<sid>', methods=['PUT'])
def api_push_schedule_update(sid):
    if not session.get('usuario'):
        return jsonify({}), 401
    import push_scheduler as _ps
    data = request.json or {}
    schedules = _ps.load_schedules()
    for s in schedules:
        if s['id'] == sid:
            for k in ['nombre','tipo','modo','dia_semana','dia_mes','hora','equipos','texto','link','activo']:
                if k in data:
                    s[k] = data[k]
            break
    _ps.save_schedules(schedules)
    return jsonify({'status': 'ok'})

@app.route('/api/push_schedule/<sid>', methods=['DELETE'])
def api_push_schedule_delete(sid):
    if not session.get('usuario'):
        return jsonify({}), 401
    import push_scheduler as _ps
    schedules = [s for s in _ps.load_schedules() if s['id'] != sid]
    _ps.save_schedules(schedules)
    return jsonify({'status': 'ok'})

# Arrancar scheduler en background
import push_scheduler as _push_scheduler_mod
_push_scheduler_mod.start(app)

if __name__ == '__main__':
    _debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=_debug, host='0.0.0.0', port=5001, use_reloader=_debug)