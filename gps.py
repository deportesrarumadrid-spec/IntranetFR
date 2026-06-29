import os
import re
import csv
import json
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, session, redirect, current_app
from werkzeug.utils import secure_filename

gps_bp = Blueprint('gps_bp', __name__)

HEADERS_GPS = ['EQUIPO', 'FECHA', 'ARCHIVO_PDF', 'ARCHIVO_CSV', 'FECHA_SUBIDA']

CORRECCION_NOMBRES_METRICA = {
    'Fecha de sesi?n': 'Fecha de sesión',
    'M?xima Velocidad': 'Máxima Velocidad',
    'Distancia de Alta Carga Metab?lica': 'Distancia de Alta Carga Metabólica',
    'Distancia de Alta Carga Metab?lica por minuto': 'Distancia de Alta Carga Metabólica por Minuto',
    'Carga De Esfuerzo Din?mico': 'Carga De Esfuerzo Dinámico',
}


def parsear_csv_gps(ruta_csv):
    """Lee un CSV exportado de Sonra (separado por ';', normalmente en cp1252)
    y devuelve columnas de métricas, valores por jugador, suma y promedio del equipo."""
    contenido = None
    for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            with open(ruta_csv, encoding=enc) as f:
                contenido = f.read()
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if contenido is None:
        with open(ruta_csv, encoding='latin-1', errors='replace') as f:
            contenido = f.read()

    lineas = [l for l in contenido.splitlines() if l.strip()]
    filas = list(csv.reader(lineas, delimiter=';'))
    if not filas:
        return {"columnas": [], "jugadores": [], "promedio": {}, "suma": {}}

    cabeceras = [h.strip() for h in filas[0] if h.strip()]
    cabeceras = [CORRECCION_NOMBRES_METRICA.get(h, h) for h in cabeceras]
    metricas = cabeceras[2:]  # columna 0: fecha de sesión, columna 1: nombre del jugador

    jugadores = []
    for fila in filas[1:]:
        fila = [c.strip().strip('"') for c in fila]
        if not fila or not fila[0]:
            continue
        nombre = fila[1] if len(fila) > 1 else ''
        valores = {}
        for idx, metrica in enumerate(metricas, start=2):
            if idx < len(fila) and fila[idx] != '':
                try:
                    valores[metrica] = float(fila[idx])
                except ValueError:
                    valores[metrica] = None
            else:
                valores[metrica] = None
        if nombre:
            jugadores.append({"nombre": nombre, "valores": valores})

    promedio, suma = {}, {}
    for m in metricas:
        vals = [j["valores"].get(m) for j in jugadores if j["valores"].get(m) is not None]
        if vals:
            promedio[m] = round(sum(vals) / len(vals), 2)
            suma[m] = round(sum(vals), 2)
        else:
            promedio[m] = None
            suma[m] = None

    return {"columnas": metricas, "jugadores": jugadores, "promedio": promedio, "suma": suma}


def sanitizar_equipo_gps(equipo):
    s = re.sub(r'[^A-Za-z0-9]+', '_', (equipo or '').strip().upper())
    return s.strip('_') or 'SINEQUIPO'


def _client_name():
    return current_app.gs_client, current_app.gs_name


def obtener_equipos_gps():
    client, name = _client_name()
    equipos = []
    try:
        sheet = client.open(name).worksheet("EQUIPO")
        all_v = sheet.get_all_values()
        if all_v:
            headers = [str(h).strip().upper() for h in all_v[0]]
            if "EQUIPO" in headers:
                idx_e = headers.index("EQUIPO")
                equipos = sorted(set(str(r[idx_e]).strip() for r in all_v[1:] if len(r) > idx_e and r[idx_e].strip()))
    except Exception as e:
        print(f"Error cargando equipos en GPS: {e}")
    return equipos


def get_gps_sheet():
    client, name = _client_name()
    try:
        return client.open(name).worksheet("GPS")
    except Exception:
        sheet = client.open(name).add_worksheet(title="GPS", rows="500", cols="6")
        sheet.append_row(HEADERS_GPS)
        return sheet


def get_gps_posiciones_sheet():
    client, name = _client_name()
    try:
        return client.open(name).worksheet("GPS_POSICIONES")
    except Exception:
        sheet = client.open(name).add_worksheet(title="GPS_POSICIONES", rows="1000", cols="4")
        sheet.append_row(["EQUIPO", "JUGADOR", "POSICION", "EDAD"])
        return sheet


def obtener_info_jugadores_equipo(equipo):
    """Devuelve {jugador: {"posicion": ..., "edad": ...}} para un equipo."""
    sheet = get_gps_posiciones_sheet()
    all_v = sheet.get_all_values()
    info = {}
    for row in all_v[1:]:
        if len(row) > 1 and row[0].strip().upper() == equipo.upper():
            info[row[1].strip()] = {
                "posicion": row[2].strip() if len(row) > 2 else '',
                "edad": row[3].strip() if len(row) > 3 else '',
            }
    return info


def guardar_info_jugador(equipo, jugador, posicion=None, edad=None):
    """Actualiza posición y/o edad de un jugador, sin pisar el campo que no se envíe."""
    sheet = get_gps_posiciones_sheet()
    all_v = sheet.get_all_values()
    for i, row in enumerate(all_v[1:], start=2):
        if len(row) > 1 and row[0].strip().upper() == equipo.upper() and row[1].strip() == jugador:
            pos_final = posicion if posicion is not None else (row[2].strip() if len(row) > 2 else '')
            edad_final = edad if edad is not None else (row[3].strip() if len(row) > 3 else '')
            sheet.update(f'A{i}:D{i}', [[equipo, jugador, pos_final, edad_final]], value_input_option='USER_ENTERED')
            return
    sheet.append_row([equipo, jugador, posicion or '', edad or ''])


def get_gps_upload_folder():
    base = current_app.config.get('UPLOAD_FOLDER', os.path.join(os.getcwd(), 'static', 'uploads'))
    return os.path.join(base, 'gps')


def get_gps_pendientes_folder():
    base = current_app.config.get('UPLOAD_FOLDER', os.path.join(os.getcwd(), 'static', 'uploads'))
    carpeta = os.path.join(base, 'gps_pendientes')
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def get_gps_comentarios_path(clave_equipo, fecha):
    return os.path.join(get_gps_upload_folder(), clave_equipo, fecha, 'comentarios.json')


def get_gps_benchmarks_sheet():
    client, name = _client_name()
    try:
        return client.open(name).worksheet("GPS_BENCHMARKS")
    except Exception:
        sheet = client.open(name).add_worksheet(title="GPS_BENCHMARKS", rows="500", cols="5")
        sheet.append_row(["POSICION", "EDAD", "METRICAS_JSON", "ORIGEN", "ACTUALIZADO"])
        return sheet


def obtener_benchmark(posicion, edad):
    sheet = get_gps_benchmarks_sheet()
    all_v = sheet.get_all_values()
    for row in all_v[1:]:
        if len(row) > 2 and row[0].strip().upper() == posicion.upper() and row[1].strip() == str(edad):
            try:
                metricas = json.loads(row[2]) if row[2] else {}
            except json.JSONDecodeError:
                metricas = {}
            return {"metricas": metricas, "origen": row[3].strip() if len(row) > 3 else ''}
    return None


def guardar_benchmark(posicion, edad, metricas, origen):
    sheet = get_gps_benchmarks_sheet()
    all_v = sheet.get_all_values()
    ahora = datetime.now().strftime("%d/%m/%Y %H:%M")
    metricas_json = json.dumps(metricas, ensure_ascii=False)
    for i, row in enumerate(all_v[1:], start=2):
        if len(row) > 1 and row[0].strip().upper() == posicion.upper() and row[1].strip() == str(edad):
            sheet.update(f'A{i}:E{i}', [[posicion, str(edad), metricas_json, origen, ahora]], value_input_option='USER_ENTERED')
            return
    sheet.append_row([posicion, str(edad), metricas_json, origen, ahora])


def generar_benchmark_ia(posicion, edad, metricas_nombres):
    """Pide a la IA valores orientativos (NO datos validados) de referencia para un jugador
    de esa edad y posición, para cada métrica indicada."""
    model = _modelo_gemini()
    lista_metricas = ", ".join(metricas_nombres)
    prompt = (
        f"Eres un preparador físico de fútbol base. Da una ESTIMACIÓN orientativa (basada en literatura general "
        f"de preparación física, no en datos validados específicos) de los valores típicos esperables en una sesión "
        f"de entrenamiento para un jugador de fútbol de {edad} años en la posición de {posicion}, para cada una de "
        f"estas métricas GPS: {lista_metricas}.\n"
        "Devuelve SOLO un JSON válido con este formato exacto, sin texto adicional ni markdown, con un número por métrica:\n"
        '{"NombreMetrica1": numero, "NombreMetrica2": numero, ...}'
    )
    resp = model.generate_content(prompt)
    texto = (resp.text or '').strip()
    if texto.startswith('```'):
        texto = texto.strip('`')
        if texto.lower().startswith('json'):
            texto = texto[4:]
    return json.loads(texto)


def registrar_sesion_gps(equipo, fecha, nombre_pdf='', nombre_csv=''):
    """Crea o actualiza la fila EQUIPO+FECHA en la hoja GPS, sin pisar el archivo
    del otro tipo (pdf/csv) si ya estaba registrado."""
    sheet = get_gps_sheet()
    all_v = sheet.get_all_values()
    fila_idx = -1
    for i, row in enumerate(all_v[1:], start=2):
        if len(row) > 1 and row[0].strip().upper() == equipo.upper() and row[1].strip() == fecha:
            fila_idx = i
            break

    ahora = datetime.now().strftime("%d/%m/%Y %H:%M")
    if fila_idx != -1:
        fila_actual = all_v[fila_idx - 1]
        pdf_final = nombre_pdf or (fila_actual[2].strip() if len(fila_actual) > 2 else '')
        csv_final = nombre_csv or (fila_actual[3].strip() if len(fila_actual) > 3 else '')
        sheet.update(f'A{fila_idx}:E{fila_idx}', [[equipo, fecha, pdf_final, csv_final, ahora]], value_input_option='USER_ENTERED')
    else:
        sheet.append_row([equipo, fecha, nombre_pdf, nombre_csv, ahora])


def _localizar_csv_sesion(equipo, fecha):
    """Devuelve la ruta absoluta del CSV registrado para equipo+fecha, o '' si no hay."""
    sheet = get_gps_sheet()
    all_v = sheet.get_all_values()
    nombre_csv = ''
    for row in all_v[1:]:
        if len(row) > 3 and row[0].strip().upper() == equipo.upper() and row[1].strip() == fecha:
            nombre_csv = row[3].strip()
            break
    if not nombre_csv:
        return ''
    clave_equipo = sanitizar_equipo_gps(equipo)
    ruta = os.path.join(get_gps_upload_folder(), clave_equipo, fecha, nombre_csv)
    return ruta if os.path.isfile(ruta) else ''


UMBRAL_OUTLIER_RELATIVO = 0.2  # un valor por debajo del 20% de la media de esa sesión se considera desviación extrema (posible lesión / no jugó realmente)


def cargar_sesiones_equipo_con_csv(equipo):
    """Devuelve [{'fecha':..., 'datos': parsear_csv_gps(...)}] para todas las sesiones con CSV de un equipo."""
    sheet = get_gps_sheet()
    all_v = sheet.get_all_values()
    clave_equipo = sanitizar_equipo_gps(equipo)
    sesiones = []
    for row in all_v[1:]:
        if len(row) > 3 and row[0].strip().upper() == equipo.upper() and row[3].strip():
            fecha = row[1].strip()
            ruta = os.path.join(get_gps_upload_folder(), clave_equipo, fecha, row[3].strip())
            if os.path.isfile(ruta):
                try:
                    sesiones.append({"fecha": fecha, "datos": parsear_csv_gps(ruta)})
                except Exception as e:
                    print(f"Error parseando CSV de sesión {fecha} para temporada: {e}")
    return sesiones


def _filtrar_outliers_sesion(datos_sesion, metrica):
    """Devuelve (valores_validos, jugadores_excluidos) de una métrica en una sesión, excluyendo
    a quien tenga un valor anormalmente bajo respecto a la media de esa sesión (posible lesión/no jugó realmente)."""
    valores = [(j['nombre'], j['valores'].get(metrica)) for j in datos_sesion['jugadores'] if j['valores'].get(metrica) is not None]
    if not valores:
        return [], []
    media_sesion = sum(v for _, v in valores) / len(valores)
    umbral = media_sesion * UMBRAL_OUTLIER_RELATIVO
    validos = [(n, v) for n, v in valores if v >= umbral]
    excluidos = [n for n, v in valores if v < umbral]
    return validos, excluidos


def calcular_temporada(equipo, fecha_sesion_actual):
    """Compara la sesión indicada contra el conjunto de todas las sesiones con CSV del equipo
    (acumulado de equipo y media por jugador), descartando desviaciones extremas por sesión."""
    sesiones = cargar_sesiones_equipo_con_csv(equipo)
    if not sesiones:
        return None

    todas_metricas = []
    for s in sesiones:
        for m in s['datos']['columnas']:
            if m not in todas_metricas:
                todas_metricas.append(m)

    metricas_resultado = {}
    for metrica in todas_metricas:
        sumas_por_sesion = []
        valores_jugador_pool = []
        datos_equipo_sesion_actual = None
        datos_jugador_sesion_actual = None
        excluidos_sesion_actual = []

        for s in sesiones:
            if metrica not in s['datos']['columnas']:
                continue
            suma_sesion = s['datos']['suma'].get(metrica)
            if suma_sesion is not None:
                sumas_por_sesion.append(suma_sesion)

            validos, excluidos = _filtrar_outliers_sesion(s['datos'], metrica)
            valores_jugador_pool.extend(v for _, v in validos)

            if s['fecha'] == fecha_sesion_actual:
                datos_equipo_sesion_actual = suma_sesion
                excluidos_sesion_actual = excluidos
                if validos:
                    datos_jugador_sesion_actual = sum(v for _, v in validos) / len(validos)

        media_equipo_temporada = (sum(sumas_por_sesion) / len(sumas_por_sesion)) if sumas_por_sesion else None
        media_jugador_temporada = (sum(valores_jugador_pool) / len(valores_jugador_pool)) if valores_jugador_pool else None

        def _dif_pct(actual, media):
            if actual is None or media is None:
                return None, None
            diferencia = actual - media
            pct = (diferencia / media * 100) if media else None
            return diferencia, pct

        dif_equipo, pct_equipo = _dif_pct(datos_equipo_sesion_actual, media_equipo_temporada)
        dif_jugador, pct_jugador = _dif_pct(datos_jugador_sesion_actual, media_jugador_temporada)

        metricas_resultado[metrica] = {
            "equipo_sesion": round(datos_equipo_sesion_actual, 2) if datos_equipo_sesion_actual is not None else None,
            "equipo_media_temporada": round(media_equipo_temporada, 2) if media_equipo_temporada is not None else None,
            "equipo_diferencia": round(dif_equipo, 2) if dif_equipo is not None else None,
            "equipo_pct": round(pct_equipo, 1) if pct_equipo is not None else None,
            "jugador_sesion": round(datos_jugador_sesion_actual, 2) if datos_jugador_sesion_actual is not None else None,
            "jugador_media_temporada": round(media_jugador_temporada, 2) if media_jugador_temporada is not None else None,
            "jugador_diferencia": round(dif_jugador, 2) if dif_jugador is not None else None,
            "jugador_pct": round(pct_jugador, 1) if pct_jugador is not None else None,
            "excluidos_sesion_actual": excluidos_sesion_actual,
        }

    return {"metricas": metricas_resultado, "num_sesiones": len(sesiones)}


def _modelo_gemini():
    import google.generativeai as genai

    with open('secretos.json', encoding='utf-8') as f:
        cfg = json.load(f)
    api_key = cfg.get('gemini_api_key')
    if not api_key:
        raise RuntimeError("No se encontró gemini_api_key en secretos.json")
    genai.configure(api_key=api_key)

    modelos = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    nombre_modelo = next((m for m in modelos if 'gemini-2.5-flash' in m),
                     next((m for m in modelos if 'gemini-1.5-flash' in m),
                     next((m for m in modelos if 'flash' in m), modelos[0] if modelos else 'models/gemini-1.5-flash')))
    return genai.GenerativeModel(model_name=nombre_modelo)


def _instrucciones_comentario_pf():
    return (
        "Eres un preparador físico profesional de fútbol analizando datos GPS de una sesión de entrenamiento. "
        "Enfoca el comentario en preparación física: carga de entrenamiento, intensidad, riesgo de fatiga o "
        "sobrecarga neuromuscular, simetría/asimetría de carrera, y necesidad de recuperación. "
        "Compara siempre con la media del equipo y, si hay posición y/o edad indicadas, ten en cuenta el perfil "
        "físico habitual esperado para esa posición y esa edad."
    )


def generar_comentarios_ia(datos, info_jugadores):
    """Llama a Gemini para generar un comentario profesional por jugador a partir de sus stats GPS."""
    model = _modelo_gemini()

    promedio = datos['promedio']
    media_txt = ', '.join(f"{k}: {v}" for k, v in promedio.items() if v is not None)

    lineas_jugadores = []
    for j in datos['jugadores']:
        info = info_jugadores.get(j['nombre'], {})
        pos, edad = info.get('posicion', ''), info.get('edad', '')
        valores_txt = ', '.join(f"{k}: {v}" for k, v in j['valores'].items() if v is not None)
        etiqueta = ''
        if pos: etiqueta += f" (Posición: {pos})"
        if edad: etiqueta += f" (Edad: {edad})"
        lineas_jugadores.append(f"- {j['nombre']}{etiqueta}: {valores_txt}")

    prompt = (
        f"{_instrucciones_comentario_pf()}\n"
        f"Media del equipo en esta sesión: {media_txt}\n\n"
        "Datos de cada jugador:\n" + "\n".join(lineas_jugadores) + "\n\n"
        "Para cada jugador, escribe un comentario profesional breve (2-3 frases, en español) sobre su rendimiento "
        "físico en esta sesión, comparándolo con la media del equipo, destacando fortalezas o aspectos a vigilar "
        "(ej. baja distancia total, alta carga de aceleraciones/deceleraciones, pocos sprints, etc.). "
        "Devuelve SOLO un JSON válido con este formato exacto, sin texto adicional ni markdown:\n"
        '{"NOMBREJUGADOR": "comentario...", ...}'
    )

    resp = model.generate_content(prompt)
    texto = (resp.text or '').strip()
    if texto.startswith('```'):
        texto = texto.strip('`')
        if texto.lower().startswith('json'):
            texto = texto[4:]
    return json.loads(texto)


def generar_comentario_jugador_ia(jugador_dict, info_jugador, promedio):
    """Genera el comentario de UN solo jugador (más rápido que regenerar todo el equipo)."""
    model = _modelo_gemini()

    media_txt = ', '.join(f"{k}: {v}" for k, v in promedio.items() if v is not None)
    valores_txt = ', '.join(f"{k}: {v}" for k, v in jugador_dict['valores'].items() if v is not None)
    pos, edad = info_jugador.get('posicion', ''), info_jugador.get('edad', '')
    etiqueta = ''
    if pos: etiqueta += f" (Posición: {pos})"
    if edad: etiqueta += f" (Edad: {edad})"

    prompt = (
        f"{_instrucciones_comentario_pf()}\n"
        f"Media del equipo en esta sesión: {media_txt}\n\n"
        f"Jugador: {jugador_dict['nombre']}{etiqueta}\n"
        f"Sus datos en esta sesión: {valores_txt}\n\n"
        "Escribe un comentario profesional breve (2-3 frases, en español) sobre su rendimiento físico en esta "
        "sesión, comparándolo con la media del equipo. Devuelve SOLO el texto del comentario, sin comillas, "
        "sin JSON ni markdown."
    )
    resp = model.generate_content(prompt)
    return (resp.text or '').strip().strip('"')


@gps_bp.route('/gps')
def gps():
    usuario = session.get('usuario')
    if not usuario:
        return redirect('/')
    perms = session.get('permisos', {})
    if perms.get('D.DEPORTIVA') != 'SI' and usuario.lower() != 'admin':
        return "Acceso denegado", 403

    equipos = obtener_equipos_gps()
    return render_template('gps.html', usuario=usuario, equipos=equipos)


@gps_bp.route('/api/gps', methods=['GET'])
def api_gps_lista():
    """Devuelve todas las sesiones GPS registradas (opcionalmente filtradas por equipo)."""
    equipo_filtro = (request.args.get('equipo') or '').strip()
    sheet = get_gps_sheet()
    all_v = sheet.get_all_values()
    sesiones = []
    if all_v:
        for row in all_v[1:]:
            if len(row) < 2 or not row[0].strip():
                continue
            if equipo_filtro and row[0].strip().upper() != equipo_filtro.upper():
                continue
            sesiones.append({
                "equipo": row[0].strip(),
                "fecha": row[1].strip(),
                "pdf": row[2].strip() if len(row) > 2 else "",
                "csv": row[3].strip() if len(row) > 3 else "",
            })
    return jsonify({"sesiones": sesiones})


@gps_bp.route('/api/gps/upload', methods=['POST'])
def api_gps_upload():
    usuario = session.get('usuario')
    if not usuario:
        return jsonify({"status": "error", "message": "No autenticado."}), 401

    equipo = (request.form.get('equipo') or '').strip()
    fecha = (request.form.get('fecha') or '').strip()  # YYYY-MM-DD
    if not equipo or not fecha:
        return jsonify({"status": "error", "message": "Falta equipo o fecha."}), 400

    clave_equipo = sanitizar_equipo_gps(equipo)
    carpeta = os.path.join(get_gps_upload_folder(), clave_equipo, fecha)
    os.makedirs(carpeta, exist_ok=True)

    nombre_pdf = ''
    nombre_csv = ''
    archivo_pdf = request.files.get('pdf')
    if archivo_pdf and archivo_pdf.filename:
        nombre_pdf = secure_filename(archivo_pdf.filename)
        archivo_pdf.save(os.path.join(carpeta, nombre_pdf))

    archivo_csv = request.files.get('csv')
    if archivo_csv and archivo_csv.filename:
        nombre_csv = secure_filename(archivo_csv.filename)
        archivo_csv.save(os.path.join(carpeta, nombre_csv))

    if not nombre_pdf and not nombre_csv:
        return jsonify({"status": "error", "message": "No se ha subido ningún archivo."}), 400

    registrar_sesion_gps(equipo, fecha, nombre_pdf, nombre_csv)
    return jsonify({"status": "success", "pdf": nombre_pdf, "csv": nombre_csv, "clave_equipo": clave_equipo})


@gps_bp.route('/api/gps/pendientes', methods=['GET'])
def api_gps_pendientes():
    """Lista los PDF/CSV que se han pegado directamente en la carpeta gps_pendientes,
    a falta de clasificar (asignarles equipo y fecha)."""
    carpeta = get_gps_pendientes_folder()
    archivos = sorted([
        f for f in os.listdir(carpeta)
        if os.path.isfile(os.path.join(carpeta, f)) and f.lower().endswith(('.pdf', '.csv'))
    ])
    return jsonify({"archivos": archivos})


@gps_bp.route('/api/gps/clasificar', methods=['POST'])
def api_gps_clasificar():
    """Mueve un archivo pendiente a la carpeta del equipo/fecha indicados y lo registra."""
    usuario = session.get('usuario')
    if not usuario:
        return jsonify({"status": "error", "message": "No autenticado."}), 401

    data = request.json or {}
    nombre_archivo = secure_filename((data.get('nombre_archivo') or '').strip())
    equipo = (data.get('equipo') or '').strip()
    fecha = (data.get('fecha') or '').strip()
    if not nombre_archivo or not equipo or not fecha:
        return jsonify({"status": "error", "message": "Falta archivo, equipo o fecha."}), 400

    origen = os.path.join(get_gps_pendientes_folder(), nombre_archivo)
    if not os.path.isfile(origen):
        return jsonify({"status": "error", "message": "El archivo ya no está en pendientes."}), 404

    clave_equipo = sanitizar_equipo_gps(equipo)
    carpeta_destino = os.path.join(get_gps_upload_folder(), clave_equipo, fecha)
    os.makedirs(carpeta_destino, exist_ok=True)
    destino = os.path.join(carpeta_destino, nombre_archivo)
    os.replace(origen, destino)

    es_pdf = nombre_archivo.lower().endswith('.pdf')
    registrar_sesion_gps(equipo, fecha, nombre_pdf=nombre_archivo if es_pdf else '', nombre_csv=nombre_archivo if not es_pdf else '')

    return jsonify({"status": "success"})


@gps_bp.route('/api/gps/analisis')
def api_gps_analisis():
    """Para cada métrica del CSV de la sesión: media del equipo, top 3 jugadores y
    jugadores por debajo de la media."""
    equipo = (request.args.get('equipo') or '').strip()
    fecha = (request.args.get('fecha') or '').strip()
    if not equipo or not fecha:
        return jsonify({"status": "error", "message": "Falta equipo o fecha."}), 400

    ruta = _localizar_csv_sesion(equipo, fecha)
    if not ruta:
        return jsonify({"status": "error", "message": "Esta sesión no tiene CSV registrado."}), 404

    try:
        datos = parsear_csv_gps(ruta)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error al leer el CSV: {e}"}), 500

    metricas_resumen = {}
    for m in datos['columnas']:
        pares = [(j['nombre'], j['valores'].get(m)) for j in datos['jugadores'] if j['valores'].get(m) is not None]
        pares.sort(key=lambda x: x[1], reverse=True)
        media = datos['promedio'].get(m)
        maximo = max((v for _, v in pares), default=None)
        debajo_media = [{"nombre": n, "valor": v} for n, v in pares if media is not None and v < media]
        peores3 = sorted(pares, key=lambda x: x[1])[:3]
        metricas_resumen[m] = {
            "media": media,
            "maximo": maximo,
            "top3": [{"nombre": n, "valor": v} for n, v in pares[:3]],
            "peores3": [{"nombre": n, "valor": v} for n, v in peores3],
            "debajo_media": debajo_media,
            "todos": [{"nombre": n, "valor": v} for n, v in pares],
        }

    return jsonify({
        "status": "success",
        "columnas": datos['columnas'],
        "metricas": metricas_resumen,
        "promedio": datos['promedio'],
        "suma": datos['suma'],
        "jugadores": datos['jugadores'],
        "nombres": [j['nombre'] for j in datos['jugadores']],
    })


@gps_bp.route('/api/gps/temporada')
def api_gps_temporada():
    """Compara la sesión indicada con el acumulado/media de todas las sesiones con CSV del equipo."""
    equipo = (request.args.get('equipo') or '').strip()
    fecha = (request.args.get('fecha') or '').strip()
    if not equipo or not fecha:
        return jsonify({"status": "error", "message": "Falta equipo o fecha."}), 400

    try:
        resultado = calcular_temporada(equipo, fecha)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error calculando la temporada: {e}"}), 500

    if resultado is None:
        return jsonify({"status": "error", "message": "No hay sesiones con CSV registrado para este equipo."}), 404

    return jsonify({"status": "success", **resultado})


@gps_bp.route('/api/gps/posiciones', methods=['GET', 'POST'])
def api_gps_posiciones():
    if request.method == 'GET':
        equipo = (request.args.get('equipo') or '').strip()
        if not equipo:
            return jsonify({"status": "error", "message": "Falta equipo."}), 400
        return jsonify({"status": "success", "info": obtener_info_jugadores_equipo(equipo)})

    data = request.json or {}
    equipo = (data.get('equipo') or '').strip()
    jugador = (data.get('jugador') or '').strip()
    if not equipo or not jugador:
        return jsonify({"status": "error", "message": "Falta equipo o jugador."}), 400
    posicion = data.get('posicion')
    edad = data.get('edad')
    guardar_info_jugador(equipo, jugador, posicion=posicion, edad=edad)
    return jsonify({"status": "success"})


@gps_bp.route('/api/gps/comentarios', methods=['GET'])
def api_gps_comentarios_get():
    """Devuelve los comentarios ya generados (cacheados) para esta sesión, si existen."""
    equipo = (request.args.get('equipo') or '').strip()
    fecha = (request.args.get('fecha') or '').strip()
    if not equipo or not fecha:
        return jsonify({"status": "error", "message": "Falta equipo o fecha."}), 400
    clave_equipo = sanitizar_equipo_gps(equipo)
    ruta_cache = get_gps_comentarios_path(clave_equipo, fecha)
    if os.path.isfile(ruta_cache):
        with open(ruta_cache, encoding='utf-8') as f:
            return jsonify({"status": "success", "comentarios": json.load(f)})
    return jsonify({"status": "success", "comentarios": {}})


@gps_bp.route('/api/gps/comentarios/generar', methods=['POST'])
def api_gps_generar_comentarios():
    """Genera (o regenera) con IA un comentario profesional por jugador para esta sesión y lo cachea en disco."""
    usuario = session.get('usuario')
    if not usuario:
        return jsonify({"status": "error", "message": "No autenticado."}), 401

    data = request.json or {}
    equipo = (data.get('equipo') or '').strip()
    fecha = (data.get('fecha') or '').strip()
    if not equipo or not fecha:
        return jsonify({"status": "error", "message": "Falta equipo o fecha."}), 400

    ruta = _localizar_csv_sesion(equipo, fecha)
    if not ruta:
        return jsonify({"status": "error", "message": "Esta sesión no tiene CSV registrado."}), 404

    try:
        datos = parsear_csv_gps(ruta)
        info_jugadores = obtener_info_jugadores_equipo(equipo)
        comentarios = generar_comentarios_ia(datos, info_jugadores)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error generando comentarios: {e}"}), 500

    clave_equipo = sanitizar_equipo_gps(equipo)
    ruta_cache = get_gps_comentarios_path(clave_equipo, fecha)
    os.makedirs(os.path.dirname(ruta_cache), exist_ok=True)
    with open(ruta_cache, 'w', encoding='utf-8') as f:
        json.dump(comentarios, f, ensure_ascii=False)

    return jsonify({"status": "success", "comentarios": comentarios})


@gps_bp.route('/api/gps/comentarios/generar_uno', methods=['POST'])
def api_gps_generar_comentario_uno():
    """Regenera el comentario de un solo jugador (más rápido que regenerar todo el equipo),
    útil tras cambiar su posición o edad."""
    usuario = session.get('usuario')
    if not usuario:
        return jsonify({"status": "error", "message": "No autenticado."}), 401

    data = request.json or {}
    equipo = (data.get('equipo') or '').strip()
    fecha = (data.get('fecha') or '').strip()
    jugador = (data.get('jugador') or '').strip()
    if not equipo or not fecha or not jugador:
        return jsonify({"status": "error", "message": "Falta equipo, fecha o jugador."}), 400

    ruta = _localizar_csv_sesion(equipo, fecha)
    if not ruta:
        return jsonify({"status": "error", "message": "Esta sesión no tiene CSV registrado."}), 404

    try:
        datos = parsear_csv_gps(ruta)
        jugador_dict = next((j for j in datos['jugadores'] if j['nombre'] == jugador), None)
        if not jugador_dict:
            return jsonify({"status": "error", "message": "Jugador no encontrado en esta sesión."}), 404
        info_jugador = obtener_info_jugadores_equipo(equipo).get(jugador, {})
        comentario = generar_comentario_jugador_ia(jugador_dict, info_jugador, datos['promedio'])
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error generando comentario: {e}"}), 500

    clave_equipo = sanitizar_equipo_gps(equipo)
    ruta_cache = get_gps_comentarios_path(clave_equipo, fecha)
    comentarios = {}
    if os.path.isfile(ruta_cache):
        with open(ruta_cache, encoding='utf-8') as f:
            comentarios = json.load(f)
    comentarios[jugador] = comentario
    os.makedirs(os.path.dirname(ruta_cache), exist_ok=True)
    with open(ruta_cache, 'w', encoding='utf-8') as f:
        json.dump(comentarios, f, ensure_ascii=False)

    return jsonify({"status": "success", "comentario": comentario})


@gps_bp.route('/api/gps/benchmark', methods=['GET', 'POST'])
def api_gps_benchmark():
    """GET: devuelve la referencia orientativa guardada para una posición+edad (o null si no existe).
    POST: guarda/edita manualmente esa referencia (origen='MANUAL')."""
    if request.method == 'GET':
        posicion = (request.args.get('posicion') or '').strip()
        edad = (request.args.get('edad') or '').strip()
        if not posicion or not edad:
            return jsonify({"status": "error", "message": "Falta posición o edad."}), 400
        resultado = obtener_benchmark(posicion, edad)
        return jsonify({"status": "success", "benchmark": resultado})

    data = request.json or {}
    posicion = (data.get('posicion') or '').strip()
    edad = (data.get('edad') or '').strip()
    metricas = data.get('metricas') or {}
    if not posicion or not edad:
        return jsonify({"status": "error", "message": "Falta posición o edad."}), 400
    guardar_benchmark(posicion, edad, metricas, origen='MANUAL')
    return jsonify({"status": "success"})


@gps_bp.route('/api/gps/benchmark/generar', methods=['POST'])
def api_gps_generar_benchmark():
    """Genera con IA una referencia orientativa por posición+edad para las métricas indicadas."""
    usuario = session.get('usuario')
    if not usuario:
        return jsonify({"status": "error", "message": "No autenticado."}), 401

    data = request.json or {}
    posicion = (data.get('posicion') or '').strip()
    edad = (data.get('edad') or '').strip()
    metricas_nombres = data.get('metricas') or []
    if not posicion or not edad or not metricas_nombres:
        return jsonify({"status": "error", "message": "Falta posición, edad o métricas."}), 400

    try:
        metricas = generar_benchmark_ia(posicion, edad, metricas_nombres)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error generando la referencia: {e}"}), 500

    guardar_benchmark(posicion, edad, metricas, origen='IA')
    return jsonify({"status": "success", "benchmark": {"metricas": metricas, "origen": "IA"}})
