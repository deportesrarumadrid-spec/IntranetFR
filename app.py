import os
import json
import base64
from datetime import datetime, timedelta
import calendar
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, render_template, request, redirect, url_for, session, jsonify

app = Flask(__name__)
app.secret_key = "club_intranet_secret_key_2024" # Necesario para las sesiones
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Límite de 16MB para subidas

# --- CONFIGURACIÓN GOOGLE SHEETS ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("secretos.json", scope)
client = gspread.authorize(creds)
NOMBRE_EXCEL = "Control Asistencia Club" 
# ----------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
DATA_FOLDER = os.path.join(BASE_DIR, 'static', 'data')

for p in [UPLOAD_FOLDER, DATA_FOLDER]: os.makedirs(p, exist_ok=True)

@app.route('/')
def index():
    return render_template('login.html')

@app.route('/save_all', methods=['POST'])
def save_all():
    data = request.json
    usuario = data.get('usuario')
    mes = data.get('mes')
    obj_path = os.path.join(DATA_FOLDER, f'obj_{usuario}_{mes}.json')
    with open(obj_path, 'w', encoding='utf-8') as f:
        json.dump(data['objetivos'], f)
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
    usuario_ingresado = request.form.get('usuario')
    session['usuario'] = usuario_ingresado # Guardamos el usuario en la sesión
    return redirect(url_for('deportivo_bp.direccion_deportiva'))

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
    usuario = session.get('usuario', 'Invitado')
    

    
    # Abre tu Excel y la pestaña de jugadores
    sheet = client.open("Control Asistencia Club").worksheet("JUGADORES")
    # Leemos todos los valores de la hoja
    all_values = sheet.get_all_values()
    
    if all_values:
        headers = [h.strip().upper() for h in all_values[0]]
        datos = []
        for row in all_values[1:]:
            if any(row):
                registro = {}
                for i, h in enumerate(headers):
                    val = row[i] if i < len(row) else ""
                    if h == "NOMBRE": val = val.strip()
                    registro[h] = val
                datos.append(registro)
    else:
        datos = []
    
    equipos = sorted(list(set(row['EQUIPO'] for row in datos)))
    meses = ["Enero 2026", "Febrero 2026", "Marzo 2026", "Abril 2026", "Mayo 2026", "Junio 2026"]
    
    return render_template('asistencias/lista.html', 
                           usuario=usuario, 
                           equipos=equipos, 
                           meses=meses, 
                           jugadores_raw=datos)

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

# --- REGISTRO DE BLUEPRINTS ---
from financiero import financiero_bp
from deportivo import deportivo_bp
app.register_blueprint(financiero_bp)
app.register_blueprint(deportivo_bp)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001, use_reloader=False)