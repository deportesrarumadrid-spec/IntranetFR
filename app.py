import os
import json
import base64
from datetime import datetime, timedelta
import calendar
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, render_template, request, redirect, url_for, session, jsonify

app = Flask(__name__)

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

@app.route('/login', methods=['POST'])
def login():
    usuario_ingresado = request.form.get('usuario')
    return redirect(url_for('deportivo', usuario=usuario_ingresado))

@app.route('/deportivo')
def deportivo():
    usuario = request.args.get('usuario', 'admin')
    # Capturamos el mes actual (por defecto Mayo 2026 según tu captura)
    mes_actual = request.args.get('mes', '2026-05')
    
    # Fecha real del sistema
    hoy = datetime.now()
    
    # 1. Cargar objetivos
    obj_path = os.path.join(DATA_FOLDER, f'obj_{usuario}_{mes_actual}.json')
    objetivos = {"tactico": "TIRO, CENTRO", "tecnico": "", "completados": []}
    if os.path.exists(obj_path):
        with open(obj_path, 'r', encoding='utf-8') as f:
            objetivos = json.load(f)

    # 2. Lógica del Calendario Dinámico
    try:
        anio, mes = map(int, mes_actual.split('-'))
    except:
        anio, mes = hoy.year, hoy.month

    cal = calendar.Calendar(firstweekday=0)
    semanas_raw = cal.monthdayscalendar(anio, mes)
    
    semanas = []
    # Determinar si estamos viendo el mes y año actuales
    es_mes_actual = (anio == hoy.year and mes == hoy.month)
    
    # Lógica para resaltar fin de semana (Sábado=5, Domingo=6)
    wd_hoy = hoy.weekday()
    dias_a_resaltar = []
    if es_mes_actual:
        dias_a_resaltar.append(hoy.day)
        if wd_hoy == 5: # Si hoy es Sábado, resaltamos también el Domingo (mañana)
            dias_a_resaltar.append(hoy.day + 1)
        elif wd_hoy == 6: # Si hoy es Domingo, resaltamos también el Sábado (ayer)
            dias_a_resaltar.append(hoy.day - 1)

    for s in semanas_raw:
        semana_formateada = []
        # Revisamos si en esta semana cae algún día de los "dias_a_resaltar" (hoy/finde)
        es_fin_de_semana_hoy = any(d in dias_a_resaltar for d in s if d != 0) if es_mes_actual else False
        
        for d in s:
            if d == 0:
                semana_formateada.append(None)
            else:
                # El día individual solo se marca si es HOY exactamente
                es_hoy_exacto = (es_mes_actual and d == hoy.day)
                semana_formateada.append({
                    'numero': d, 
                    'es_hoy': es_hoy_exacto,
                    'resaltar_finde': es_fin_de_semana_hoy # Nueva marca para el bloque del finde
                })
        semanas.append(semana_formateada)

    # 3. Contar archivos y obtener lista de días con foto
    archivos_reales = [f for f in os.listdir(UPLOAD_FOLDER) if f.startswith(f"entreno_{usuario}_")]
    
    # Creamos la lista de números de día que ya tienen foto
    fotos_subidas = [f.split('_')[-1].split('.')[0] for f in archivos_reales]

    # 4. Return
    return render_template('deportivo/calendario.html',
                           usuario=usuario,
                           mes_actual=mes_actual,
                           objetivos=objetivos,
                           semanas=semanas,
                           archivos_subidos=len(archivos_reales),
                           fotos_subidas=fotos_subidas,
                           dias_transcurridos=hoy.day if es_mes_actual else (30 if (anio < hoy.year or (anio == hoy.year and mes < hoy.month)) else 0))
    
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
@app.route('/delete_foto', methods=['POST'])
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
@app.route('/update_objetivos', methods=['POST'])
def update_objetivos():
    data = request.json
    usuario = data.get('usuario')
    mes = data.get('mes')
    
    # Ruta del archivo donde guardamos los objetivos del mes
    obj_path = os.path.join(DATA_FOLDER, f'obj_{usuario}_{mes}.json')
    
    with open(obj_path, 'w', encoding='utf-8') as f:
        json.dump(data['objetivos'], f)
    
    return jsonify({"status": "success"})
import gspread
from oauth2client.service_account import ServiceAccountCredentials

@app.route('/asistencias')
def asistencias():
    usuario = session.get('usuario', 'Invitado')
    

    
    # Abre tu Excel y la pestaña de jugadores
    sheet = client.open("Control Asistencia Club").worksheet("JUGADORES")
    # Leemos todos los valores de la hoja
    all_values = sheet.get_all_values()
    
    if all_values:
        headers = all_values[0]
        # Creamos la lista de diccionarios ignorando columnas vacías a la derecha
        datos = []
        for row in all_values[1:]:
            if any(row):  # Solo procesa la fila si tiene algún dato
                registro = {headers[i]: row[i] for i in range(len(headers)) if headers[i].strip() != ""}
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
            if len(fila) >= 3 and fila[1] == equipo_filtro:
                # Extraemos el día de la fecha (ej: "04/05/2026" -> "4")
                partes_fecha = fila[0].split('/')
                dia_extraido = str(int(partes_fecha[0])) 
                
                registros.append({
                    "nombre": fila[2],
                    "dia": dia_extraido,
                    "estado": fila[4] if len(fila) > 4 else ""
                })
        
        return jsonify(registros)
    except Exception as e:
        print(f"Error al leer asistencias: {e}")
        return jsonify([])



@app.route('/obtener_observaciones_jugadores')
def obtener_observaciones_jugadores():
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        datos = sheet.get_all_records()
        # Esto crea un mapa de nombres y sus observaciones
        obs = {fila['NOMBRE']: fila.get('OBSERVACIONES', '') for fila in datos}
        return jsonify(obs)
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
        celda = sheet.find(nombre)
        # El 6 corresponde a la columna F (OBSERVACIONES) en la pestaña JUGADORES
        sheet.update_cell(celda.row, 6, texto)
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error al guardar observacion: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/guardar_asistencia_masiva', methods=['POST'])
def guardar_asistencia_masiva():
    try:
        data = request.get_json()
        cambios = data.get('cambios', [])
        # Extraemos el mes y aseguramos dos dígitos (ej: "05")
        mes_sel = str(data.get('mes', '5')).zfill(2)
        
        # IMPORTANTE: Usamos 'client' directamente, que ya lo tienes definido arriba
        sheet = client.open(NOMBRE_EXCEL).worksheet("ASISTENCIAS")
        
        actuales = sheet.get_all_values()
        
        for c in cambios:
            # Formato de fecha DD/MM/YYYY
            fecha_full = f"{str(c['dia']).zfill(2)}/{mes_sel}/2026"
            nombre = c['nombre']
            equipo = c['equipo']
            estado = c['estado']
            observacion = c.get('motivo', '') 

            # Buscar si ya existe para actualizar
            fila_idx = -1
            for i, fila in enumerate(actuales):
                if i == 0: continue
                if len(fila) >= 3 and fila[0] == fecha_full and fila[1] == equipo and fila[2] == nombre:
                    fila_idx = i + 1
                    break
            
            if fila_idx != -1:
                # Actualizar: Columna E (5) es Asistencia, Columna F (6) es Observaciones
                sheet.update_cell(fila_idx, 5, estado)
                sheet.update_cell(fila_idx, 6, observacion)
            else:
                # Crear nueva fila: FECHA, EQUIPO, NOMBRE, APELLIDO, ASISTENCIA, OBSERVACIONES
                sheet.append_row([fecha_full, equipo, nombre, "", estado, observacion])

        return jsonify({"status": "success"}), 200
    except Exception as e:
        print(f"Error crítico al guardar: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    except Exception as e:
        print(f"Error al guardar: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
if __name__ == '__main__':
    app.run(debug=True, port=5000)