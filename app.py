import os
import json
import base64
from datetime import datetime, timedelta
import calendar
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

from oauth2client.service_account import ServiceAccountCredentials
import requests # ¡IMPORTANTE! Añadir esta línea
try:
    from dotenv import load_dotenv
    load_dotenv() # Carga las variables desde un archivo .env si existe
except ImportError:
    pass
from flask import Flask, render_template, request, redirect, url_for, session, jsonify

from perfiles import get_perfiles_sheet # Importamos la función para obtener la hoja de perfiles

app = Flask(__name__)
app.secret_key = "club_intranet_secret_key_2024" # Necesario para las sesiones
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Límite de 16MB para subidas

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
        "Authorization": f"Basic {rest_api_key}"
    }
    
    payload = {
        "app_id": app_id,
        "contents": {"es": mensaje, "en": mensaje},
        "headings": {"es": "Intranet Club", "en": "Intranet Club"},
        "include_external_user_ids": [normalizar_id(usuario)], # Usamos el ID normalizado
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        res_json = response.json()
        # OneSignal devuelve 'id' si tuvo éxito. Verificamos si hubo destinatarios
        recipients = res_json.get('recipients', 0)
        print(f">>> RESULTADO PUSH ({usuario}): {recipients} dispositivos alcanzados. ID: {res_json.get('id')}")
        if recipients == 0:
            print(f"⚠️ AVISO: El entrenador '{usuario}' (ID: {normalizar_id(usuario)}) aún no ha aceptado notificaciones en su móvil.")
    except requests.exceptions.RequestException as e:
        print(f"Error al enviar Push a {usuario}: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"OneSignal API Response: {e.response.text}")

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
DATA_FOLDER = os.path.join(BASE_DIR, 'static', 'data')

# --- CONFIGURACIÓN GOOGLE SHEETS ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(os.path.join(BASE_DIR, "secretos.json"), scope)
client = gspread.authorize(creds)
NOMBRE_EXCEL = "Control Asistencia Club"
app.gs_client = client
app.gs_name = NOMBRE_EXCEL
# ----------------------------------

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['DATA_FOLDER'] = DATA_FOLDER

def normalizar_cabecera_universal(h):
    """Limpia cabeceras de forma agresiva para comparaciones seguras."""
    s = "".join(c for c in unicodedata.normalize('NFD', str(h).upper().strip()) if unicodedata.category(c) != 'Mn')
    return s.replace(' ', '').replace('_', '').replace('.', '').replace('/', '')

for p in [UPLOAD_FOLDER, DATA_FOLDER]: os.makedirs(p, exist_ok=True)

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
        dia_num = str(int(fecha.split('/')[0]))
        filename_evidencia = f"entreno_{usuario}_{dia_num}.jpg"
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
            # Si hay filtro de equipo, saltamos las carpetas que no coincidan
            if equipo_req and eq_folder.upper() != equipo_req.upper():
                continue
            
            full_path = os.path.join(path_base, eq_folder)
            if not os.path.isdir(full_path): continue
            
            for f in os.listdir(full_path):
                if f.endswith('.json'):
                    try:
                        with open(os.path.join(full_path, f), 'r', encoding='utf-8') as file:
                            meta = json.load(file)
                            if usuario != 'admin' and meta.get('usuario') != usuario:
                                continue
                            meta['img_url'] = f"/static/data/sesiones/{eq_folder}/{f.replace('.json', '.jpg')}"
                            meta['tipo'] = 'ONLINE'
                            sesiones.append(meta)
                    except: continue

    # 2. Escanear Sesiones Subidas (Evidencias directas del calendario)
    if os.path.exists(UPLOAD_FOLDER):
        for f in os.listdir(UPLOAD_FOLDER):
            if f.startswith(f"entreno_{usuario}_") and f.endswith('.jpg'):
                try:
                    file_path = os.path.join(UPLOAD_FOLDER, f)
                    # Obtener la fecha de modificación del archivo
                    timestamp = os.path.getmtime(file_path)
                    fecha_dt = datetime.fromtimestamp(timestamp)
                    fecha_str = fecha_dt.strftime("%d/%m/%Y")
                except:
                    dia = f.split('_')[-1].split('.')[0]
                    fecha_str = f"{dia.zfill(2)}/05/2026" # Fallback

                sesiones.append({
                    "fecha": fecha_str,
                    "equipo": "Archivo Adjunto",
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
        
        # Redirección inteligente al módulo correspondiente tras elegir equipo
        if perms.get('D.DEPORTIVA') == 'SI': target = url_for('deportivo_bp.direccion_deportiva')
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
        
        equipos = sorted(list(set(str(row[actual_idx]).strip() for row in all_v[start_row:] if len(row) > actual_idx and str(row[actual_idx]).strip())))
    except Exception as e:
        print(f"Error recuperando equipos para selección: {e}")
        equipos = []

    return render_template('seleccionar_equipo.html', equipos=equipos, usuario=session.get('usuario'))

@app.route('/logout')
def logout():
    """Limpia la sesión por completo y redirige al login."""
    session.clear()
    return redirect(url_for('index'))

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

@app.route('/upload_foto', methods=['POST'])
def upload_foto():
    data = request.json
    usuario = data.get('usuario')
    dia = data.get('dia')
    try:
        img_data = base64.b64decode(data.get('image').split(",")[1])
        filename = f"entreno_{usuario}_{dia}.jpg"
        with open(os.path.join(UPLOAD_FOLDER, filename), "wb") as f:
            f.write(img_data)
        return jsonify({"status": "success"})
    except:
        return jsonify({"status": "error"}), 500
@app.route('/login', methods=['POST'])
def login():
    usuario_ingresado = (request.form.get('usuario') or "").strip()
    password_ingresado = (request.form.get('password') or "").strip()

    # 1. Caso especial para admin (Master) - NO SE TOCA, acceso total garantizado
    if usuario_ingresado.lower() == 'admin':
        session['usuario'] = 'admin'
        session['permisos'] = {
            'ENTRENAMIENTOS': 'SI', 'ASISTENCIAS': 'SI', 'FINANCIERO': 'SI', 'D.DEPORTIVA': 'SI', 'USUARIOS': 'SI'
            , 'CRONOGRAMA': 'SI' # Admin master always has Cronograma access
        }
        return redirect(url_for('deportivo_bp.direccion_deportiva'))

    # 2. Otros usuarios - Consultar la pestaña PERFILES
    try:
        sheet = get_perfiles_sheet()
        records = sheet.get_all_records()
        
        # Búsqueda ultra-precisa: normalizamos el target y los registros
        user_data = None
        target_upper = usuario_ingresado.upper()
        
        for r in records:
            # Normalización de llaves del diccionario de la fila
            r_norm = {normalizar_cabecera_universal(k): v for k, v in r.items()}
            if str(r_norm.get('USUARIO', '')).strip().upper() == target_upper:
                user_data = r_norm
                break

        if user_data:
            # Validación de contraseña (sensible a mayúsculas/minúsculas pero ignora espacios laterales)
            if str(user_data.get('CONTRASEÑA', '')).strip() == password_ingresado:
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
                    'CRONOGRAMA': check_p('CRONOGRAMA'),
                    'SEL. EQ.': 'SI' if str(user_data.get('SELEQ', user_data.get('SELEQ.', 'NO'))).strip().upper() == 'SI' else 'NO'
                }
                session['permisos'] = perms
                
                # 3. ¿Debe elegir equipo? Buscamos SELEQ (normalizado de SEL. EQ.)
                debe_elegir = user_data.get('SELEQ') or user_data.get('ELEGIRIQUIPO') or user_data.get('SELEQ.') or 'NO'
                if str(debe_elegir).strip().upper() == 'SI':
                    session['equipo_defecto'] = '' # Limpiamos selección previa para forzar la nueva
                    return redirect(url_for('seleccionar_equipo'))
                
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
    equipo = request.args.get('equipo')
    mes = request.args.get('mes')
    path = os.path.join(DATA_FOLDER, f'balones_{equipo}_{mes}.json')
    
    if request.method == 'GET':
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f))
        return jsonify({})
    
    data = request.json
    dia = str(data.get('dia'))
    tipo = data.get('tipo')  # 'inicio' o 'final'
    cantidad = data.get('cantidad')
    foto = data.get('foto')  # base64
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M")
    
    current_data = {}
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            current_data = json.load(f)
    
    if dia not in current_data:
        current_data[dia] = {}

    # Si el dato existente no es una lista (datos antiguos), lo convertimos
    if tipo in current_data[dia] and not isinstance(current_data[dia][tipo], list):
        current_data[dia][tipo] = [current_data[dia][tipo]]
    
    if tipo not in current_data[dia]:
        current_data[dia][tipo] = []

    nuevo_registro = {
        "cantidad": cantidad,
        "timestamp": timestamp
    }
    
    if foto and "," in foto:
        # Nombre de archivo único usando el índice de la lista
        foto_filename = f"balones_{equipo}_{mes}_{dia}_{tipo}_{len(current_data[dia][tipo])}.jpg"
        foto_path = os.path.join(UPLOAD_FOLDER, foto_filename)
        img_data = base64.b64decode(foto.split(",")[1])
        with open(foto_path, "wb") as f:
            f.write(img_data)
        nuevo_registro["foto_url"] = f"/static/uploads/{foto_filename}"

    current_data[dia][tipo].append(nuevo_registro)

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(current_data, f)
    return jsonify({"status": "success"})

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
                    "estado": fila[4] if len(fila) > 4 else "",
                    "valoracion": fila[5] if len(fila) > 5 else "-",
                    "motivo": fila[6] if len(fila) > 6 else "",
                    "charla": fila[7] if len(fila) > 7 else "NO"
                })
        
        return jsonify(registros)
    except Exception as e:
        print(f"Error al leer asistencias: {e}")
        return jsonify([])



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

# --- REGISTRO DE BLUEPRINTS ---
from financiero import financiero_bp
from deportivo import deportivo_bp
from jugadores_datos import jugadores_datos_bp
from perfiles import perfiles_bp
from jugadores_detalles import jugadores_detalles_bp # Importar el nuevo blueprint
from ropa import ropa_bp
from mi_equipo import mi_equipo_bp
from formulario_partido import formulario_partido_bp # Importar el nuevo blueprint
from inscripcion import inscripcion_bp # Importar el blueprint de inscripción
from asistente_ia import asistente_bp # Importar el blueprint del asistente IA
app.register_blueprint(financiero_bp)
app.register_blueprint(deportivo_bp)
app.register_blueprint(jugadores_datos_bp)
app.register_blueprint(perfiles_bp)
app.register_blueprint(ropa_bp)
app.register_blueprint(jugadores_detalles_bp) # Registrar el nuevo blueprint
app.register_blueprint(mi_equipo_bp)
app.register_blueprint(formulario_partido_bp)
app.register_blueprint(inscripcion_bp) # Registrar el blueprint de inscripción
app.register_blueprint(asistente_bp) # Registrar el blueprint del asistente IA

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001, use_reloader=True)