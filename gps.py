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
    """Devuelve la lista de equipos GPS disponibles.
    Primero intenta leer de la hoja EQUIPO de Google Sheets;
    luego añade cualquier equipo que tenga carpeta en el sistema de archivos."""
    client, name = _client_name()
    equipos = set()
    try:
        sheet = client.open(name).worksheet("EQUIPO")
        all_v = sheet.get_all_values()
        if all_v:
            headers = [str(h).strip().upper() for h in all_v[0]]
            if "EQUIPO" in headers:
                idx_e = headers.index("EQUIPO")
                for r in all_v[1:]:
                    if len(r) > idx_e and r[idx_e].strip():
                        equipos.add(r[idx_e].strip())
    except Exception as e:
        print(f"Error cargando equipos GPS de Sheets: {e}")

    # Respaldo: leer también desde la hoja GPS (columna EQUIPO)
    try:
        sheet_gps = get_gps_sheet()
        all_v = sheet_gps.get_all_values()
        for row in all_v[1:]:
            if row and row[0].strip():
                equipos.add(row[0].strip())
    except Exception as e:
        print(f"Error leyendo equipos de hoja GPS: {e}")

    # Respaldo final: nombres de carpetas en el directorio GPS de uploads
    try:
        base = current_app.config.get('UPLOAD_FOLDER', os.path.join(os.getcwd(), 'static', 'uploads'))
        carpeta_gps = os.path.join(base, 'gps')
        if os.path.isdir(carpeta_gps):
            for nombre_carpeta in os.listdir(carpeta_gps):
                if os.path.isdir(os.path.join(carpeta_gps, nombre_carpeta)):
                    # Convertir clave sanitizada de vuelta a nombre legible (best-effort)
                    nombre_display = nombre_carpeta.replace('_', ' ')
                    equipos.add(nombre_display)
    except Exception as e:
        print(f"Error leyendo carpetas GPS: {e}")

    return sorted(equipos)


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


def _trimmed_mean_2sigma(values):
    """Media del equipo eliminando outliers que se alejan >2σ."""
    if not values:
        return None
    if len(values) <= 2:
        return sum(values) / len(values)
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = variance ** 0.5
    if std == 0:
        return mean
    filtered = [v for v in values if abs(v - mean) <= 2 * std]
    return sum(filtered) / len(filtered) if filtered else mean


def _calcular_stats_equipo_mes(equipo, tipo='ENT'):
    """
    Para cada sesión y métrica calcula la media del equipo (sin outliers >2σ).
    Agrupa por mes y devuelve la media mensual + datos por sesión para el calendario.
    """
    if not equipo:
        return None
    try:
        inicio_temp, fin_temp = _get_temporada_actual()
        clave_equipo = sanitizar_equipo_gps(equipo)
        carpeta_equipo = os.path.join(get_gps_upload_folder(), clave_equipo)

        sesiones_raw = []
        if os.path.isdir(carpeta_equipo):
            for fecha_str in sorted(os.listdir(carpeta_equipo)):
                try:
                    fecha_dt = datetime.strptime(fecha_str, '%Y-%m-%d')
                except ValueError:
                    continue
                if not (inicio_temp <= fecha_dt <= fin_temp):
                    continue
                meta = get_meta_sesion(equipo, fecha_str)
                if tipo and meta.get('tipo', '') != tipo:
                    continue
                carpeta_sesion = os.path.join(carpeta_equipo, fecha_str)
                csvs = [f for f in os.listdir(carpeta_sesion) if f.lower().endswith('.csv')]
                if not csvs:
                    continue
                try:
                    datos = parsear_csv_gps(os.path.join(carpeta_sesion, csvs[0]))
                    sesiones_raw.append({
                        'fecha': fecha_str,
                        'mes': fecha_dt.month,
                        'dia': fecha_dt.day,
                        'datos': datos,
                    })
                except Exception:
                    pass

        if not sesiones_raw:
            return {"metricas": [], "meses": [], "por_mes": {}, "sesiones": []}

        todas_metricas = []
        for s in sesiones_raw:
            for m in s['datos']['columnas']:
                if m not in todas_metricas:
                    todas_metricas.append(m)

        # acum[metrica][mes_str] = [trimmed_mean_sesion, ...]
        acum = {m: {} for m in todas_metricas}
        sesiones_out = []

        for s in sesiones_raw:
            ses_m = {}
            for m in todas_metricas:
                vals = [j['valores'].get(m) for j in s['datos']['jugadores']
                        if j['valores'].get(m) is not None]
                tm = _trimmed_mean_2sigma(vals) if vals else None
                if tm is not None:
                    tm = round(tm, 2)
                ses_m[m] = tm
                if tm is not None:
                    mes_key = str(s['mes'])
                    acum[m].setdefault(mes_key, []).append(tm)
            sesiones_out.append({
                'fecha': s['fecha'],
                'mes': s['mes'],
                'dia': s['dia'],
                'metricas': ses_m,
            })

        por_mes = {}
        meses_con_datos = set()
        for m in todas_metricas:
            por_mes[m] = {}
            for mes_key, vals in acum[m].items():
                por_mes[m][mes_key] = {
                    'media': round(sum(vals) / len(vals), 2),
                    'n_sesiones': len(vals),
                }
                meses_con_datos.add(int(mes_key))

        meses_orden = [9, 10, 11, 12, 1, 2, 3, 4, 5, 6]
        meses_sorted = [m for m in meses_orden if m in meses_con_datos]

        return {
            "metricas": todas_metricas,
            "meses": meses_sorted,
            "por_mes": por_mes,
            "sesiones": sesiones_out,
        }
    except Exception as e:
        print(f"GPS equipo_mes error: {e}")
        return None


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


def _calcular_stats_jugadores(equipo, tipo='ENT'):
    """Calcula las stats agregadas de jugadores para un equipo/tipo. Mismo algoritmo que la API."""
    if not equipo:
        return {}, []
    try:
        inicio_temp, fin_temp = _get_temporada_actual()
        clave_equipo = sanitizar_equipo_gps(equipo)
        carpeta_equipo = os.path.join(get_gps_upload_folder(), clave_equipo)
        sesiones = []
        if os.path.isdir(carpeta_equipo):
            for fecha_str in sorted(os.listdir(carpeta_equipo)):
                try:
                    fecha_dt = datetime.strptime(fecha_str, '%Y-%m-%d')
                except ValueError:
                    continue
                if not (inicio_temp <= fecha_dt <= fin_temp):
                    continue
                meta = get_meta_sesion(equipo, fecha_str)
                tipo_sesion = meta.get('tipo', '')
                if tipo and tipo_sesion != tipo:
                    continue
                carpeta_sesion = os.path.join(carpeta_equipo, fecha_str)
                csvs = [f for f in os.listdir(carpeta_sesion) if f.lower().endswith('.csv')]
                if not csvs:
                    continue
                try:
                    datos = parsear_csv_gps(os.path.join(carpeta_sesion, csvs[0]))
                    sesiones.append({'mes': fecha_dt.month, 'datos': datos})
                except Exception:
                    pass
        if not sesiones:
            return {}, []
        todas_metricas = []
        for s in sesiones:
            for m in s['datos']['columnas']:
                if m not in todas_metricas:
                    todas_metricas.append(m)
        raw = {}
        for s in sesiones:
            mes = str(s['mes'])
            for j in s['datos']['jugadores']:
                nombre = j['nombre']
                if not nombre:
                    continue
                if nombre not in raw:
                    raw[nombre] = {'sesiones': 0, 'metricas': {m: {'vals': [], 'por_mes': {}} for m in todas_metricas}}
                raw[nombre]['sesiones'] += 1
                for m in todas_metricas:
                    v = j['valores'].get(m)
                    if v is not None:
                        raw[nombre]['metricas'][m]['vals'].append(v)
                        raw[nombre]['metricas'][m]['por_mes'].setdefault(mes, []).append(v)
        resultado = {}
        for nombre, d in raw.items():
            resultado[nombre] = {'sesiones': d['sesiones'], 'metricas': {}}
            for m in todas_metricas:
                vals = d['metricas'][m]['vals']
                por_mes = {k: round(sum(v) / len(v), 2) for k, v in d['metricas'][m]['por_mes'].items()}
                resultado[nombre]['metricas'][m] = {
                    'media': round(sum(vals) / len(vals), 2) if vals else None,
                    'por_mes': por_mes
                }
        return resultado, todas_metricas
    except Exception as e:
        print(f"GPS _calcular_stats_jugadores error: {e}")
        return {}, []


@gps_bp.route('/gps')
def gps():
    usuario = session.get('usuario')
    if not usuario:
        return redirect('/')
    perms = session.get('permisos', {})
    if perms.get('D.DEPORTIVA') != 'SI' and usuario.lower() != 'admin':
        return "Acceso denegado", 403

    equipos = obtener_equipos_gps()
    equipo_inicial = equipos[0] if equipos else ''
    stats_inicial, metricas_inicial = _calcular_stats_jugadores(equipo_inicial, 'ENT')
    return render_template('gps.html', usuario=usuario, equipos=equipos,
                           equipo_inicial=equipo_inicial,
                           equipo_defecto=equipo_inicial,
                           stats_inicial=stats_inicial,
                           metricas_inicial=metricas_inicial)


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
            eq = row[0].strip()
            fe = row[1].strip()
            meta = get_meta_sesion(eq, fe)
            sesiones.append({
                "equipo": eq,
                "fecha": fe,
                "pdf": row[2].strip() if len(row) > 2 else "",
                "csv": row[3].strip() if len(row) > 3 else "",
                "tipo": meta.get('tipo', ''),
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

    tipo = (request.form.get('tipo') or '').strip().upper()
    if tipo in ('ENT', 'PART'):
        meta = get_meta_sesion(equipo, fecha)
        meta['tipo'] = tipo
        guardar_meta_sesion(equipo, fecha, meta)

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


# ── Metadatos de sesión (tipo ENT/PART) ──────────────────────────────────────

def _get_meta_path(clave_equipo, fecha):
    return os.path.join(get_gps_upload_folder(), clave_equipo, fecha, 'sesion_meta.json')

def get_meta_sesion(equipo, fecha):
    ruta = _get_meta_path(sanitizar_equipo_gps(equipo), fecha)
    if os.path.isfile(ruta):
        with open(ruta, encoding='utf-8') as f:
            return json.load(f)
    return {}

def guardar_meta_sesion(equipo, fecha, meta):
    clave = sanitizar_equipo_gps(equipo)
    carpeta = os.path.join(get_gps_upload_folder(), clave, fecha)
    os.makedirs(carpeta, exist_ok=True)
    with open(_get_meta_path(clave, fecha), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False)


@gps_bp.route('/api/gps/sesion_tipo', methods=['POST'])
def api_gps_sesion_tipo():
    """Guarda o actualiza el tipo (ENT/PART) de una sesión."""
    usuario = session.get('usuario')
    if not usuario:
        return jsonify({"status": "error", "message": "No autenticado."}), 401
    data = request.json or {}
    equipo = (data.get('equipo') or '').strip()
    fecha  = (data.get('fecha')  or '').strip()
    tipo   = (data.get('tipo')   or '').strip().upper()
    if not equipo or not fecha or tipo not in ('ENT', 'PART'):
        return jsonify({"status": "error", "message": "Datos incorrectos."}), 400
    meta = get_meta_sesion(equipo, fecha)
    meta['tipo'] = tipo
    guardar_meta_sesion(equipo, fecha, meta)
    return jsonify({"status": "success"})


def _get_temporada_actual():
    """Devuelve (inicio, fin) de la temporada activa. Sep–Jun."""
    hoy = datetime.now()
    if hoy.month >= 9:
        return datetime(hoy.year, 9, 1), datetime(hoy.year + 1, 6, 30)
    return datetime(hoy.year - 1, 9, 1), datetime(hoy.year, 6, 30)


@gps_bp.route('/api/gps/stats_jugadores')
def api_gps_stats_jugadores():
    equipo = (request.args.get('equipo') or '').strip()
    tipo   = (request.args.get('tipo')   or '').strip().upper()
    if not equipo:
        return jsonify({"status": "error", "message": "Falta equipo."}), 400
    jugadores, metricas = _calcular_stats_jugadores(equipo, tipo)
    return jsonify({'status': 'ok', 'jugadores': jugadores, 'metricas': metricas,
                    'num_sesiones': max((v['sesiones'] for v in jugadores.values()), default=0)})


@gps_bp.route('/api/gps/equipo_mes')
def api_gps_equipo_mes():
    equipo = (request.args.get('equipo') or '').strip()
    tipo   = (request.args.get('tipo')   or 'ENT').strip().upper()
    if not equipo:
        return jsonify({"status": "error", "message": "Falta equipo"}), 400
    data = _calcular_stats_equipo_mes(equipo, tipo)
    if data is None:
        return jsonify({"status": "error", "message": "Error calculando stats"}), 500
    return jsonify({"status": "success", **data})


@gps_bp.route('/api/gps/sugerencias_stat', methods=['POST'])
def api_gps_sugerencias_stat():
    """Genera sugerencias de entrenamiento para una métrica concreta del equipo."""
    data = request.json or {}
    metrica   = (data.get('metrica')  or '').strip()
    valor     = data.get('valor')
    ideal     = data.get('ideal')
    pct_diff  = data.get('pct_diff')
    categoria = (data.get('categoria') or '').strip()
    tipo      = (data.get('tipo')      or 'ENT').strip().upper()

    if not metrica:
        return jsonify({"status": "error", "message": "Falta métrica"}), 400

    estado = "por encima del ideal" if (pct_diff or 0) >= 0 else "por debajo del ideal"
    accion = "mantener o mejorar aún más" if (pct_diff or 0) >= 0 else "mejorar y alcanzar el ideal"
    tipo_label = "entrenamiento" if tipo == 'ENT' else "partido"

    prompt = (
        f"Eres un preparador físico profesional especializado en fútbol base y amateur. "
        f"Responde de forma ESQUEMÁTICA y CONCISA. Sin párrafos largos. Usa frases cortas.\n\n"
        f"DATOS:\n"
        f"· Métrica: {metrica}\n"
        f"· Categoría: {categoria} · Sesión: {tipo_label}\n"
        f"· Valor equipo: {valor} · Ideal: {ideal} · Diferencia: {f'+{pct_diff}' if (pct_diff or 0) >= 0 else pct_diff}%\n"
        f"· Situación: {estado}\n\n"
        f"DEVUELVE este JSON exacto (sin markdown, sin texto extra):\n"
        '{{'
        '"analisis_puntos": ["punto 1 (máx 15 palabras)", "punto 2", "punto 3"],'
        '"ejercicios": [{{'
        '"nombre": "Nombre del ejercicio",'
        '"objetivo": "1 frase corta",'
        '"formato": "Ej: 4x6min · 80% int. · pausa 2min",'
        '"clave": "1 clave táctica/física",'
        '"busqueda_yt": "english search term for YouTube"'
        '}}],'
        '"preguntas_sugeridas": ["pregunta 1?", "pregunta 2?", "pregunta 3?"]'
        '}}'
        f"\n\nGenera exactamente 3 ejercicios para {accion} esta métrica."
    )

    try:
        model = _modelo_gemini()
        resp  = model.generate_content(prompt)
        texto = (resp.text or '').strip().strip('`')
        if texto.lower().startswith('json'):
            texto = texto[4:]
        result = json.loads(texto)
        return jsonify({"status": "success", **result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@gps_bp.route('/api/gps/sugerencias_stat/chat', methods=['POST'])
def api_gps_sugerencias_stat_chat():
    """Responde preguntas de seguimiento sobre una métrica GPS del equipo."""
    data = request.json or {}
    metrica   = (data.get('metrica')  or '').strip()
    valor     = data.get('valor')
    ideal     = data.get('ideal')
    pct_diff  = data.get('pct_diff')
    categoria = (data.get('categoria') or '').strip()
    tipo      = (data.get('tipo')      or 'ENT').strip().upper()
    pregunta  = (data.get('pregunta')  or '').strip()

    if not metrica or not pregunta:
        return jsonify({"status": "error", "message": "Faltan datos"}), 400

    tipo_label = "entrenamiento" if tipo == 'ENT' else "partido"
    prompt = (
        f"Eres un preparador físico profesional especializado en fútbol. "
        f"Contexto: métrica '{metrica}', categoría {categoria}, sesiones de {tipo_label}. "
        f"Valor actual del equipo: {valor}. Ideal: {ideal}. "
        f"Diferencia: {f'+{pct_diff}' if (pct_diff or 0) >= 0 else pct_diff}%.\n\n"
        f"Pregunta del entrenador: {pregunta}\n\n"
        f"Responde de forma profesional, concisa (máx. 150 palabras) y práctica. "
        f"Si mencionas ejercicios específicos, termina con: BUSCAR: [término YouTube en inglés]"
    )

    try:
        model = _modelo_gemini()
        resp  = model.generate_content(prompt)
        return jsonify({"status": "success", "respuesta": (resp.text or '').strip()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


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


def _festivos_colegios_madrid(año_ini, año_fin):
    """Festivos y vacaciones escolares Comunidad de Madrid para una temporada."""
    from datetime import date, timedelta
    festivos = set()

    def rango(d_ini, d_fin):
        d = d_ini
        while d <= d_fin:
            festivos.add(d)
            d += timedelta(days=1)

    # ── Temporada 2025-2026 ──────────────────────────────────────────
    if año_ini == 2025:
        # Festivos nacionales/regionales
        for f in [
            date(2025, 10, 12),  # Hispanidad
            date(2025, 11,  1),  # Todos los Santos
            date(2025, 12,  6),  # Constitución
            date(2025, 12,  8),  # Inmaculada (lunes → cancela entrenamiento)
            date(2026,  1,  1),  # Año Nuevo
            date(2026,  1,  6),  # Reyes
            date(2026,  3, 19),  # San José
            date(2026,  4,  2),  # Jueves Santo
            date(2026,  4,  3),  # Viernes Santo
            date(2026,  5,  1),  # Trabajador
            date(2026,  5,  2),  # Comunidad de Madrid
            date(2026,  5, 15),  # San Isidro
        ]:
            festivos.add(f)
        # Vacaciones Navidad: 20 dic – 7 ene
        rango(date(2025, 12, 20), date(2026,  1,  7))
        # Vacaciones Semana Santa: 30 mar – 7 abr (Pascua 5 abr 2026)
        rango(date(2026,  3, 30), date(2026,  4,  7))

    # ── Temporada 2026-2027 (esqueleto para el futuro) ───────────────
    if año_ini == 2026:
        for f in [
            date(2026, 10, 12),
            date(2026, 11,  1),
            date(2026, 12,  6),
            date(2026, 12,  8),
            date(2027,  1,  1),
            date(2027,  1,  6),
            date(2027,  5,  1),
            date(2027,  5,  2),
        ]:
            festivos.add(f)
        rango(date(2026, 12, 21), date(2027,  1,  7))
        # Semana Santa 2027: Pascua 28 mar → ~22-31 mar
        rango(date(2027,  3, 22), date(2027,  3, 31))

    return festivos


@gps_bp.route('/api/gps/dias_entrenamiento')
def api_gps_dias_entrenamiento():
    """Devuelve los días de entrenamiento del equipo de la temporada, basado en el horario de STAFF."""
    import calendar as cal_mod
    from datetime import date

    equipo = (request.args.get('equipo') or '').strip().upper()
    if not equipo:
        return jsonify({'status': 'error', 'fechas': []})

    def _parse_dias(texto):
        mapping = {
            'L': 0, 'LUNES': 0, 'M': 1, 'MARTES': 1,
            'X': 2, 'MIERCOLES': 2, 'MIÉRCOLES': 2,
            'J': 3, 'JUEVES': 3, 'V': 4, 'VIERNES': 4,
            'S': 5, 'SABADO': 5, 'SÁBADO': 5, 'D': 6, 'DOMINGO': 6
        }
        res = []
        for p in (texto or '').upper().replace(';', ',').replace(' ', ',').split(','):
            p = p.strip()
            if p in mapping and mapping[p] not in res:
                res.append(mapping[p])
        return res

    try:
        client, name = _client_name()

        # Buscar horario en STAFF
        dias_semana = []
        try:
            staff_records = client.open(name).worksheet("STAFF").get_all_records()
            for s in staff_records:
                eq = str(s.get('EQUIPO', '')).strip().upper().replace('_', ' ')
                if eq == equipo.replace('_', ' '):
                    dias_semana = _parse_dias(str(s.get('DIAS ENTRENAMIENTO', '')))
                    if dias_semana:
                        break
        except Exception as e:
            print(f"GPS dias_entrenamiento - error STAFF: {e}")

        if not dias_semana:
            return jsonify({'status': 'ok', 'fechas': [], 'source': 'none'})

        # Temporada: Sep del año anterior → Jun del año en curso
        hoy = date.today()
        año_fin = hoy.year if hoy.month >= 7 else hoy.year
        año_ini = año_fin - 1
        meses_temp = [
            (año_ini, 9), (año_ini, 10), (año_ini, 11), (año_ini, 12),
            (año_fin, 1), (año_fin, 2), (año_fin, 3), (año_fin, 4), (año_fin, 5), (año_fin, 6)
        ]

        festivos = _festivos_colegios_madrid(año_ini, año_fin)

        fechas = []
        for (año, mes) in meses_temp:
            dias_en_mes = cal_mod.monthrange(año, mes)[1]
            for dia in range(1, dias_en_mes + 1):
                f = date(año, mes, dia)
                if f.weekday() in dias_semana and f <= hoy and f not in festivos:
                    fechas.append(f"{dia:02d}/{mes:02d}/{año}")

        return jsonify({'status': 'ok', 'fechas': fechas, 'source': 'staff'})

    except Exception as e:
        print(f"Error en api_gps_dias_entrenamiento: {e}")
        return jsonify({'status': 'error', 'fechas': []})
