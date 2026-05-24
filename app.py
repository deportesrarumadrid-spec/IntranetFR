import os
import json
import base64
from datetime import datetime, timedelta
import calendar
import unicodedata
import gspread
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

# --- UTILIDADES DE NOTIFICACIÓN ---
def normalizar_id(texto):
    if not texto: return ""
    # Convierte a minúsculas, quita tildes y limpia espacios
    s = "".join(c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn')
    return s.lower().strip()

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
    mapping = {'L':0, 'M':1, 'X':2, 'J':3, 'V':4, 'S':5, 'D':6}
    res = []
    if not texto: return res
    for d in texto.upper().replace(' ', '').split(','):
        if d in mapping: res.append(mapping[d])
    return res

# --- CONFIGURACIÓN GOOGLE SHEETS ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("secretos.json", scope)
client = gspread.authorize(creds)
NOMBRE_EXCEL = "Control Asistencia Club" 
app.gs_client = client
app.gs_name = NOMBRE_EXCEL
# ----------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
DATA_FOLDER = os.path.join(BASE_DIR, 'static', 'data')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['DATA_FOLDER'] = DATA_FOLDER

def normalizar_cabecera_universal(h):
    """Limpia cabeceras de forma agresiva para comparaciones seguras."""
    return str(h).strip().upper().replace('Ó','O').replace('Í','I').replace('É','E').replace('Á','A').replace('Ú','U').replace(' ','').replace('_', '').replace('.', '')

for p in [UPLOAD_FOLDER, DATA_FOLDER]: os.makedirs(p, exist_ok=True)

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
                    'D.DEPORTIVA': check_p('D.DEPORTIVA')
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
    
    # 0. Cargar lista de equipos desde la pestaña "EQUIPO" para el desplegable y para determinar el equipo activo
    equipos = []
    try:
        sheet_eq = client.open(NOMBRE_EXCEL).worksheet("EQUIPO")
        rows_eq = sheet_eq.get_all_values()
        if rows_eq:
            # Normalizar cabeceras para una búsqueda robusta
            h_eq = [normalizar_cabecera_universal(h) for h in rows_eq[0]]
            i_eq = -1
            # Buscar la columna de equipo con varias posibles cabeceras
            for kw in ["EQUIPO", "NOMBRE", "EQUIPOS", "CATEGORIA", "GRUPO", "EQUIPOACTIVO"]:
                if kw in h_eq:
                    i_eq = h_eq.index(kw)
                    break
            
            # Determinar la fila de inicio de los datos
            s_row = 1 if i_eq != -1 else 0 # Si hay cabecera, empezar desde la segunda fila
            if i_eq == -1: i_eq = 0 # Si no se encontró una cabecera específica, asumir la primera columna
            
            # Extraer y ordenar equipos únicos
            equipos = sorted(list(set(str(r[i_eq]).strip() for r in rows_eq[s_row:] if len(r) > i_eq and str(r[i_eq]).strip())))
        else: equipos = []
    except Exception as e:
        print(f"Error al cargar equipos en asistencias: {e}")
        # Fallback si no existe la pestaña EQUIPO o hay error: equipos vacíos por ahora
        equipos = []

    # 1. Determinar equipo activo
    equipo_param = request.args.get('equipo')
    equipo_activo = equipo_param.strip() if equipo_param else session.get('equipo_defecto', '')
    if not equipo_activo and equipos: # Si no hay equipo activo y hay equipos disponibles, seleccionar el primero
        equipo_activo = equipos[0]
    session['equipo_defecto'] = equipo_activo # Guardar en sesión para persistencia

    # 1. Cargar Jugadores y mapear cualquier columna de equipo a la clave 'EQUIPO'
    sheet_jug = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
    all_values = sheet_jug.get_all_values()
    datos = []

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
                    # Normalización crítica: Si es la columna identificada como equipo, la llamamos EQUIPO
                    key = 'EQUIPO' if (i == idx_eq_jug and idx_eq_jug != -1) else h
                    registro[key] = str(val).strip()
                if 'EQUIPO' not in registro: registro['EQUIPO'] = ""
                datos.append(registro)

    # 2. Cargar lista de equipos desde la pestaña "EQUIPO"
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
        # Fallback si no existe la pestaña EQUIPO: sacarlos de JUGADORES
        equipos = sorted(list(set(row['EQUIPO'] for row in datos if row.get('EQUIPO'))))
    
    meses = ["Enero 2026", "Febrero 2026", "Marzo 2026", "Abril 2026", "Mayo 2026", "Junio 2026"]
    
    # También cargamos el Staff para saber quiénes son los entrenadores
    try:
        staff_sheet = client.open(NOMBRE_EXCEL).worksheet("STAFF")
        staff_all = staff_sheet.get_all_records()
        # Normalizamos las llaves por si acaso
        staff_datos = []
        for s in staff_all:
            staff_datos.append({
                "NOMBRE": s.get("NOMBRE", ""),
                "EQUIPO": s.get("EQUIPO", "").strip().upper(),
                "TELEFONO": str(s.get("TELEFONO", "")),
                "DIAS ENTRENAMIENTO": s.get("DIAS ENTRENAMIENTO", "") # Changed key to match Google Sheet header
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
                dia_extraido = str(int(partes_fecha[0])) 
                mes_extraido = str(int(partes_fecha[1]))
                
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
                sheet.append_row([fecha_full, equipo, nombre, "", estado, valoracion, observacion, charla])

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

# --- REGISTRO DE BLUEPRINTS ---
from financiero import financiero_bp
from deportivo import deportivo_bp
from jugadores_datos import jugadores_datos_bp
from perfiles import perfiles_bp
from ropa import ropa_bp
app.register_blueprint(financiero_bp)
app.register_blueprint(deportivo_bp)
app.register_blueprint(jugadores_datos_bp)
app.register_blueprint(perfiles_bp)
app.register_blueprint(ropa_bp)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001, use_reloader=True)