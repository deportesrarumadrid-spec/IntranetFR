import os
import json
import re
import io
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
try:
    import pdfplumber
except ImportError:
    pdfplumber = None
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None
import gspread
from gspread.utils import rowcol_to_a1
try:
    import google.generativeai as genai
except ImportError:
    genai = None

jugadores_datos_bp = Blueprint('jugadores_datos', __name__)

# --- ALTERNATIVA: EXTRACTOR TRADICIONAL (Sin IA) ---
def extraer_datos_pdf_tradicional(file_content, mime_type):
    """Extrae texto buscando palabras clave en el PDF (solo para PDFs digitales)."""
    if not pdfplumber or 'pdf' not in mime_type.lower():
        return None
    
    try:
        text = ""
        with pdfplumber.open(io.BytesIO(file_content)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text: text += page_text + "\n"
        
        if not text.strip(): return None # Es un escaneo/imagen, requiere IA

        datos = {col: "" for col in COLUMNAS_DATOS_JUGADORES}
        
        # Diccionario de búsqueda por expresiones regulares (ajusta según tus PDFs)
        mapping = {
            "JUGADOR_NOMBRE": r"(?i)Nombre[:\s]+([^\n]+)",
            "JUGADOR_APELLIDOS": r"(?i)Apellidos[:\s]+([^\n]+)",
            "JUGADOR_EMAIL": r"(?i)Email[:\s]+([\w\.-]+@[\w\.-]+)",
            "SEPA_IBAN": r"(?i)IBAN[:\s]+([A-Z]{2}[0-9\s]{15,30})",
            "JUGADOR_MOVIL": r"(?i)Móvil[:\s]+(\d{9})"
        }
        
        for key, pattern in mapping.items():
            match = re.search(pattern, text)
            if match:
                datos[key] = match.group(1).strip()
                
        return datos
    except Exception as e:
        print(f"Error en extracción tradicional: {e}")
        return None

# Definición de columnas según el formulario solicitado (alineado con cabeceras de Google Sheets)
COLUMNAS_DATOS_JUGADORES = [
    "JUGADOR_NOMBRE", "JUGADOR_APELLIDOS", "JUGADOR_COLEGIO", "JUGADOR_FECHA_NACIMIENTO",
    "JUGADOR_EQUIPO_LETRA", "JUGADOR_DOMICILIO_CP", "JUGADOR_TELEFONO_MOVIL", "JUGADOR_EMAIL",
    "FAMILIA_NOMBRE_PADRE", "FAMILIA_NOMBRE_MADRE", "PAGO_MODALIDAD", "SEPA_NOMBRE_DEUDOR",
    "SEPA_DIRECCION_DEUDOR", "SEPA_SWIFT_BIC", "SEPA_IBAN", "SEPA_TIPO_PAGO",
    "AUTORIZA_TUTOR_NOMBRE", "AUTORIZA_JUGADOR_NOMBRE", "AUTORIZA_JUGADOR_DNI",
    "CIERRE_FECHA_LOCALIDAD", "CIERRE_FIRMA_TUTOR", "ARCHIVO_URL"
]

def inicializar_ia():
    """Carga la configuración de IA desde el archivo de secretos."""
    if not genai:
        return
    try:
        with open('secretos.json', 'r') as f:
            config = json.load(f)
            api_key = config.get('gemini_api_key')
            if api_key:
                genai.configure(api_key=api_key)
                print(f"--- SDK Gemini v{genai.__version__} configurado correctamente ---")
            else:
                print("CRÍTICO: No se encontró gemini_api_key en secretos.json")
    except Exception as e:
        print(f"Error al configurar Gemini: {e}")

inicializar_ia()

@jugadores_datos_bp.route('/api/jugadores_datos/upload', methods=['POST'])
def upload_ficha_ia():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No se recibió archivo"}), 400
    
    if not genai:
        return jsonify({"status": "error", "message": "Librería IA no detectada. Ejecuta: pip install -U google-generativeai"}), 500

    file = request.files['file']
    mime_type = file.mimetype
    
    try:
        # Leemos el contenido binario del archivo (funciona para Imagen y PDF)
        file_content = file.read()
        
        # Guardamos el archivo físicamente para referencia posterior y previsualización utilizando el config de la app
        upload_folder = current_app.config.get('UPLOAD_FOLDER', os.path.join(os.getcwd(), 'static', 'uploads'))
        os.makedirs(upload_folder, exist_ok=True) # Aseguramos que la carpeta existe para evitar errores
        ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'pdf'
        filename = f"ficha_ia_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
        file_path = os.path.join(upload_folder, filename)
        with open(file_path, 'wb') as f:
            f.write(file_content)
        file_url = f"/static/uploads/{filename}"

        # --- SOLUCIÓN DEFINITIVA: SELECCIÓN DINÁMICA ---
        # Consultamos a la API qué modelos exactos tienes habilitados para evitar el 404
        try:
            modelos_visibles = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            # Buscamos la mejor coincidencia: 1.5-flash, luego cualquier flash, luego 1.5-pro
            nombre_modelo = next((m for m in modelos_visibles if 'gemini-1.5-flash' in m), 
                            next((m for m in modelos_visibles if 'flash' in m), 
                            next((m for m in modelos_visibles if 'gemini-1.5-pro' in m), 
                            'models/gemini-1.5-flash')))
        except Exception as e_list:
            print(f"No se pudo listar modelos: {e_list}. Usando nombre por defecto.")
            nombre_modelo = 'models/gemini-1.5-flash'

        print(f"DEBUG IA: Usando modelo detectado -> {nombre_modelo}")
        
        model = genai.GenerativeModel(model_name=nombre_modelo)
        
        # --- EXTRACCIÓN TÉCNICA DE ACROFORMS (CAMPOS AZULES) ---
        metadatos_formulario = {}
        if fitz and 'pdf' in mime_type.lower():
            try:
                doc = fitz.open(stream=file_content, filetype="pdf")
                for page in doc:
                    widgets = page.widgets()
                    for widget in widgets:
                        val = widget.field_value
                        if widget.field_type == 2: # Checkbox/Radio
                            val = "SI" if val not in ["Off", "", None, "No"] else "NO"
                        metadatos_formulario[widget.field_name] = val
                doc.close()
            except Exception as e_meta:
                print(f"Aviso: No se pudieron extraer metadatos AcroForm: {e_meta}")

        prompt = f"""
        Eres un experto en extracción de datos de formularios de inscripción deportiva escaneados o en PDF.

        ESTRUCTURA DEL FORMULARIO:
        Este documento es la HOJA DATOS de la Escuela de Fútbol Club Fuentelarreyna (temporada 2025-2026).
        El formulario tiene DOS secciones principales en la PÁGINA 1:
        - Bloque de datos del JUGADOR (parte superior): las etiquetas están impresas y los valores rellenados a mano o escritos debajo/al lado.
        - Bloque SEPA (parte inferior): datos bancarios para domiciliación.
        La PÁGINA 2 es la hoja de autorizaciones con nombre del tutor, DNI y firma.

        ADVERTENCIA CRÍTICA — DISTINCIÓN ETIQUETA vs VALOR:
        El formulario imprime las etiquetas en una fila (ej: "Nombre:", "Apellidos:", "Colegio:") y los valores
        rellenados aparecen debajo o a continuación. NUNCA confundas el texto de la etiqueta con el valor.
        Por ejemplo: si ves "Nombre: Apellidos: Colegio: _______" en una línea y en la siguiente
        "ADRIÁN  HERNÁNDEZ FERNÁNDEZ  VALDELUZ", el nombre es ADRIÁN, los apellidos son HERNÁNDEZ FERNÁNDEZ
        y el colegio es VALDELUZ.

        METADATOS AcroForm DETECTADOS EN EL PDF (fuente de verdad prioritaria si existen):
        {json.dumps(metadatos_formulario, indent=2) if metadatos_formulario else "No se detectaron campos de formulario digital."}

        INSTRUCCIONES:
        1. Si hay metadatos AcroForm, úsalos como fuente primaria para los campos correspondientes.
        2. Para los checkboxes: determina cuál opción está marcada visualmente (casilla tachada/rellena) o por metadato.
        3. Para PAGO_MODALIDAD: puede ser "PAGO EN OFICINA" o "PAGO DOMICILIADO".
        4. Para SEPA_TIPO_PAGO: puede ser "PAGO RECURRENTE TRES PAGOS" o "PAGO UNICO".
        5. JUGADOR_DOMICILIO_CP: incluye la calle/número y el código postal juntos (ej: "FINISTERRE 8 10º B 28029").
        6. JUGADOR_TELEFONO_MOVIL: incluye todos los teléfonos separados por " / " (fijo y móvil).
        7. CIERRE_FIRMA_TUTOR: "SI" si hay firma manuscrita o sello digital, "NO" si el recuadro está vacío.
        8. Ignora completamente el texto de las etiquetas, avisos legales, encabezados y líneas de puntos.

        CAMPOS A EXTRAER (llaves JSON exactas):
        - JUGADOR_NOMBRE: solo el nombre de pila del jugador (ej: "ADRIÁN")
        - JUGADOR_APELLIDOS: apellidos del jugador (ej: "HERNÁNDEZ FERNÁNDEZ")
        - JUGADOR_COLEGIO: colegio del jugador
        - JUGADOR_FECHA_NACIMIENTO: fecha en formato DD/MM/AAAA
        - JUGADOR_EQUIPO_LETRA: equipo y letra (ej: "ALEVÍN F7 A")
        - JUGADOR_DOMICILIO_CP: domicilio completo con código postal
        - JUGADOR_TELEFONO_MOVIL: teléfonos de contacto
        - JUGADOR_EMAIL: email de contacto
        - FAMILIA_NOMBRE_PADRE: nombre completo del padre
        - FAMILIA_NOMBRE_MADRE: nombre completo de la madre
        - PAGO_MODALIDAD: "PAGO EN OFICINA" o "PAGO DOMICILIADO"
        - SEPA_NOMBRE_DEUDOR: nombre del titular bancario
        - SEPA_DIRECCION_DEUDOR: dirección del titular bancario
        - SEPA_SWIFT_BIC: código BIC/SWIFT
        - SEPA_IBAN: IBAN completo (empieza por ES)
        - SEPA_TIPO_PAGO: "PAGO RECURRENTE TRES PAGOS" o "PAGO UNICO"
        - AUTORIZA_TUTOR_NOMBRE: nombre completo del tutor que autoriza (página 2)
        - AUTORIZA_JUGADOR_NOMBRE: nombre del jugador autorizado (página 2)
        - AUTORIZA_JUGADOR_DNI: DNI del tutor (página 2)
        - CIERRE_FECHA_LOCALIDAD: fecha y localidad al pie del documento
        - CIERRE_FIRMA_TUTOR: "SI" o "NO"

        SALIDA: responde ÚNICAMENTE con el objeto JSON plano, sin bloques de código markdown, sin explicaciones.
        Si un campo no aparece en el documento, devuelve "".
        """

        response = model.generate_content([
            prompt,
            {'mime_type': mime_type, 'data': file_content}
        ])
        
        texto_respuesta = response.text
        match = re.search(r'\{.*\}', texto_respuesta, re.DOTALL)
        if match:
            texto_limpio = match.group(0)
        else:
            texto_limpio = texto_respuesta.strip()
        
        datos_extraidos = json.loads(texto_limpio)
        # Incluimos la URL del archivo procesado para que el frontal lo muestre
        datos_extraidos['ARCHIVO_URL'] = file_url
        
        # Asegurar que todas las columnas existan en el diccionario
        for col in COLUMNAS_DATOS_JUGADORES:
            if col not in datos_extraidos:
                datos_extraidos[col] = ""

        return jsonify({"status": "success", "data": datos_extraidos})
    except Exception as e:
        print("--- DETALLE DEL ERROR IA ---")
        print(f"Tipo de error: {type(e).__name__}")
        print(f"Mensaje: {str(e)}")
        # Fallback: intento extractor tradicional si Gemini falla
        try:
            datos_fallback = extraer_datos_pdf_tradicional(file_content, mime_type)
            if datos_fallback and any(v for v in datos_fallback.values() if v):
                datos_fallback['ARCHIVO_URL'] = file_url
                print("INFO: Fallback a pdfplumber tras error de Gemini")
                return jsonify({"status": "success", "data": datos_fallback, "metodo": "tradicional"})
        except Exception:
            pass
        return jsonify({"status": "error", "message": str(e)}), 500

@jugadores_datos_bp.route('/api/jugadores_datos/confirmar', methods=['POST'])
def confirmar_ficha():
    import shutil
    import unicodedata
    from app import client, NOMBRE_EXCEL
    datos = request.json
    try:
        # 1. Mover y Renombrar el archivo subido si existe
        archivo_url = datos.get('ARCHIVO_URL', '').strip()
        if archivo_url and archivo_url.startswith('/static/uploads/'):
            filename_orig = os.path.basename(archivo_url)
            upload_folder = current_app.config.get('UPLOAD_FOLDER', os.path.join(os.getcwd(), 'static', 'uploads'))
            temp_path = os.path.join(upload_folder, filename_orig)
            
            if os.path.exists(temp_path):
                # Generar nombre destino
                nombre = datos.get('JUGADOR_NOMBRE', '').strip()
                apellidos = datos.get('JUGADOR_APELLIDOS', '').strip()
                equipo = datos.get('JUGADOR_EQUIPO_LETRA', '').strip()
                
                parts = [nombre, apellidos, equipo]
                combined_name = "_".join([p for p in parts if p])
                
                # Normalizar texto para nombre de archivo seguro
                def clean_filename(text):
                    nfkd_form = unicodedata.normalize('NFKD', text)
                    only_ascii = "".join([c for c in nfkd_form if not unicodedata.category(c).startswith('M')])
                    cleaned = re.sub(r'[^a-zA-Z0-9_\-]', '', only_ascii.replace(' ', '_'))
                    return cleaned.upper()
                
                sanitized_name = clean_filename(combined_name)
                ext = filename_orig.rsplit('.', 1)[1].lower() if '.' in filename_orig else 'pdf'
                new_filename = f"{sanitized_name}.{ext}"
                
                target_folder = os.path.join(os.getcwd(), 'static', 'hojas_inscripcion')
                os.makedirs(target_folder, exist_ok=True)
                new_path = os.path.join(target_folder, new_filename)
                
                try:
                    shutil.copy(temp_path, new_path)
                    # Actualizar URL al nuevo destino
                    datos['ARCHIVO_URL'] = f"/static/hojas_inscripcion/{new_filename}"
                    print(f"DEBUG: Archivo copiado exitosamente a: {new_path}")
                except Exception as ex_copy:
                    print(f"Error al copiar archivo de revisión: {ex_copy}")

        # 2. Guardar en Google Sheets
        try:
            sheet = client.open(NOMBRE_EXCEL).worksheet("DATOS JUGADORES")
        except gspread.exceptions.WorksheetNotFound:
            sheet = client.open(NOMBRE_EXCEL).add_worksheet(title="DATOS JUGADORES", rows="1000", cols="25")
            sheet.append_row(COLUMNAS_DATOS_JUGADORES)
        
        # Self-healing de cabeceras en Google Sheets
        try:
            headers = [h.strip().upper() for h in sheet.row_values(1)]
            if "ARCHIVO_URL" not in headers:
                sheet.update_cell(1, len(headers) + 1, "ARCHIVO_URL")
                print("DEBUG: Columna ARCHIVO_URL agregada al Google Sheet.")
        except Exception as e_sh:
            print(f"Error en self-healing del Google Sheet: {e_sh}")

        fila = [datos.get(col, "") for col in COLUMNAS_DATOS_JUGADORES]
        sheet.append_row(fila, value_input_option='USER_ENTERED')
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@jugadores_datos_bp.route('/api/validar_ficha_ia', methods=['POST'])
def validar_ficha_ia():
    import unicodedata
    from app import client, NOMBRE_EXCEL
    datos = request.json or {}

    nombre   = str(datos.get('nombre', '')).strip()
    apellidos = str(datos.get('apellidos', '')).strip()
    equipo   = str(datos.get('equipo', '')).strip().upper()
    iban_raw = str(datos.get('iban', '')).strip().replace(' ', '').upper()

    def norm(s):
        s = str(s).strip().upper()
        nfkd = unicodedata.normalize('NFKD', s)
        s = ''.join(c for c in nfkd if not unicodedata.category(c).startswith('M'))
        return ' '.join(s.split())

    resultados = {}

    # ── 1. Validar jugador en equipo ──────────────────────────────────
    res_jug = {'ok': False, 'msg': ''}
    if not nombre and not apellidos:
        res_jug = {'ok': False, 'msg': 'Nombre y apellidos vacíos'}
    elif not equipo:
        res_jug = {'ok': False, 'msg': 'No se ha seleccionado equipo'}
    else:
        try:
            wb = client.open(NOMBRE_EXCEL)
            sheet_jug = wb.worksheet("JUGADORES")
            all_v = sheet_jug.get_all_values()

            def lh(h):
                nfkd = unicodedata.normalize('NFKD', str(h).strip().upper())
                return ''.join(c for c in nfkd if not unicodedata.category(c).startswith('M')).replace(' ', '').replace('_', '')

            hdrs = [lh(h) for h in all_v[0]] if all_v else []
            idx_nom = next((i for i, h in enumerate(hdrs) if h == 'NOMBRE'), 0)
            idx_eq  = next((i for i, h in enumerate(hdrs) if h in ('EQUIPO','CATEGORIA','GRUPO','EQUIPOS')), 1)

            jugadores_equipo = []
            for row in all_v[1:]:
                eq_row = row[idx_eq].strip().upper() if idx_eq < len(row) else ''
                if norm(eq_row) == norm(equipo):
                    nom_row = row[idx_nom].strip() if idx_nom < len(row) else ''
                    if nom_row:
                        jugadores_equipo.append(norm(nom_row))

            if not jugadores_equipo:
                res_jug = {'ok': False, 'warn': False, 'msg': f'No se encontraron jugadores para el equipo "{equipo}"'}
            else:
                nombre_n    = norm(nombre)
                apellidos_n = norm(apellidos)
                palabras_apellido = [w for w in apellidos_n.split() if len(w) > 2]

                candidatos = [j for j in jugadores_equipo if nombre_n and nombre_n in j.split()]
                if candidatos:
                    apellido_ok = not palabras_apellido or any(
                        any(ap in cand for ap in palabras_apellido) for cand in candidatos
                    )
                    if apellido_ok:
                        res_jug = {'ok': True, 'warn': False, 'msg': f'Jugador encontrado en {equipo}'}
                    else:
                        # Nombre sí, apellido no → advertencia (warn), no bloqueo
                        res_jug = {'ok': True, 'warn': True,
                                   'msg': f'Nombre "{nombre}" está en {equipo} pero ningún apellido coincide. Jugadores en roster: {", ".join(candidatos[:3])}'}
                else:
                    palabras_busq = [w for w in (nombre_n + ' ' + apellidos_n).split() if len(w) > 2]
                    similares = [j for j in jugadores_equipo if any(p in j for p in palabras_busq)]
                    if similares:
                        res_jug = {'ok': False, 'warn': False,
                                   'msg': f'"{nombre}" no encontrado en {equipo}. Similares: {", ".join(similares[:3])}'}
                    else:
                        res_jug = {'ok': False, 'warn': False,
                                   'msg': f'"{nombre} {apellidos}" no encontrado en el equipo {equipo}'}
        except Exception as e:
            res_jug = {'ok': False, 'msg': f'Error al consultar roster: {str(e)}'}

    resultados['jugador'] = res_jug

    # ── 2. Validar IBAN ───────────────────────────────────────────────
    def validar_iban(iban):
        if not iban:
            return False, 'IBAN vacío'
        if not iban.startswith('ES'):
            return False, f'IBAN debe empezar por ES (se recibió: {iban[:2]})'
        if len(iban) != 24:
            return False, f'IBAN español debe tener 24 caracteres (tiene {len(iban)})'
        if not iban[2:].isdigit():
            return False, 'IBAN contiene caracteres no numéricos tras ES'
        # Mod-97 check
        rearranged = iban[4:] + iban[:4]
        numeric = ''.join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
        if int(numeric) % 97 != 1:
            return False, 'IBAN inválido (fallo de dígito de control)'
        return True, f'IBAN válido: {iban[:4]} {iban[4:8]} {iban[8:12]} {iban[12:16]} {iban[16:20]} {iban[20:]}'

    iban_ok, iban_msg = validar_iban(iban_raw)
    resultados['iban'] = {'ok': iban_ok, 'msg': iban_msg}

    return jsonify(resultados)


@jugadores_datos_bp.route('/api/jugadores_datos', methods=['GET'])
def get_datos_jugadores():
    from app import client, NOMBRE_EXCEL
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("DATOS JUGADORES")
        # Devolvemos los datos para la tabla principal de la pestaña
        return jsonify(sheet.get_all_records())
    except:
        return jsonify([])

@jugadores_datos_bp.route('/api/jugadores_datos/fichas_pendientes', methods=['GET'])
def get_fichas_pendientes():
    """
    Cruza DATOS JUGADORES con JUGADORES (roster maestro) + ASISTENCIAS.
    - pendientes:   jugadores en roster sin ficha en DATOS JUGADORES
    - sin_asignar:  filas de DATOS JUGADORES sin coincidencia en roster
                    (incluye flag 'ambiguo' si hay varios jugadores con el mismo nombre)
    """
    import unicodedata
    from app import client, NOMBRE_EXCEL
    from flask import session as flask_session
    if not flask_session.get('usuario'):
        return jsonify({"status": "error"}), 401

    def norm(s):
        s = str(s).strip().upper()
        nfkd = unicodedata.normalize('NFKD', s)
        s = ''.join(c for c in nfkd if not unicodedata.category(c).startswith('M'))
        return ' '.join(s.split())

    def limpiar_h(h):
        s = str(h).strip().upper()
        nfkd = unicodedata.normalize('NFKD', s)
        return ''.join(c for c in nfkd if not unicodedata.category(c).startswith('M')).replace(' ', '').replace('_', '')

    try:
        wb = client.open(NOMBRE_EXCEL)

        # --- 1. Leer DATOS JUGADORES (con índice de fila para poder actualizar) ---
        fichas = []
        try:
            sheet_datos = wb.worksheet("DATOS JUGADORES")
            for idx_f, r in enumerate(sheet_datos.get_all_records()):
                nombre_n = norm(str(r.get('JUGADOR_NOMBRE', '')) + ' ' + str(r.get('JUGADOR_APELLIDOS', '')))
                equipo = str(r.get('JUGADOR_EQUIPO_LETRA', '')).strip().upper()
                nombre_orig = (str(r.get('JUGADOR_NOMBRE', '')) + ' ' + str(r.get('JUGADOR_APELLIDOS', ''))).strip()
                if nombre_n.strip():
                    fichas.append({'nombre_norm': nombre_n, 'equipo': equipo, 'nombre_orig': nombre_orig, 'idx': idx_f})
        except Exception:
            pass

        # --- 2. Leer JUGADORES (roster maestro — lista para soportar nombres duplicados) ---
        roster = []  # [{nombre_norm, nombre, equipo}]
        roster_seen = set()  # clave nombre_norm+equipo para no duplicar la misma combinación
        try:
            sheet_jug = wb.worksheet("JUGADORES")
            all_v = sheet_jug.get_all_values()
            if all_v:
                hdrs = [limpiar_h(h) for h in all_v[0]]
                idx_nom = next((i for i, h in enumerate(hdrs) if h == 'NOMBRE'), 0)
                idx_eq  = next((i for i, h in enumerate(hdrs) if h in ('EQUIPO', 'CATEGORIA', 'GRUPO', 'EQUIPOS')), 1)
                for row in all_v[1:]:
                    nombre_raw = row[idx_nom].strip() if idx_nom < len(row) else ''
                    equipo_raw = row[idx_eq].strip().upper() if idx_eq < len(row) else ''
                    if not nombre_raw or nombre_raw in ('-', '—'):
                        continue
                    nombre_n = norm(nombre_raw)
                    key = nombre_n + '|' + equipo_raw
                    if nombre_n and key not in roster_seen:
                        roster_seen.add(key)
                        roster.append({'nombre_norm': nombre_n, 'nombre': nombre_raw, 'equipo': equipo_raw})
        except Exception:
            pass

        # --- 3. Completar con ASISTENCIAS ---
        try:
            sheet_asis = wb.worksheet("ASISTENCIAS")
            all_a = sheet_asis.get_all_values()
            if all_a:
                ha = [limpiar_h(h) for h in all_a[0]]
                idx_nom_a = next((i for i, h in enumerate(ha) if h == 'NOMBRE'), 2)
                idx_eq_a  = next((i for i, h in enumerate(ha) if h in ('EQUIPO', 'CATEGORIA', 'GRUPO')), 1)
                for row in all_a[1:]:
                    nombre_raw = row[idx_nom_a].strip() if idx_nom_a < len(row) else ''
                    equipo_raw = row[idx_eq_a].strip().upper() if idx_eq_a < len(row) else ''
                    if not nombre_raw or nombre_raw in ('-', '—'):
                        continue
                    nombre_n = norm(nombre_raw)
                    key = nombre_n + '|' + equipo_raw
                    if nombre_n and key not in roster_seen:
                        roster_seen.add(key)
                        roster.append({'nombre_norm': nombre_n, 'nombre': nombre_raw, 'equipo': equipo_raw})
        except Exception:
            pass

        # Índice de fichas: cuántas fichas hay por nombre normalizado
        from collections import Counter
        fichas_count = Counter(f['nombre_norm'] for f in fichas)
        # Cuántas veces aparece cada nombre_norm en el roster
        roster_count = Counter(r['nombre_norm'] for r in roster)

        # --- 4. Fichas pendientes: entradas de roster sin ficha suficiente ---
        # Si hay N jugadores con el mismo nombre y M fichas, max(0,N-M) están pendientes
        fichas_usadas = Counter()
        pendientes = []
        for r in roster:
            nn = r['nombre_norm']
            fichas_disponibles = fichas_count.get(nn, 0)
            if fichas_usadas[nn] < fichas_disponibles:
                fichas_usadas[nn] += 1  # esta entrada del roster tiene ficha
            else:
                pendientes.append({'nombre': r['nombre'], 'equipo': r['equipo']})

        pendientes.sort(key=lambda x: (x['equipo'], x['nombre']))
        por_equipo = {}
        for p in pendientes:
            eq = p['equipo'] or 'Sin equipo'
            por_equipo.setdefault(eq, []).append(p['nombre'])

        # --- 5. Sin asignar: fichas sin jugador en roster, con flag de ambigüedad ---
        roster_norms = set(r['nombre_norm'] for r in roster)
        # Jugadores del roster por nombre_norm (para mostrar opciones al asignar)
        roster_por_nombre = {}
        for r in roster:
            roster_por_nombre.setdefault(r['nombre_norm'], []).append({'nombre': r['nombre'], 'equipo': r['equipo']})

        sin_asignar = []
        for f in fichas:
            if f['nombre_norm'] not in roster_norms:
                sin_asignar.append({
                    'nombre_ficha': f['nombre_orig'],
                    'equipo': f['equipo'],
                    'idx': f['idx'],
                    'ambiguo': False,
                    'opciones': []
                })
            elif roster_count[f['nombre_norm']] > 1:
                # Mismo nombre en varios equipos → ambiguo
                sin_asignar.append({
                    'nombre_ficha': f['nombre_orig'],
                    'equipo': f['equipo'],
                    'idx': f['idx'],
                    'ambiguo': True,
                    'opciones': roster_por_nombre[f['nombre_norm']]
                })

        # Lista completa del roster para selector de asignación manual
        roster_lista = sorted(
            [{'nombre': r['nombre'], 'equipo': r['equipo']} for r in roster],
            key=lambda x: (x['equipo'], x['nombre'])
        )

        return jsonify({
            'total': len(pendientes),
            'por_equipo': por_equipo,
            'lista': pendientes,
            'sin_asignar': sin_asignar,
            'roster': roster_lista
        })
    except Exception as e:
        return jsonify({'total': 0, 'por_equipo': {}, 'lista': [], 'sin_asignar': [], 'roster': [], 'error': str(e)})


@jugadores_datos_bp.route('/api/jugadores_datos/actualizar', methods=['POST'])
def actualizar_datos_jugador():
    from app import client, NOMBRE_EXCEL
    data = request.json or {}
    idx = data.get('idx')
    campos = data.get('campos') or {}
    if idx is None:
        return jsonify({"status": "error", "message": "Falta el índice de la ficha"}), 400

    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("DATOS JUGADORES")
        headers = [h.strip().upper() for h in sheet.row_values(1)]
        fila_sheet = int(idx) + 2  # +1 cabecera, +1 porque idx es base 0

        updates = []
        for col, val in campos.items():
            if col in headers:
                col_idx = headers.index(col) + 1
                updates.append({'range': f"'DATOS JUGADORES'!{rowcol_to_a1(fila_sheet, col_idx)}", 'values': [[val]]})

        if updates:
            sheet.spreadsheet.values_batch_update({'valueInputOption': 'USER_ENTERED', 'data': updates})

        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error actualizando ficha de jugador: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@jugadores_datos_bp.route('/api/jugadores_datos/eliminar', methods=['POST'])
def eliminar_datos_jugador():
    from app import client, NOMBRE_EXCEL
    from flask import session as flask_session
    if not flask_session.get('usuario'):
        return jsonify({"status": "error"}), 401
    data = request.json or {}
    idx = data.get('idx')
    if idx is None:
        return jsonify({"status": "error", "message": "Falta el índice de la ficha"}), 400

    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("DATOS JUGADORES")
        fila_sheet = int(idx) + 2  # +1 cabecera, +1 porque idx es base 0
        sheet.delete_rows(fila_sheet)
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error eliminando ficha de jugador: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500