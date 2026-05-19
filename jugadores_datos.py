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

# Definición de columnas según el formulario solicitado
COLUMNAS_DATOS_JUGADORES = [
    "JUGADOR_NOMBRE", "JUGADOR_APELLIDOS", "JUGADOR_COLEGIO", "JUGADOR_FECHA_NACIMIENTO",
    "JUGADOR_EQUIPO", "JUGADOR_LETRA", "JUGADOR_DOMICILIO", "JUGADOR_CP",
    "JUGADOR_TEL_FIJO", "JUGADOR_MOVIL", "JUGADOR_EMAIL",
    "PADRE_NOMBRE", "MADRE_NOMBRE",
    "FORMA_PAGO",
    "SEPA_NOMBRE_DEUDOR", "SEPA_DIRECCION_DEUDOR", "SEPA_CP_POBLACION_CIUDAD",
    "SEPA_PAIS", "SEPA_SWIFT_BIC", "SEPA_IBAN", "SEPA_TIPO_PAGO",
    "SEPA_FECHA_LOCALIDAD", "SEPA_FIRMA_DETECTADA", "ARCHIVO_URL"
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

        # --- INTENTO 1: MÉTODO TRADICIONAL (Alternativa a la IA) ---
        datos_extraidos = extraer_datos_pdf_tradicional(file_content, mime_type)
        if datos_extraidos and any(v for v in datos_extraidos.values() if v):
            datos_extraidos['ARCHIVO_URL'] = file_url
            print("INFO: Datos extraídos con éxito mediante pdfplumber (sin IA)")
            return jsonify({"status": "success", "data": datos_extraidos, "metodo": "tradicional"})
        
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
                        # widget.field_name es el ID interno (ej: 'txt_nombre')
                        # widget.field_value es lo que se escribió
                        val = widget.field_value
                        # Normalización de checkboxes/botones
                        if widget.field_type == 2: # Checkbox/Radio
                            val = "SI" if val not in ["Off", "", None, "No"] else "NO"
                        metadatos_formulario[widget.field_name] = val
                doc.close()
            except Exception as e_meta:
                print(f"Aviso: No se pudieron extraer metadatos AcroForm: {e_meta}")

        prompt = f"""
        Actúa como un sistema experto en procesamiento de documentos PDF y mapeo de metadatos AcroForm. 
        Tu objetivo es realizar una extracción de FALLA CERO combinando la visión multimodal con los metadatos técnicos proporcionados.

        CONTEXTO TÉCNICO (DICCIONARIO DEL PDF):
        {json.dumps(metadatos_formulario, indent=2) if metadatos_formulario else "No se detectaron campos de metadatos digitales."}

        INSTRUCCIONES DE PRIORIDAD ABSOLUTA:
        1. **Prioridad Metadatos**: Si un campo existe en el "CONTEXTO TÉCNICO", usa ese valor como fuente de verdad primaria.
        2. **Mapeo de Nombres**: Relaciona los 'Field Names' técnicos (como 'topmostSubform[0].Page1[0].Nombre[0]') con las llaves JSON solicitadas basándote en la semántica.
        3. **Tratamiento de Checkboxes**: Usa los valores del contexto técnico para determinar si una opción está marcada (SI/NO). No te bases solo en la visión si el metadato está disponible.
        4. **Limpieza de Ruido**: Ignora todo el texto estático, avisos legales y encabezados. Devuelve exclusivamente los datos del usuario.
        5. **Firma**: Si en la imagen se observa un trazo manuscrito en el recuadro de firma, devuelve "SI", de lo contrario "NO".

        CAMPOS A EXTRAER Y SUS LLAVES EXACTAS:
        1. **DATOS JUGADOR**: 
           - **JUGADOR_NOMBRE**: Nombre completo.
           - **JUGADOR_APELLIDOS**: Apellidos completos.
           - **JUGADOR_COLEGIO**: Nombre del colegio.
           - **JUGADOR_FECHA_NACIMIENTO**: Fecha de nacimiento.
           - **JUGADOR_EQUIPO**: Nombre del equipo.
           - **JUGADOR_LETRA**: Letra del equipo (A, B, C...).
           - Domicilio, Código Postal (CP).
           - **JUGADOR_TEL_FIJO**: Teléfono fijo (suele empezar por 91).
           - **JUGADOR_MOVIL**: Teléfono móvil (empieza por 6 o 7).
           - **JUGADOR_EMAIL**: E-mail de contacto.
           - **PADRE_NOMBRE**: Nombre del padre.
           - **MADRE_NOMBRE**: Nombre de la madre.
        2. **FORMA_PAGO**: Identifica cuál casilla está marcada: "PAGO EN OFICINA" o "PAGO DOMICILIADO".
        3. **SEPA_NOMBRE_DEUDOR**: Nombre del titular.
           - **SEPA_DIRECCION_DEUDOR**: Dirección del deudor.
           - **SEPA_CP_POBLACION_CIUDAD**: Bloque de CP, Población y Ciudad.
           - **SEPA_PAIS**: País del deudor.
           - **SEPA_SWIFT_BIC**: SWIFT/BIC.
           - **SEPA_IBAN**: IBAN completo (debe empezar por ES).
           - **SEPA_TIPO_PAGO**: Identifica cuál casilla está marcada: "PAGO RECURRENTE TRES PAGOS" o "PAGO UNICO".
           - **SEPA_FECHA_LOCALIDAD**: Fecha y Localidad escrita en el mandato.
        4. **SEPA_FIRMA_DETECTADA**: "SI" o "NO" según el recuadro de firma.

        REGLAS DE SALIDA OBLIGATORIAS:
        - Devuelve EXCLUSIVAMENTE el objeto JSON plano sin bloques de código markdown.
        - Usa estas llaves exactas: {', '.join(COLUMNAS_DATOS_JUGADORES[:-1])}
        - Si un cuadro azul está vacío, pon "".
        - No inventes datos que no estén dentro de un cuadro azul.
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
        # Log detallado para depuración
        print("--- DETALLE DEL ERROR IA ---")
        print(f"Tipo de error: {type(e).__name__}")
        print(f"Mensaje: {str(e)}")
        try:
            print("Modelos que tu API ve actualmente:", [m.name for m in genai.list_models()])
        except:
            print("No se pudieron listar los modelos. Revisa tu API KEY.")
        print(f"Error IA: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@jugadores_datos_bp.route('/api/jugadores_datos/confirmar', methods=['POST'])
def confirmar_ficha():
    from app import client, NOMBRE_EXCEL
    datos = request.json
    try:
        try:
            sheet = client.open(NOMBRE_EXCEL).worksheet("DATOS JUGADORES")
        except gspread.exceptions.WorksheetNotFound:
            sheet = client.open(NOMBRE_EXCEL).add_worksheet(title="DATOS JUGADORES", rows="1000", cols="25")
            sheet.append_row(COLUMNAS_DATOS_JUGADORES)
        
        fila = [datos.get(col, "") for col in COLUMNAS_DATOS_JUGADORES]
        sheet.append_row(fila)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@jugadores_datos_bp.route('/api/jugadores_datos', methods=['GET'])
def get_datos_jugadores():
    from app import client, NOMBRE_EXCEL
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("DATOS JUGADORES")
        # Devolvemos los datos para la tabla principal de la pestaña
        return jsonify(sheet.get_all_records())
    except:
        return jsonify([])