import os
import re
import io
import base64
import unicodedata
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, current_app
from PIL import Image, ImageDraw, ImageFont
import gspread

from jugadores_datos import COLUMNAS_DATOS_JUGADORES

inscripcion_bp = Blueprint('inscripcion_bp', __name__)

def clean_filename(text):
    nfkd_form = unicodedata.normalize('NFKD', text)
    only_ascii = "".join([c for c in nfkd_form if not unicodedata.category(c).startswith('M')])
    cleaned = re.sub(r'[^a-zA-Z0-9_\-]', '', only_ascii.replace(' ', '_'))
    return cleaned.upper()

def get_font(size):
    font_paths = [
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf"
    ]
    for path in font_paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def paste_signature(img, base64_str, box):
    try:
        if ',' in base64_str:
            base64_str = base64_str.split(',')[1]
        sig_data = base64.b64decode(base64_str)
        sig_img = Image.open(io.BytesIO(sig_data))
        
        x1, y1, x2, y2 = box
        w = x2 - x1
        h = y2 - y1
        
        # Resize signature maintaining alpha if present
        sig_img = sig_img.resize((w, h), Image.Resampling.LANCZOS)
        
        if sig_img.mode == 'RGBA':
            img.paste(sig_img, (x1, y1), sig_img)
        else:
            img.paste(sig_img, (x1, y1))
    except Exception as e:
        print(f"Error pasting signature onto PDF page: {e}")

def generar_pdf_inscripcion(datos, sig1, sig2, dest_path):
    static_dir = os.path.join(current_app.root_path, 'static')
    img1_path = os.path.join(static_dir, 'inscripcion_p1.png')
    img2_path = os.path.join(static_dir, 'inscripcion_p2.png')
    
    if not os.path.exists(img1_path) or not os.path.exists(img2_path):
        raise FileNotFoundError("Las plantillas de inscripción en la carpeta static no existen.")

    # Open templates in RGB mode
    img1 = Image.open(img1_path).convert('RGB')
    img2 = Image.open(img2_path).convert('RGB')
    
    draw1 = ImageDraw.Draw(img1)
    draw2 = ImageDraw.Draw(img2)
    
    font = get_font(12)
    
    def draw_text(draw, text, pos):
        if text:
            draw.text(pos, str(text).upper(), fill=(20, 20, 110), font=font) # Dark blue text looks filled by hand
            
    # Page 1 values drawing
    draw_text(draw1, datos.get('JUGADOR_APELLIDOS', ''), (89, 133))
    draw_text(draw1, datos.get('JUGADOR_NOMBRE', ''), (217, 133))
    draw_text(draw1, datos.get('JUGADOR_COLEGIO', ''), (432, 133))
    
    draw_text(draw1, datos.get('JUGADOR_FECHA_NACIMIENTO', ''), (130, 155))
    draw_text(draw1, datos.get('JUGADOR_EQUIPO', ''), (268, 154))
    draw_text(draw1, datos.get('JUGADOR_LETRA', ''), (416, 154))
    
    draw_text(draw1, datos.get('JUGADOR_DOMICILIO', ''), (96, 176))
    draw_text(draw1, datos.get('JUGADOR_CP', ''), (531, 176))
    
    draw_text(draw1, datos.get('JUGADOR_TEL_FIJO', ''), (119, 197))
    draw_text(draw1, datos.get('JUGADOR_MOVIL', ''), (225, 197))
    draw_text(draw1, datos.get('JUGADOR_EMAIL', ''), (335, 197))
    
    draw_text(draw1, datos.get('FAMILIA_NOMBRE_PADRE', ''), (119, 218))
    draw_text(draw1, datos.get('FAMILIA_NOMBRE_MADRE', ''), (375, 218))
    
    # Draw checkmarks for payment method
    fpago = str(datos.get('PAGO_MODALIDAD', '')).upper()
    if 'OFICINA' in fpago:
        draw_text(draw1, "X", (81, 287))
    elif 'DOMICILIADO' in fpago:
        draw_text(draw1, "X", (81, 308))

    # SEPA Details
    draw_text(draw1, datos.get('SEPA_NOMBRE_DEUDOR', ''), (241, 566))
    draw_text(draw1, datos.get('SEPA_DIRECCION_DEUDOR', ''), (252, 586))
    draw_text(draw1, datos.get('SEPA_CP_POBLACION_CIUDAD', ''), (345, 606))
    draw_text(draw1, datos.get('SEPA_PAIS', ''), (511, 606))
    draw_text(draw1, datos.get('SEPA_SWIFT_BIC', ''), (148, 624))
    draw_text(draw1, datos.get('SEPA_IBAN', ''), (291, 654))
    
    sepa_tipo = str(datos.get('SEPA_TIPO_PAGO', '')).upper()
    if 'RECURRENTE' in sepa_tipo:
        draw_text(draw1, "X", (97, 749))
    elif 'UNICO' in sepa_tipo:
        draw_text(draw1, "X", (97, 769))
        
    draw_text(draw1, datos.get('CIERRE_FECHA_LOCALIDAD', ''), (152, 801))
    
    # Paste SEPA Signature (sig1)
    if sig1:
        paste_signature(img1, sig1, (440, 792, 595, 878))
        
    # Page 2 values drawing
    draw_text(draw2, datos.get('AUTORIZA_TUTOR_NOMBRE', ''), (76, 122))
    draw_text(draw2, datos.get('AUTORIZA_TUTOR_DNI', ''), (408, 122))
    draw_text(draw2, datos.get('AUTORIZA_JUGADOR_NOMBRE', ''), (280, 149))
    draw_text(draw2, datos.get('CIERRE_FECHA_LOCALIDAD', ''), (152, 820))
    
    # Paste Authorization Signature (sig2)
    if sig2:
        paste_signature(img2, sig2, (370, 810, 570, 890))
        
    # Save combined images as single PDF
    img1.save(dest_path, "PDF", resolution=100.0, save_all=True, append_images=[img2])

@inscripcion_bp.route('/inscripcion', methods=['GET', 'POST'])
def inscripcion_publica():
    if request.method == 'GET':
        # Load active teams for dropdown
        equipos = []
        try:
            from app import app as main_app
            client = main_app.gs_client
            sheet_eq = client.open(main_app.gs_name).worksheet("EQUIPO")
            rows_eq = sheet_eq.get_all_values()
            if rows_eq:
                headers = [str(h).strip().upper() for h in rows_eq[0]]
                if "EQUIPO" in headers:
                    idx_e = headers.index("EQUIPO")
                    equipos = sorted(list(set(str(r[idx_e]).strip() for r in rows_eq[1:] if len(r) > idx_e and r[idx_e].strip())))
        except Exception as e:
            print(f"Error cargando equipos en inscripción: {e}")
            equipos = ["PRIMER EQUIPO", "BENJAMIN A", "ALEVIN A", "INFANTIL A", "JUVENIL A"] # Fallback
            
        return render_template('inscripcion.html', equipos=equipos)

@inscripcion_bp.route('/api/inscripcion/submit', methods=['POST'])
def api_inscripcion_submit():
    from app import client, NOMBRE_EXCEL
    try:
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "No se recibieron datos"}), 400
        
        # 1. Sanitize file name
        nombre = data.get('JUGADOR_NOMBRE', '').strip()
        apellidos = data.get('JUGADOR_APELLIDOS', '').strip()
        equipo = data.get('JUGADOR_EQUIPO', '').strip()
        letra = data.get('JUGADOR_LETRA', '').strip()
        
        combined_team = f"{equipo} {letra}".strip()
        parts = [nombre, apellidos, combined_team]
        combined_filename = "_".join([p for p in parts if p])
        sanitized_filename = clean_filename(combined_filename) + ".pdf"
        
        # Destination folder
        target_folder = os.path.join(os.getcwd(), 'static', 'hojas_inscripcion')
        os.makedirs(target_folder, exist_ok=True)
        pdf_path = os.path.join(target_folder, sanitized_filename)
        pdf_url = f"/static/hojas_inscripcion/{sanitized_filename}"
        
        # 2. Extract signatures
        sig1 = data.get('sig1', '') # SEPA Signature
        sig2 = data.get('sig2', '') # Auth Signature
        
        # 3. Generate PDF
        generar_pdf_inscripcion(data, sig1, sig2, pdf_path)
        
        # 4. Map form dictionary to Google Sheets row format
        # JUGADOR_EQUIPO_LETRA is combined team + letter
        # JUGADOR_DOMICILIO_CP is combined address + CP
        # JUGADOR_TELEFONO_MOVIL is combined phone numbers
        # SEPA_DIRECCION_DEUDOR is combined SEPA address + CP + Pais
        
        sepa_addr = data.get('SEPA_DIRECCION_DEUDOR', '').strip()
        sepa_cp_pob = data.get('SEPA_CP_POBLACION_CIUDAD', '').strip()
        sepa_pais = data.get('SEPA_PAIS', '').strip()
        full_sepa_addr = f"{sepa_addr}, CP/Pob: {sepa_cp_pob}, Pais: {sepa_pais}" if sepa_addr else ""
        
        sheet_row_data = {
            'JUGADOR_NOMBRE': nombre,
            'JUGADOR_APELLIDOS': apellidos,
            'JUGADOR_COLEGIO': data.get('JUGADOR_COLEGIO', '').strip(),
            'JUGADOR_FECHA_NACIMIENTO': data.get('JUGADOR_FECHA_NACIMIENTO', '').strip(),
            'JUGADOR_EQUIPO_LETRA': combined_team,
            'JUGADOR_DOMICILIO_CP': f"{data.get('JUGADOR_DOMICILIO', '').strip()}, CP {data.get('JUGADOR_CP', '').strip()}",
            'JUGADOR_TELEFONO_MOVIL': f"Fijo: {data.get('JUGADOR_TEL_FIJO', '').strip()}, Movil: {data.get('JUGADOR_MOVIL', '').strip()}",
            'JUGADOR_EMAIL': data.get('JUGADOR_EMAIL', '').strip(),
            'FAMILIA_NOMBRE_PADRE': data.get('FAMILIA_NOMBRE_PADRE', '').strip(),
            'FAMILIA_NOMBRE_MADRE': data.get('FAMILIA_NOMBRE_MADRE', '').strip(),
            'PAGO_MODALIDAD': data.get('PAGO_MODALIDAD', '').strip(),
            'SEPA_NOMBRE_DEUDOR': data.get('SEPA_NOMBRE_DEUDOR', '').strip(),
            'SEPA_DIRECCION_DEUDOR': full_sepa_addr,
            'SEPA_SWIFT_BIC': data.get('SEPA_SWIFT_BIC', '').strip(),
            'SEPA_IBAN': data.get('SEPA_IBAN', '').strip(),
            'SEPA_TIPO_PAGO': data.get('SEPA_TIPO_PAGO', '').strip(),
            'AUTORIZA_TUTOR_NOMBRE': data.get('AUTORIZA_TUTOR_NOMBRE', '').strip(),
            'AUTORIZA_JUGADOR_NOMBRE': data.get('AUTORIZA_JUGADOR_NOMBRE', '').strip(),
            'AUTORIZA_JUGADOR_DNI': data.get('AUTORIZA_TUTOR_DNI', '').strip(),
            'CIERRE_FECHA_LOCALIDAD': data.get('CIERRE_FECHA_LOCALIDAD', '').strip(),
            'CIERRE_FIRMA_TUTOR': 'SI' if sig2 else 'NO',
            'ARCHIVO_URL': pdf_url
        }
        
        # 5. Append row to worksheet "DATOS JUGADORES"
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
            
        fila = [sheet_row_data.get(col, "") for col in COLUMNAS_DATOS_JUGADORES]
        sheet.append_row(fila, value_input_option='USER_ENTERED')
        
        return jsonify({"status": "success", "pdf_url": pdf_url})
    except Exception as e:
        print(f"Error en api_inscripcion_submit: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
