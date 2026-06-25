import csv
import os
import re
from io import StringIO
import json
import gspread
from gspread.utils import rowcol_to_a1
try:
    import pandas as pd
except ImportError:
    pd = None
try:
    import pdfplumber
except ImportError:
    pdfplumber = None
from flask import Blueprint, render_template, request, session, jsonify, redirect
from datetime import datetime # Import datetime for sorting

# Creamos el Blueprint para la sección financiera
financiero_bp = Blueprint('financiero', __name__)

FACTURAS_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'facturas')
os.makedirs(FACTURAS_FOLDER, exist_ok=True)

def normalizar_cabeceras(headers):
    """Normaliza las cabeceras del Excel para que coincidan con las claves del sistema."""
    # Aseguramos que todos los elementos sean strings y manejamos posibles valores nulos
    headers = [str(h).strip() if h is not None else "" for h in headers]
    # Limpieza profunda: quitamos tildes, símbolos especiales y espacios
    headers = [h.upper().replace('Ó','O').replace('Í','I').replace('É','E').replace('Á','A').replace('Ú','U').replace('º','') for h in headers]
    # Mapeo de variantes de nombres de columnas financieras para asegurar persistencia
    return [h.replace(' ', '_').replace('Nº', 'N').replace('TIPO_DE_PAGO', 'TIPO_PAGO').replace('FORMA_DE_PAGO', 'FORMA_PAGO').replace('JUGADOR', 'NOMBRE').replace('CANTIDAD', 'IMPORTE').replace('MONTO', 'IMPORTE') for h in headers]

def es_fila_vacia(row):
    """Verifica si una fila está realmente vacía (ignora espacios)."""
    return not any(str(cell).strip() for cell in row if cell)

def leer_hoja_limpia(client, nombre_excel, nombre_hoja):
    """Función de utilidad para leer datos de Excel de forma estructurada."""
    try:
        sheet = client.open(nombre_excel).worksheet(nombre_hoja)
        all_v = sheet.get_all_values()
        if not all_v: return []
        headers = normalizar_cabeceras(all_v[0])
        
        datos = []
        for row in all_v[1:]:
            if not es_fila_vacia(row):
                registro = {}
                for i, h in enumerate(headers):
                    val = row[i] if i < len(row) else ""
                    registro[h] = val
                datos.append(registro)
        return datos
    except Exception as e:
        print(f"Error leyendo hoja {nombre_hoja}: {e}")
        return []

def normalizar_concepto_interno(val):
    """Asegura que los conceptos numéricos tengan dos dígitos (01, 02...)."""
    s = str(val).strip().lower()
    return s.zfill(2) if s.isdigit() else s

def get_or_create_sheet(client, nombre_excel, sheet_name):
    """Obtiene una hoja de cálculo o la crea si no existe."""
    try:
        return client.open(nombre_excel).worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        print(f"Creando hoja '{sheet_name}'...")
        sheet = client.open(nombre_excel).add_worksheet(title=sheet_name, rows="100", cols="20")
        sheet.update('A1', [['KEY', 'VALUE']])
        return sheet

# Helper to parse date strings for sorting
def parse_date_for_sort(date_str):
    if not date_str or date_str == '-':
        return datetime.min
    s = str(date_str).strip()
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y %H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Fallback tolerante: extraer 3 grupos numéricos (día/mes/año en cualquier orden con separador / o -)
    # y corregir años mal tecleados (p.ej. 5 dígitos por error de escritura).
    m = re.match(r'^(\d{1,4})[/\-](\d{1,2})[/\-](\d{1,4})', s)
    if m:
        g1, g2, g3 = m.groups()
        try:
            if len(g1) >= 4:  # YYYY-MM-DD (recortamos años con dígitos de más)
                anio, mes, dia = int(g1[:4]), int(g2), int(g3)
            else:  # DD/MM/YYYY
                dia, mes, anio = int(g1), int(g2), int(g3[:4])
            return datetime(anio, mes, dia)
        except (ValueError, IndexError):
            pass
    return datetime.min

# Helper function to parse and format dates
def format_date_for_frontend(date_value):
    if not date_value:
        return ''
    try:
        # Try parsing common date formats
        if isinstance(date_value, datetime):
            return date_value.strftime('%d/%m/%Y')
        s_date = str(date_value).strip()
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
            try:
                dt_obj = datetime.strptime(s_date, fmt)
                return dt_obj.strftime('%d/%m/%Y')
            except ValueError:
                pass
        # Try to clean up common Excel date string artifacts
        if ' 00:00:00' in s_date:
            s_date = s_date.replace(' 00:00:00', '')
        return s_date
    except Exception:
        return str(date_value) # Fallback to original string if parsing fails

# Helper function to parse and format amounts
def parse_amount(amount_raw):
    if not amount_raw:
        return 0.0
    s_amount = str(amount_raw).replace('€', '').replace(' ', '').strip()
    # Manejo de separadores europeos (1.234,56)
    if ',' in s_amount and '.' in s_amount:
        s_amount = s_amount.replace('.', '').replace(',', '.')
    else:
        s_amount = s_amount.replace(',', '.')
    try:
        return float(s_amount)
    except ValueError:
        return 0.0 # Default to 0.0 if not a valid number

def get_friendly_concepto(concepto):
    """Mapea conceptos técnicos a nombres legibles para el historial."""
    meses_map = {
        '01': 'Ene', '02': 'Feb', '03': 'Mar', '04': 'Abr', '05': 'May', '06': 'Jun',
        '07': 'Jul', '08': 'Ago', '09': 'Sep', '10': 'Oct', '11': 'Nov', '12': 'Dic',
        'INSCRIPCION': 'Inscrp', 'PACK_ROPA': 'Ropa', 'EXTRA': 'Extra', 'EXTRA2': 'Extra2'
    }
    c_clean = str(concepto).strip().upper().replace('Ó', 'O').replace('Í', 'I')
    lookup = c_clean.replace('STAFF-', '') if c_clean.startswith('STAFF-') else c_clean
    prefix = "Staff-" if c_clean.startswith('STAFF-') else ""
    if lookup in meses_map:
        return f"{prefix}{meses_map[lookup]}"
    return str(concepto)


@financiero_bp.route('/financiero')
def financiero():
    # Importamos las variables globales de conexión desde app.py
    from app import client, NOMBRE_EXCEL
    usuario = session.get('usuario')
    if not usuario:
        return redirect('/')

    if session.get('permisos', {}).get('FINANCIERO') != 'SI':
        return "Acceso denegado", 403
    
    # 1. Obtener lista de equipos desde la pestaña EQUIPO (fuente de verdad para "todos")
    equipos = []
    try:
        sheet_eq = client.open(NOMBRE_EXCEL).worksheet("EQUIPO")
        rows_eq = sheet_eq.get_all_values()
        if rows_eq:
            h_eq = normalizar_cabeceras(rows_eq[0])
            idx_e = h_eq.index("EQUIPO") if "EQUIPO" in h_eq else 0
            equipos = sorted(list(set(str(r[idx_e]).strip() for r in rows_eq[1:] if len(r) > idx_e and r[idx_e].strip())))
    except:
        # Fallback si falla la pestaña EQUIPO
        jugadores_temp = leer_hoja_limpia(client, NOMBRE_EXCEL, "JUGADORES")
        equipos = sorted(list(set(j['EQUIPO'] for j in jugadores_temp if 'EQUIPO' in j)))

    jugadores = leer_hoja_limpia(client, NOMBRE_EXCEL, "JUGADORES")
    staff = leer_hoja_limpia(client, NOMBRE_EXCEL, "STAFF")

    return render_template('financiero/lista.html', 
                           usuario=usuario, 
                           equipos=equipos,
                           jugadores=jugadores,
                           staff=staff)

@financiero_bp.route('/api/presupuesto', methods=['GET', 'POST'])
def api_presupuesto():
    from app import client, NOMBRE_EXCEL
    
    try:
        if request.method == 'GET':
            # EL HISTORIAL AHORA ES ÚNICO: Se lee exclusivamente de la pestaña FINANCIERO para evitar duplicados
            historial_completo = leer_hoja_limpia(client, NOMBRE_EXCEL, "FINANCIERO")
            
            for a in historial_completo:
                # Identificar el tipo de origen basándonos en el pilar guardado
                pilar_raw = str(a.get('PILAR', '')).strip().upper()
                if 'CUOTA' in pilar_raw:
                    a['TIPO_ORIGEN'] = 'JUGADOR'
                elif 'STAFF' in pilar_raw:
                    a['TIPO_ORIGEN'] = 'STAFF'
                else:
                    a['TIPO_ORIGEN'] = 'MANUAL'
                
                # Asignar el ID real de la fila para permitir ediciones/borrados
                # Intentamos buscar variantes del ID de asiento
                a['REAL_ID'] = str(a.get('N_ASIENTO') or a.get('Nº_ASIENTO') or a.get('NASIENTO') or '0')
            
            # Filtrar por si acaso hay algún registro totalmente vacío que haya pasado los filtros previos
            historial_completo = [h for h in historial_completo if any(str(v).strip() for v in h.values() if v)]

            # Ordenamos por fecha ascendente para asignar IDs secuenciales correctos (el más viejo es el 1)
            historial_completo.sort(key=lambda x: parse_date_for_sort(x.get('FECHA')))

            # Asignar Nº ASIENTO secuencial global (000001...)
            for i, item in enumerate(historial_completo):
                item['Nº_ASIENTO_GLOBAL'] = str(i + 1).zfill(6)
            
            # Invertimos para que por defecto el GET devuelva los más nuevos arriba
            historial_completo.reverse()

            # Evitar cache de navegador
            resp = jsonify(historial_completo)
            resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            return resp
        
        datos = request.json
        pilar = datos.get('pilar')
        
        if pilar == "Cuotas":
            # Guardado específico en la nueva pestaña PAGOS JUGADORES
            try:
                sheet = client.open(NOMBRE_EXCEL).worksheet("PAGOS JUGADORES")
            except:
                sheet = client.open(NOMBRE_EXCEL).add_worksheet(title="PAGOS JUGADORES", rows="100", cols="20")
                sheet.append_row(["FECHA", "EQUIPO", "NOMBRE", "TIPO PAGO", "FORMA PAGO", "CONCEPTO", "PAGADO", "ESPERADO"])
            
            all_v = sheet.get_all_values()
            if not all_v:
                sheet.append_row(["FECHA", "EQUIPO", "NOMBRE", "TIPO PAGO", "FORMA PAGO", "CONCEPTO", "PAGADO", "ESPERADO"])
                all_v = sheet.get_all_values()
            
            headers_jug = normalizar_cabeceras(all_v[0])
            idx_eq = headers_jug.index('EQUIPO') if 'EQUIPO' in headers_jug else 1
            idx_nom = headers_jug.index('NOMBRE') if 'NOMBRE' in headers_jug else 2
            idx_con = headers_jug.index('CONCEPTO') if 'CONCEPTO' in headers_jug else 5
            idx_pag = headers_jug.index('PAGADO') if 'PAGADO' in headers_jug else 6
            idx_esp = headers_jug.index('ESPERADO') if 'ESPERADO' in headers_jug else 7
            idx_tp = headers_jug.index('TIPO_PAGO') if 'TIPO_PAGO' in headers_jug else 3
            idx_fp = headers_jug.index('FORMA_PAGO') if 'FORMA_PAGO' in headers_jug else 4

            idx_existente = -1
            if all_v:
                nom_b = str(datos.get('nombre')).strip().lower()
                eq_b = str(datos.get('equipo')).strip().lower()
                con_norm = normalizar_concepto_interno(datos.get('concepto'))
                
                for i, row in enumerate(all_v):
                    if i == 0: continue
                    if len(row) > max(idx_eq, idx_nom, idx_con):
                        r_eq = row[idx_eq].strip().lower()
                        r_nom = row[idx_nom].strip().lower()
                        r_con_norm = normalizar_concepto_interno(row[idx_con])
                        
                        if r_eq == eq_b and r_nom == nom_b and r_con_norm == con_norm:
                            idx_existente = i + 1
                            break

            if idx_existente != -1:
                # ACTUALIZAR: Evitamos duplicar la línea modificando la existente
                updates = [
                    {'range': f"'PAGOS JUGADORES'!{rowcol_to_a1(idx_existente, 1)}", 'values': [[datos.get('fecha')]]},
                    {'range': f"'PAGOS JUGADORES'!{rowcol_to_a1(idx_existente, idx_tp+1)}:{rowcol_to_a1(idx_existente, idx_fp+1)}", 'values': [[datos.get('tipo_pago'), datos.get('forma_pago')]]},
                    {'range': f"'PAGOS JUGADORES'!{rowcol_to_a1(idx_existente, idx_pag+1)}:{rowcol_to_a1(idx_existente, idx_esp+1)}", 'values': [[datos.get('importe'), datos.get('esperado')]]}
                ]
                sheet.spreadsheet.values_batch_update({
                    'valueInputOption': 'USER_ENTERED',
                    'data': updates
                })
            else:
                sheet.append_row([datos.get('fecha'), datos.get('equipo'), datos.get('nombre'), datos.get('tipo_pago'), datos.get('forma_pago'), datos.get('concepto'), datos.get('importe'), datos.get('esperado')])

            # Preparamos los metadatos para el registro maestro en FINANCIERO
            if not datos.get('descripcion'):
                friendly = get_friendly_concepto(datos.get('concepto'))
                datos['descripcion'] = f"Cuota {friendly}: {datos.get('nombre')}"
            datos['departamento'] = "Administración"

        elif pilar == "Pagos Staff":
            # Guardado específico en la nueva pestaña PAGOS STAFF
            try:
                sheet = client.open(NOMBRE_EXCEL).worksheet("PAGOS STAFF")
            except:
                sheet = client.open(NOMBRE_EXCEL).add_worksheet(title="PAGOS STAFF", rows="100", cols="20")
                sheet.append_row(["FECHA", "NOMBRE", "CONCEPTO", "PAGADO", "ESPERADO"])
            
            all_v = sheet.get_all_values()
            headers_staff = normalizar_cabeceras(all_v[0]) if all_v else []
            idx_nom = headers_staff.index('NOMBRE') if 'NOMBRE' in headers_staff else 1
            idx_con = headers_staff.index('CONCEPTO') if 'CONCEPTO' in headers_staff else 2
            idx_pag = headers_staff.index('PAGADO') if 'PAGADO' in headers_staff else 3
            idx_esp = headers_staff.index('ESPERADO') if 'ESPERADO' in headers_staff else 4

            idx_existente = -1
            if all_v:
                nom_b = str(datos.get('nombre')).strip().lower()
                con_norm = normalizar_concepto_interno(datos.get('concepto'))
                for i, row in enumerate(all_v):
                    if i == 0: continue
                    if len(row) > max(idx_nom, idx_con):
                        r_con_norm = normalizar_concepto_interno(row[idx_con])
                        if row[idx_nom].strip().lower() == nom_b and r_con_norm == con_norm:
                            idx_existente = i + 1
                            break

            if idx_existente != -1:
                updates = [
                    {'range': f"'PAGOS STAFF'!{rowcol_to_a1(idx_existente, 1)}", 'values': [[datos.get('fecha')]]},
                    {'range': f"'PAGOS STAFF'!{rowcol_to_a1(idx_existente, idx_pag+1)}:{rowcol_to_a1(idx_existente, idx_esp+1)}", 'values': [[datos.get('importe'), datos.get('esperado')]]}
                ]
                sheet.spreadsheet.values_batch_update({'valueInputOption': 'USER_ENTERED', 'data': updates})
            else:
                sheet.append_row([datos.get('fecha'), datos.get('nombre'), datos.get('concepto'), datos.get('importe'), datos.get('esperado')])

            # Preparamos los metadatos para el registro maestro en FINANCIERO
            if not datos.get('descripcion'):
                friendly = get_friendly_concepto(datos.get('concepto'))
                datos['descripcion'] = f"Pago Staff {friendly}: {datos.get('nombre')}"
            datos['departamento'] = "Administración"

        # REGISTRO MAESTRO EN PESTAÑA FINANCIERO (Homólogo)
        try:
            sheet_fin = client.open(NOMBRE_EXCEL).worksheet("FINANCIERO")
        except gspread.exceptions.WorksheetNotFound:
            sheet_fin = client.open(NOMBRE_EXCEL).add_worksheet(title="FINANCIERO", rows="1000", cols="11")
            sheet_fin.update('A1', [["FECHA", "Nº ASIENTO", "DEPARTAMENTO", "PILAR", "DESCRIPCION", "IMPORTE", "NOMBRE", "EQUIPO", "CONCEPTO", "ESPERADO", "FACTURA"]])

        all_fin = sheet_fin.get_all_values()
        headers_fin_raw = all_fin[0] if all_fin else []
        headers_fin_norm = normalizar_cabeceras(headers_fin_raw)
        
        if 'N_ASIENTO' not in headers_fin_norm:
            sheet_fin.update('A1', [["FECHA", "Nº ASIENTO", "DEPARTAMENTO", "PILAR", "DESCRIPCION", "IMPORTE", "NOMBRE", "EQUIPO", "CONCEPTO", "ESPERADO", "FACTURA"]])
            all_fin = sheet_fin.get_all_values()
            headers_fin_raw = all_fin[0]
            headers_fin_norm = normalizar_cabeceras(headers_fin_raw)
        else:
            missing = [c for c in ["NOMBRE", "EQUIPO", "CONCEPTO", "ESPERADO", "FACTURA"] if c not in headers_fin_norm]
            if missing:
                new_headers_raw = headers_fin_raw + missing
                sheet_fin.update('A1', [new_headers_raw])
                all_fin = sheet_fin.get_all_values()
                headers_fin_raw = all_fin[0]
                headers_fin_norm = normalizar_cabeceras(headers_fin_raw)

        # BÚSQUEDA ROBUSTA DE DUPLICADOS EN FINANCIERO (MAESTRA)
        idx_fin_existente = -1
        try:
            idx_p = headers_fin_norm.index('PILAR') if 'PILAR' in headers_fin_norm else -1
            idx_n = headers_fin_norm.index('NOMBRE') if 'NOMBRE' in headers_fin_norm else -1
            idx_c = headers_fin_norm.index('CONCEPTO') if 'CONCEPTO' in headers_fin_norm else -1
            
            if pilar in ["Cuotas", "Pagos Staff"] and idx_p != -1 and idx_n != -1 and idx_c != -1:
                nom_b = str(datos.get('nombre', '')).strip().lower()
                con_norm = normalizar_concepto_interno(datos.get('concepto'))

                for i, row in enumerate(all_fin):
                    if i == 0: continue
                    if len(row) > max(idx_p, idx_n, idx_c):
                        r_pilar = str(row[idx_p]).strip().upper()
                        r_nom = str(row[idx_n]).strip().lower()
                        r_con_norm = normalizar_concepto_interno(row[idx_c])
                        
                        if (pilar.upper() in r_pilar or r_pilar in pilar.upper()) and r_nom == nom_b and r_con_norm == con_norm:
                            idx_fin_existente = i + 1
                            break
        except Exception as e:
            print(f"Error buscando duplicado en FINANCIERO: {e}")

        if idx_fin_existente != -1:
            # Actualización robusta usando índices normalizados
            updates = []
            mapping = {'FECHA': datos.get('fecha'), 'DESCRIPCION': datos.get('descripcion'), 'IMPORTE': datos.get('importe'), 'ESPERADO': datos.get('esperado', 0)}
            for key, val in mapping.items():
                if key in headers_fin_norm:
                    col_idx = headers_fin_norm.index(key) + 1
                    updates.append({'range': f"'FINANCIERO'!{rowcol_to_a1(idx_fin_existente, col_idx)}", 'values': [[val]]})
            
            if updates:
                sheet_fin.spreadsheet.values_batch_update({'valueInputOption': 'USER_ENTERED', 'data': updates})
            
            idx_asi_col = headers_fin_norm.index('N_ASIENTO') if 'N_ASIENTO' in headers_fin_norm else 1
            return jsonify({"status": "success", "asiento": all_fin[idx_fin_existente-1][idx_asi_col]})

        # Intentamos usar el número de asiento proporcionado (de la carga masiva o manual)
        num_asiento = datos.get('n_asiento')
        if not num_asiento or str(num_asiento).strip() == "":
            max_asi = 0
            # Buscamos la columna de asiento de forma más flexible
            idx_asi = -1
            for variant in ['N_ASIENTO', 'Nº_ASIENTO', 'ASIENTO']:
                if variant in headers_fin_norm:
                    idx_asi = headers_fin_norm.index(variant)
                    break
            
            for row in all_fin[1:]:
                if idx_asi != -1 and len(row) > idx_asi and row[idx_asi]:
                    try:
                        val_limpio = str(row[idx_asi]).strip().replace('#','')
                        if val_limpio.isdigit():
                            max_asi = max(max_asi, int(val_limpio))
                    except: continue
            num_asiento = max_asi + 1
        else:
            try: 
                num_asiento = int(num_asiento)
            except: 
                num_asiento = datos.get('n_asiento') # Mantener como string si no es numérico

        mapeo_fila = {
            "FECHA": datos.get('fecha'),
            "N_ASIENTO": num_asiento,
            "DEPARTAMENTO": datos.get('departamento'),
            "PILAR": pilar,
            "DESCRIPCION": datos.get('descripcion') or f"Asiento {num_asiento}",
            "IMPORTE": datos.get('importe'),
            "NOMBRE": datos.get('nombre', ''),
            "EQUIPO": datos.get('equipo', ''),
            "CONCEPTO": datos.get('concepto', ''),
            "ESPERADO": datos.get('esperado', datos.get('importe', 0)),
            "FACTURA": datos.get('factura_estado', '')
        }

        nueva_fila_fin = []
        for h_norm in headers_fin_norm:
            nueva_fila_fin.append(mapeo_fila.get(h_norm, ""))

        sheet_fin.append_row(nueva_fila_fin)
        return jsonify({"status": "success", "asiento": num_asiento})
    except Exception as e:
        print(f"Error en api_presupuesto: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/asiento/subir_factura', methods=['POST'])
def api_subir_factura():
    from app import client, NOMBRE_EXCEL

    n_asiento = (request.form.get('n_asiento') or '').strip()
    archivo = request.files.get('factura')
    sin_factura = (request.form.get('sin_factura') or '').strip() == '1'
    if not n_asiento or (not archivo and not sin_factura):
        return jsonify({"status": "error", "message": "Falta el nº de asiento, el archivo o marcar 'sin factura'"}), 400

    try:
        if sin_factura:
            valor_factura = 'SIN_FACTURA'
        else:
            ext = os.path.splitext(archivo.filename)[1].lower()
            valor_factura = f"factura_{n_asiento}{ext}"
            archivo.save(os.path.join(FACTURAS_FOLDER, valor_factura))

        sheet_fin = client.open(NOMBRE_EXCEL).worksheet("FINANCIERO")
        all_fin = sheet_fin.get_all_values()
        headers_fin_norm = normalizar_cabeceras(all_fin[0])

        if 'FACTURA' not in headers_fin_norm:
            sheet_fin.update('A1', [all_fin[0] + ["FACTURA"]])
            all_fin = sheet_fin.get_all_values()
            headers_fin_norm = normalizar_cabeceras(all_fin[0])

        idx_asi = -1
        for variant in ['N_ASIENTO', 'Nº_ASIENTO', 'ASIENTO']:
            if variant in headers_fin_norm:
                idx_asi = headers_fin_norm.index(variant)
                break
        idx_fac = headers_fin_norm.index('FACTURA')

        for i, row in enumerate(all_fin):
            if i == 0:
                continue
            if idx_asi != -1 and len(row) > idx_asi and str(row[idx_asi]).strip().replace('#', '') == str(n_asiento):
                sheet_fin.update_cell(i + 1, idx_fac + 1, valor_factura)
                break

        return jsonify({"status": "success", "archivo": valor_factura})
    except Exception as e:
        print(f"Error subiendo factura: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/asiento/asignar_jugador', methods=['POST'])
def api_asignar_jugador_asiento():
    from app import client, NOMBRE_EXCEL

    datos = request.json or {}
    n_asiento = str(datos.get('n_asiento', '')).strip()
    accion = datos.get('accion')
    if not n_asiento or accion not in ('asignar', 'no_asignar'):
        return jsonify({"status": "error", "message": "Datos incompletos"}), 400

    try:
        sheet_fin = client.open(NOMBRE_EXCEL).worksheet("FINANCIERO")
        all_fin = sheet_fin.get_all_values()
        headers_fin_norm = normalizar_cabeceras(all_fin[0])

        missing = [c for c in ["NOMBRE", "EQUIPO", "CONCEPTO", "ASIGNACION"] if c not in headers_fin_norm]
        if missing:
            sheet_fin.update('A1', [all_fin[0] + missing])
            all_fin = sheet_fin.get_all_values()
            headers_fin_norm = normalizar_cabeceras(all_fin[0])

        idx_asi = -1
        for variant in ['N_ASIENTO', 'Nº_ASIENTO', 'ASIENTO']:
            if variant in headers_fin_norm:
                idx_asi = headers_fin_norm.index(variant)
                break

        idx_fila = -1
        fila_actual = None
        for i, row in enumerate(all_fin):
            if i == 0:
                continue
            if idx_asi != -1 and len(row) > idx_asi and str(row[idx_asi]).strip().replace('#', '') == n_asiento:
                idx_fila = i + 1
                fila_actual = row
                break
        if idx_fila == -1:
            return jsonify({"status": "error", "message": "Asiento no encontrado"}), 404

        idx_asignacion = headers_fin_norm.index('ASIGNACION')

        if accion == 'no_asignar':
            sheet_fin.update_cell(idx_fila, idx_asignacion + 1, 'NO_ASIGNAR')
            return jsonify({"status": "success"})

        equipo = (datos.get('equipo') or '').strip()
        nombre = (datos.get('nombre') or '').strip()
        concepto = (datos.get('concepto') or '').strip()
        if not equipo or not nombre or not concepto:
            return jsonify({"status": "error", "message": "Faltan equipo, jugador o concepto"}), 400

        idx_nombre = headers_fin_norm.index('NOMBRE')
        idx_equipo = headers_fin_norm.index('EQUIPO')
        idx_concepto = headers_fin_norm.index('CONCEPTO')
        idx_pilar = headers_fin_norm.index('PILAR') if 'PILAR' in headers_fin_norm else -1
        idx_importe = headers_fin_norm.index('IMPORTE') if 'IMPORTE' in headers_fin_norm else -1
        idx_descripcion = headers_fin_norm.index('DESCRIPCION') if 'DESCRIPCION' in headers_fin_norm else -1
        idx_fecha = headers_fin_norm.index('FECHA') if 'FECHA' in headers_fin_norm else -1

        importe = parse_amount(fila_actual[idx_importe]) if idx_importe != -1 and len(fila_actual) > idx_importe else 0
        fecha_actual = fila_actual[idx_fecha] if idx_fecha != -1 and len(fila_actual) > idx_fecha else ''

        updates = [
            {'range': f"'FINANCIERO'!{rowcol_to_a1(idx_fila, idx_nombre + 1)}", 'values': [[nombre]]},
            {'range': f"'FINANCIERO'!{rowcol_to_a1(idx_fila, idx_equipo + 1)}", 'values': [[equipo]]},
            {'range': f"'FINANCIERO'!{rowcol_to_a1(idx_fila, idx_concepto + 1)}", 'values': [[concepto]]},
            {'range': f"'FINANCIERO'!{rowcol_to_a1(idx_fila, idx_asignacion + 1)}", 'values': [['']]},
        ]
        if idx_pilar != -1:
            updates.append({'range': f"'FINANCIERO'!{rowcol_to_a1(idx_fila, idx_pilar + 1)}", 'values': [['Cuotas']]})
        sheet_fin.spreadsheet.values_batch_update({'valueInputOption': 'USER_ENTERED', 'data': updates})

        # Reflejar también en PAGOS JUGADORES, igual que en el alta normal de un asiento
        try:
            sheet_pj = client.open(NOMBRE_EXCEL).worksheet("PAGOS JUGADORES")
        except gspread.exceptions.WorksheetNotFound:
            sheet_pj = client.open(NOMBRE_EXCEL).add_worksheet(title="PAGOS JUGADORES", rows="100", cols="20")
            sheet_pj.append_row(["FECHA", "EQUIPO", "NOMBRE", "TIPO PAGO", "FORMA PAGO", "CONCEPTO", "PAGADO", "ESPERADO"])

        all_pj = sheet_pj.get_all_values()
        headers_pj = normalizar_cabeceras(all_pj[0]) if all_pj else []
        idx_eq_pj = headers_pj.index('EQUIPO') if 'EQUIPO' in headers_pj else 1
        idx_nom_pj = headers_pj.index('NOMBRE') if 'NOMBRE' in headers_pj else 2
        idx_con_pj = headers_pj.index('CONCEPTO') if 'CONCEPTO' in headers_pj else 5
        idx_pag_pj = headers_pj.index('PAGADO') if 'PAGADO' in headers_pj else 6

        idx_existente = -1
        con_norm = normalizar_concepto_interno(concepto)
        for i, row in enumerate(all_pj):
            if i == 0:
                continue
            if len(row) > max(idx_eq_pj, idx_nom_pj, idx_con_pj):
                if (row[idx_eq_pj].strip().lower() == equipo.lower() and
                        row[idx_nom_pj].strip().lower() == nombre.lower() and
                        normalizar_concepto_interno(row[idx_con_pj]) == con_norm):
                    idx_existente = i + 1
                    break

        if idx_existente != -1:
            sheet_pj.update_cell(idx_existente, idx_pag_pj + 1, importe)
        else:
            nueva = [''] * len(headers_pj)
            nueva[0] = fecha_actual
            nueva[idx_eq_pj] = equipo
            nueva[idx_nom_pj] = nombre
            nueva[idx_con_pj] = concepto
            nueva[idx_pag_pj] = importe
            sheet_pj.append_row(nueva)

        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error asignando jugador a asiento: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/borrar_pago', methods=['POST'])
def api_borrar_pago():
    from app import client, NOMBRE_EXCEL
    datos = request.json
    try:
        equipo_buscado = str(datos.get('equipo') or "").strip().lower()
        is_staff = equipo_buscado == "staff" or datos.get('pilar') == "Pagos Staff"
        sheet_name = "PAGOS STAFF" if is_staff else "PAGOS JUGADORES"
        nombre_buscado = str(datos.get('nombre') or "").strip().lower()
        sheet = client.open(NOMBRE_EXCEL).worksheet(sheet_name)
        all_v = sheet.get_all_values()
        
        idx_to_delete = -1
        concepto_buscado = normalizar_concepto_interno(datos.get('concepto'))

        for i, row in enumerate(all_v):
            if i == 0: continue
            if is_staff:
                if len(row) >= 3:
                    r_nombre = str(row[1]).strip().lower()
                    r_concepto = normalizar_concepto_interno(row[2])
                    if r_nombre == nombre_buscado and r_concepto == concepto_buscado:
                        idx_to_delete = i + 1
                        break
            else:
                if len(row) >= 6:
                    # Leemos columnas 1 (Equipo), 2 (Nombre) y 5 (Concepto)
                    r_equipo = str(row[1]).strip().lower()
                    r_nombre = str(row[2]).strip().lower()
                    r_concepto = normalizar_concepto_interno(row[5])
                    
                    if r_equipo == equipo_buscado and r_nombre == nombre_buscado and r_concepto == concepto_buscado:
                        idx_to_delete = i + 1
                        break
        
        if idx_to_delete != -1:
            sheet.delete_rows(idx_to_delete)
            
            # TAMBIÉN BORRAR DE FINANCIERO para mantener sincronía total
            sheet_fin = client.open(NOMBRE_EXCEL).worksheet("FINANCIERO")
            all_fin = sheet_fin.get_all_values()
            headers = normalizar_cabeceras(all_fin[0])
            idx_n = headers.index('NOMBRE') if 'NOMBRE' in headers else -1
            idx_c = headers.index('CONCEPTO') if 'CONCEPTO' in headers else -1
            
            if idx_n != -1 and idx_c != -1:
                for i, row in enumerate(all_fin):
                    if i > 0 and len(row) > max(idx_n, idx_c):
                        r_nom = str(row[idx_n]).strip().lower()
                        r_con = normalizar_concepto_interno(row[idx_c])
                        if r_nom == nombre_buscado and r_con == concepto_buscado:
                            sheet_fin.delete_rows(i + 1)
                            break
            else:
                print("Aviso: No se pudo borrar el homólogo en FINANCIERO. Columnas NOMBRE o CONCEPTO no encontradas.")
            return jsonify({"status": "success"})
            
        return jsonify({"status": "error", "message": "No se encontró el registro"}), 404
    except Exception as e:
        print(f"Error api_borrar_pago: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/actualizar_pago', methods=['POST'])
def api_actualizar_pago():
    from app import client, NOMBRE_EXCEL
    datos = request.json
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("PAGOS JUGADORES")
        all_v = sheet.get_all_values()
        if not all_v: return jsonify({"status":"error"}), 404
        headers = normalizar_cabeceras(all_v[0])
        
        idx_to_update = -1
        nombre_buscado = str(datos.get('nombre', '')).strip().lower()
        equipo_buscado = str(datos.get('equipo', '')).strip().lower()
        concepto_buscado = normalizar_concepto_interno(datos.get('concepto'))

        idx_eq = headers.index('EQUIPO') if 'EQUIPO' in headers else 1
        idx_nom = headers.index('NOMBRE') if 'NOMBRE' in headers else 2
        idx_con = headers.index('CONCEPTO') if 'CONCEPTO' in headers else 5
        idx_pag = (headers.index('PAGADO') + 1) if 'PAGADO' in headers else 7

        for i, row in enumerate(all_v):
            if i == 0: continue
            if len(row) > max(idx_eq, idx_nom, idx_con):
                r_equipo = str(row[idx_eq]).strip().lower()
                r_nombre = str(row[idx_nom]).strip().lower()
                r_concepto = normalizar_concepto_interno(row[idx_con])
                
                if r_equipo == equipo_buscado and r_nombre == nombre_buscado and r_concepto == concepto_buscado:
                    idx_to_update = i + 1
                    break
        
        if idx_to_update != -1:
            sheet.update_cell(idx_to_update, idx_pag, datos.get('importe'))

            # TAMBIÉN ACTUALIZAR EN FINANCIERO para que se vea en el historial
            try:
                sheet_fin = client.open(NOMBRE_EXCEL).worksheet("FINANCIERO")
                all_fin = sheet_fin.get_all_values()
                headers = normalizar_cabeceras(all_fin[0])
                idx_n = headers.index('NOMBRE') if 'NOMBRE' in headers else -1
                idx_c = headers.index('CONCEPTO') if 'CONCEPTO' in headers else -1
                idx_i = headers.index('IMPORTE') if 'IMPORTE' in headers else -1
                idx_d = headers.index('DESCRIPCION') if 'DESCRIPCION' in headers else -1
                
                if idx_n != -1 and idx_c != -1 and idx_i != -1:
                    for i, row in enumerate(all_fin):
                        if i > 0 and len(row) > max(idx_n, idx_c, idx_i):
                            r_con = normalizar_concepto_interno(row[idx_c])
                            if row[idx_n].strip().lower() == nombre_buscado and r_con == concepto_buscado:
                                sheet_fin.update_cell(i + 1, idx_i + 1, datos.get('importe'))
                                if idx_d != -1 and datos.get('descripcion'):
                                    sheet_fin.update_cell(i + 1, idx_d + 1, datos['descripcion'])
                                break
            except Exception as e:
                print(f"Aviso: No se pudo actualizar el homólogo en FINANCIERO: {e}")

            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": "No se encontró el registro"}), 404
    except Exception as e:
        print(f"Error api_actualizar_pago: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/anadir_staff', methods=['POST'])
def api_anadir_staff():
    from app import client, NOMBRE_EXCEL
    datos = request.json
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("STAFF")
        all_v = sheet.get_all_values()
        if not all_v:
            headers = ["CARGO", "NOMBRE", "TELEFONO", "EQUIPO", "DIAS ENTRENAMIENTO"]
            sheet.append_row(headers)
            all_v = [headers]
        
        headers_norm = normalizar_cabeceras(all_v[0])
        fila = [""] * len(headers_norm)
        mapping = {
            'CARGO': datos.get('cargo'),
            'NOMBRE': datos.get('nombre'),
            'TELEFONO': datos.get('telefono'),
            'EQUIPO': datos.get('equipo'),
            'DIAS_ENTRENAMIENTO': datos.get('dias_entrenamiento')
        }
        
        for i, h in enumerate(headers_norm):
            if h in mapping:
                fila[i] = mapping[h]
        
        sheet.append_row(fila)
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error api_anadir_staff: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/eliminar_staff', methods=['POST'])
def api_eliminar_staff():
    from app import client, NOMBRE_EXCEL
    nombre = request.json.get('nombre')
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("STAFF")
        # Buscamos en la columna B (Nombre)
        celda = sheet.find(nombre.strip(), in_column=2)
        sheet.delete_rows(celda.row)
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error api_eliminar_staff: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/actualizar_staff', methods=['POST'])
def api_actualizar_staff():
    from app import client, NOMBRE_EXCEL
    datos = request.json
    nombre_antiguo = str(datos.get('nombre_antiguo', '')).strip()
    nombre_nuevo = str(datos.get('nombre_nuevo', '')).strip()
    cargo_nuevo = str(datos.get('cargo_nuevo', '')).strip()
    tel_nuevo = str(datos.get('telefono', '')).strip()
    equipo_nuevo = str(datos.get('equipo', '')).strip()
    dias_nuevo = str(datos.get('dias_entrenamiento', '')).strip()
    
    try:
        # 1. Actualizar en la pestaña STAFF
        sheet_staff = client.open(NOMBRE_EXCEL).worksheet("STAFF")
        all_v = sheet_staff.get_all_values()
        if not all_v: return jsonify({"status": "error", "message": "Hoja vacía"}), 404
        
        headers_norm = normalizar_cabeceras(all_v[0])
        def get_col_idx(name):
            try: return headers_norm.index(name) + 1
            except: return None

        idx_nom = get_col_idx('NOMBRE') or 2
        idx_car = get_col_idx('CARGO') or 1
        idx_tel = get_col_idx('TELEFONO') or 3
        idx_equ = get_col_idx('EQUIPO') or 4
        idx_dia = get_col_idx('DIAS_ENTRENAMIENTO') or 5

        celda = sheet_staff.find(nombre_antiguo, in_column=idx_nom)
        if celda:
            if idx_car: sheet_staff.update_cell(celda.row, idx_car, cargo_nuevo)
            if idx_nom: sheet_staff.update_cell(celda.row, idx_nom, nombre_nuevo)
            if idx_tel: sheet_staff.update_cell(celda.row, idx_tel, tel_nuevo)
            if idx_equ: sheet_staff.update_cell(celda.row, idx_equ, equipo_nuevo)
            if idx_dia: sheet_staff.update_cell(celda.row, idx_dia, dias_nuevo)
            
            # 2. Si el nombre cambió, actualizar en PAGOS STAFF para mantener integridad de los registros
            if nombre_antiguo.lower() != nombre_nuevo.lower():
                sheet_pagos = client.open(NOMBRE_EXCEL).worksheet("PAGOS STAFF")
                all_v = sheet_pagos.get_all_values()
                updates = []
                for i, row in enumerate(all_v):
                    if i == 0: continue
                    if len(row) > 1 and row[1].strip().lower() == nombre_antiguo.lower():
                        updates.append({
                            'range': f"'PAGOS STAFF'!B{i+1}",
                            'values': [[nombre_nuevo]]
                        })
                if updates:
                    sheet_pagos.spreadsheet.values_batch_update({'valueInputOption': 'USER_ENTERED', 'data': updates})
            
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": "No se encontró el staff"}), 404
    except Exception as e:
        print(f"Error api_actualizar_staff: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/limpiar_historial', methods=['POST'])
def api_limpiar_historial():
    from app import client, NOMBRE_EXCEL
    data = request.json
    tipo = data.get('tipo') 
    try:
        spreadsheet = client.open(NOMBRE_EXCEL)
        hojas_a_limpiar = [tipo]
        if tipo == 'HISTORIAL_UNIFICADO':
            hojas_a_limpiar = ["FINANCIERO", "PAGOS JUGADORES", "PAGOS STAFF"]
            
        for nombre_hoja in hojas_a_limpiar:
            try:
                sheet = spreadsheet.worksheet(nombre_hoja)
                all_values = sheet.get_all_values()
                rows = len(all_values)
                if rows > 1:
                    sheet.delete_rows(2, rows)
            except: continue
            
        return jsonify({"status": "success", "message": "El historial seleccionado ha sido limpiado correctamente."})
    except Exception as e:
        print(f"Error api_limpiar_historial: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/operacion_asiento', methods=['POST'])
def api_operacion_asiento():
    from app import client, NOMBRE_EXCEL
    data = request.json
    accion = data.get('accion')
    asiento_id = str(data.get('id')) # REAL_ID (ej: "5" o "P-1")
    tipo_origen = data.get('tipo_origen')

    try:
        # Mapeo de hojas
        hojas_map = {'JUGADOR': "PAGOS JUGADORES", 'STAFF': "PAGOS STAFF", 'MANUAL': "FINANCIERO"}
        nombre_hoja = hojas_map.get(tipo_origen, "FINANCIERO")
        
        sheet = client.open(NOMBRE_EXCEL).worksheet(nombre_hoja)
        all_v = sheet.get_all_values()
        fila_idx = -1

        if tipo_origen in ['JUGADOR', 'STAFF']:
            # Búsqueda robusta por contenido (Fecha, Nombre, Concepto) enviada desde el front
            # Esto evita borrar la fila equivocada si el orden en el Excel ha cambiado
            # Columnas según hoja: 
            # JUGADOR: FECHA(0), EQUIPO(1), NOMBRE(2), TIPO(3), FORMA(4), CONCEPTO(5), PAGADO(6), ESPERADO(7)
            # STAFF: FECHA(0), NOMBRE(1), CONCEPTO(2), PAGADO(3), ESPERADO(4)
            col_n = 2 if tipo_origen == 'JUGADOR' else 1
            col_c = 5 if tipo_origen == 'JUGADOR' else 2
            
            for i, row in enumerate(all_v):
                if i == 0: continue
                if len(row) > max(col_n, col_c):
                    f_match = (row[0].strip() == data.get('fecha_orig', '').strip()) or (data.get('fecha_orig') == '-')
                    n_match = row[col_n].strip().lower() == data.get('nombre_orig', '').strip().lower()
                    c_match = normalizar_concepto_interno(row[col_c]) == normalizar_concepto_interno(data.get('concepto_orig'))
                    
                    if f_match and n_match and c_match:
                        fila_idx = i + 1
                        break
            
            # Fallback al índice si falla la búsqueda por contenido (compatibilidad)
            if fila_idx == -1 and '-' in asiento_id:
                try:
                    idx_rel = int(asiento_id.split('-')[1])
                    # Validamos que al menos coincida el nombre en esa fila
                    if idx_rel < len(all_v) and all_v[idx_rel][col_n].strip().lower() == data.get('nombre_orig', '').strip().lower():
                        fila_idx = idx_rel + 1
                except: pass

        else: # MANUAL
            headers = normalizar_cabeceras(all_v[0])

        # SIEMPRE operamos primero sobre la pestaña maestra FINANCIERO para el historial
        sheet_fin = client.open(NOMBRE_EXCEL).worksheet("FINANCIERO")
        all_fin = sheet_fin.get_all_values()
        headers_fin = normalizar_cabeceras(all_fin[0])
        
        idx_asi = -1
        for v in ['Nº_ASIENTO', 'N_ASIENTO', 'ASIENTO']:
            if v in headers_fin:
                idx_asi = headers_fin.index(v)
                break
        
        fila_fin_idx = -1
        for i, row in enumerate(all_fin):
            if i > 0 and len(row) > idx_asi:
                # Comparación flexible de IDs (ignora símbolos y ceros a la izquierda)
                val_row = str(row[idx_asi]).strip().replace('#','')
                val_target = str(asiento_id).strip().replace('#','')
                if val_row == val_target or (val_row.isdigit() and val_target.isdigit() and int(val_row) == int(val_target)):
                    fila_fin_idx = i + 1
                    break

        if fila_fin_idx == -1:
            return jsonify({"status": "error", "message": "Asiento no encontrado en el historial"}), 404

        if accion == 'borrar':
            sheet_fin.delete_rows(fila_fin_idx)
            # También borrar de la hoja específica si corresponde
            if tipo_origen in ['JUGADOR', 'STAFF']:
                sheet_esp = client.open(NOMBRE_EXCEL).worksheet("PAGOS STAFF" if tipo_origen == 'STAFF' else "PAGOS JUGADORES")
                # Búsqueda por contenido para borrar en la sub-hoja
                all_esp = sheet_esp.get_all_values()
                col_n = 2 if tipo_origen == 'JUGADOR' else 1
                col_c = 5 if tipo_origen == 'JUGADOR' else 2
                for i, row in enumerate(all_esp):
                    if i > 0 and len(row) > max(col_n, col_c) and row[col_n].strip().lower() == data.get('nombre_orig','').strip().lower() and normalizar_concepto_interno(row[col_c]) == normalizar_concepto_interno(data.get('concepto_orig')):
                        sheet_esp.delete_rows(i + 1)
                        break
        
        elif accion == 'editar':
            # Actualización maestra en FINANCIERO (usa batch para ser atómico)
            mapeo_fin = {'fecha': 'FECHA', 'departamento': 'DEPARTAMENTO', 'pilar': 'PILAR', 'descripcion': 'DESCRIPCION', 'importe': 'IMPORTE'}
            updates_fin = []
            for k_json, k_head in mapeo_fin.items():
                if k_json in data and k_head in headers_fin:
                    updates_fin.append({'range': f"'FINANCIERO'!{rowcol_to_a1(fila_fin_idx, headers_fin.index(k_head)+1)}", 'values': [[data[k_json]]]})
            
            if tipo_origen in ['JUGADOR', 'STAFF'] and 'ESPERADO' in headers_fin:
                updates_fin.append({'range': f"'FINANCIERO'!{rowcol_to_a1(fila_fin_idx, headers_fin.index('ESPERADO')+1)}", 'values': [[data.get('esperado', 0)]]})
            
            sheet_fin.spreadsheet.values_batch_update({'valueInputOption': 'USER_ENTERED', 'data': updates_fin})

            # Sincronizar con sub-hoja específica
            if tipo_origen in ['JUGADOR', 'STAFF']:
                sheet_esp = client.open(NOMBRE_EXCEL).worksheet("PAGOS STAFF" if tipo_origen == 'STAFF' else "PAGOS JUGADORES")
                all_esp = sheet_esp.get_all_values()
                col_n = 2 if tipo_origen == 'JUGADOR' else 1
                col_c = 5 if tipo_origen == 'JUGADOR' else 2
                for i, row in enumerate(all_esp):
                    if i > 0 and len(row) > max(col_n, col_c) and row[col_n].strip().lower() == data.get('nombre_orig','').strip().lower() and normalizar_concepto_interno(row[col_c]) == normalizar_concepto_interno(data.get('concepto_orig')):
                        if tipo_origen == 'JUGADOR':
                            sheet_esp.update(f'A{i+1}', [[data['fecha']]]) # Col A
                            sheet_esp.update(f'G{i+1}:H{i+1}', [[data['importe'], data.get('esperado', 0)]])
                        else:
                            sheet_esp.update(f'A{i+1}', [[data['fecha']]])
                            sheet_esp.update(f'D{i+1}:E{i+1}', [[data['importe'], data.get('esperado', 0)]])
                        break

        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/anadir_jugador_fin', methods=['POST'])
def api_anadir_jugador_fin():
    from app import client, NOMBRE_EXCEL
    datos = request.json
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        headers_raw = sheet.row_values(1)
        headers_norm = normalizar_cabeceras(headers_raw)
        
        # Asegurar que las columnas existen para el registro financiero
        updated_headers = False
        if "FORMA_PAGO" not in headers_norm:
            headers_raw.append("FORMA_PAGO")
            updated_headers = True
        if "TIPO_PAGO" not in headers_norm:
            headers_raw.append("TIPO_PAGO")
            updated_headers = True

        if updated_headers:
            sheet.update('A1', [headers_raw])
            headers_norm = normalizar_cabeceras(headers_raw)

        row_to_add = [""] * len(headers_norm)
        for i, h in enumerate(headers_norm):
            if h == "NOMBRE": row_to_add[i] = datos.get('nombre')
            if h == "EQUIPO": row_to_add[i] = datos.get('equipo')
            if h == "FORMA_PAGO": row_to_add[i] = datos.get('forma_pago')
            if h == "TIPO_PAGO": row_to_add[i] = datos.get('tipo_pago')
            
        sheet.append_row(row_to_add)
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error api_anadir_jugador_fin: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/config_financiera', methods=['GET', 'POST'])
def api_config_financiera():
    from app import client, NOMBRE_EXCEL
    try:
        config_sheet = get_or_create_sheet(client, NOMBRE_EXCEL, "CONFIGURACION")
        
        if request.method == 'GET':
            all_configs = config_sheet.get_all_values()
            inscripciones = {}
            formas_pago = []
            cuotas_mes = {}
            extra_names = {"EXTRA": "EXTRA", "EXTRA2": "EXTRA 2", "STAFF_OTROS": "OTROS"}

            for row in all_configs:
                if len(row) >= 2:
                    key = row[0].strip()
                    value = row[1].strip()
                    if key == "INSCRIPCIONES_EQUIPO":
                        try: inscripciones = json.loads(value)
                        except json.JSONDecodeError: pass
                    elif key == "FORMAS_PAGO":
                        try: formas_pago = json.loads(value)
                        except json.JSONDecodeError: pass
                    elif key == "CUOTAS_MES":
                        try: cuotas_mes = json.loads(value)
                        except json.JSONDecodeError: pass
                    elif key == "EXTRA_NAMES":
                        try: extra_names = json.loads(value)
                        except json.JSONDecodeError: pass
            
            # Valores por defecto si no se encuentran en la hoja
            if not formas_pago:
                formas_pago = [
                    {"nombre": "Efectivo", "total": 490, "modalidad": "mensual", "cuota": 49, "meses": 10, "tipo": "Efectivo"},
                    {"nombre": "Domiciliación", "total": 490, "modalidad": "mensual", "cuota": 49, "meses": 10, "tipo": "Domiciliación"},
                    {"nombre": "Transferencia", "total": 490, "modalidad": "mensual", "cuota": 49, "meses": 10, "tipo": "Transferencia"}
                ]

            return jsonify({"inscripciones": inscripciones, "formas_pago": formas_pago, "cuotas_mes": cuotas_mes, "extra_names": extra_names})
        
        elif request.method == 'POST':
            data = request.json
            # Guardado robusto de configuración en dos columnas
            config_sheet.update('A2', [
                ['INSCRIPCIONES_EQUIPO', json.dumps(data.get('inscripciones', {}))],
                ['FORMAS_PAGO', json.dumps(data.get('formas_pago', []))],
                ['CUOTAS_MES', json.dumps(data.get('cuotas_mes', {}))],
                ['EXTRA_NAMES', json.dumps(data.get('extra_names', {}))]
            ], value_input_option='RAW')
            return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error api_config_financiera: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/bulk_update_jugadores', methods=['POST'])
def api_bulk_update_jugadores():
    from app import client, NOMBRE_EXCEL
    data = request.json # { "updates": [{"nombre": "...", "campo": "...", "valor": "..."}] }
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("JUGADORES")
        all_rows = sheet.get_all_values()
        headers_raw = list(all_rows[0])
        headers_norm = normalizar_cabeceras(headers_raw)

        # Asegurar que las columnas destino existen
        campos_necesarios = set(item['campo'].upper() for item in data.get('updates', []))
        header_modificado = False
        for campo in campos_necesarios:
            if campo not in headers_norm:
                headers_raw.append(campo.replace('_', ' ')) # Convierte TIPO_PAGO a TIPO PAGO para el Excel
                header_modificado = True
        
        if header_modificado:
            sheet.update('A1', [headers_raw])
            all_rows = sheet.get_all_values() # Refrescar datos con nuevas columnas
            headers_norm = normalizar_cabeceras(all_rows[0])
        
        if "NOMBRE" not in headers_norm: return jsonify({"status":"error", "message": "Columna NOMBRE no encontrada"}), 500
        idx_nom = headers_norm.index("NOMBRE")
        
        updates = []
        for item in data.get('updates', []):
            nombre_clean = item['nombre'].strip().lower()
            campo = item['campo'].upper() # Viene como 'TIPO_PAGO'
            if campo not in headers_norm: continue
            col_idx = headers_norm.index(campo) + 1
            
            for i, row in enumerate(all_rows):
                if i == 0: continue
                if len(row) > idx_nom and row[idx_nom].strip().lower() == nombre_clean:
                    updates.append({
                        'range': f"'JUGADORES'!{rowcol_to_a1(i + 1, col_idx)}",
                        'values': [[item['valor']]]
                    })
                    break
        
        if updates:
            sheet.spreadsheet.values_batch_update({'valueInputOption': 'USER_ENTERED', 'data': updates})
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error en bulk_update_jugadores: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/carga_masiva_presupuesto', methods=['POST'])
def api_carga_masiva_presupuesto():
    """Endpoint para cargar múltiples movimientos financieros desde un CSV."""
    def detectar_importe(fila_dict):
        """Busca el importe en múltiples variantes de nombres de columna."""
        # Prioridad de búsqueda de columnas de dinero (incluye términos bancarios comunes)
        variantes = ['IMPORTE', 'VALOR', 'MONTO', 'TOTAL', 'DEBE', 'CARGO', 'HABER', 'ABONO', 'CUANTIA', 'SUMA', 'SALDO']
        
        for v in variantes:
            val = fila_dict.get(v)
            if val is not None and str(val).strip() not in ['', '0', '0.0', '0,00']:
                num = parse_amount(val)
                if num != 0:
                    # Si viene de una columna de 'gasto' (Debe/Cargo) y es positivo, lo convertimos en negativo
                    if v in ['DEBE', 'CARGO'] and num > 0:
                        return -num
                    return num
        return 0.0

    try:
        if 'file' not in request.files:
            return jsonify({"status": "error", "message": "No se recibió ningún archivo"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"status": "error", "message": "Archivo no seleccionado"}), 400

        file.seek(0)
        datos_leidos = []
        filename = file.filename.lower()

        if filename.endswith('.csv'):
            raw_data = file.read()
            try:
                content = raw_data.decode("UTF-8-SIG")
            except UnicodeDecodeError:
                content = raw_data.decode("latin-1")

            lines = content.splitlines()
            if not lines:
                return jsonify({"status": "error", "message": "El archivo está vacío"}), 400

            delimiter = ';' if ';' in lines[0] else ','
            reader = csv.DictReader(lines, delimiter=delimiter)

            for row in reader:
                # Limpieza de claves para evitar fallos si hay columnas vacías en CSV
                keys = [str(k).strip() for k in row.keys() if k]
                norm_keys = normalizar_cabeceras(keys)
                mapping = {norm_keys[i]: row[keys[i]] for i in range(len(keys))}

                imp = detectar_importe(mapping)
                asiento_val = mapping.get('ASIENTO', mapping.get('N_ASIENTO', mapping.get('Nº_ASIENTO', '')))
                datos_leidos.append({
                    "FECHA": format_date_for_frontend(mapping.get('FECHA', '')),
                    "ASIENTO": str(asiento_val),
                    "DESCRIPCION": str(mapping.get('CONCEPTO', mapping.get('DESCRIPCION', 'Importación Masiva'))),
                    "IMPORTE": imp,
                    "NOMBRE": str(mapping.get('NOMBRE', '')),
                    "EQUIPO": str(mapping.get('EQUIPO', '')),
                    "CONCEPTO": str(mapping.get('CONCEPTO', ''))
                })

        elif filename.endswith(('.xls', '.xlsx', '.xlsm')):
            if pd is None:
                return jsonify({"status": "error", "message": "El servidor no tiene instalada la librería 'pandas'. Ejecuta: pip install pandas openpyxl"}), 500
            try:
                file.seek(0)
                df = pd.read_excel(file).fillna("") 
                df.columns = normalizar_cabeceras(df.columns.tolist())
                
                for _, row in df.iterrows():
                    row_dict = row.to_dict()
                    imp = detectar_importe(row_dict)
                    asiento_val = row_dict.get('ASIENTO') or row_dict.get('N_ASIENTO') or row_dict.get('Nº_ASIENTO') or ''
                    datos_leidos.append({
                        "FECHA": format_date_for_frontend(row.get('FECHA', '')),
                        "ASIENTO": str(asiento_val),
                        "DESCRIPCION": str(row.get('CONCEPTO', row.get('DESCRIPCION', 'Importación Excel'))).strip(),
                        "IMPORTE": imp,
                        "NOMBRE": str(row.get('NOMBRE', '')),
                        "EQUIPO": str(row.get('EQUIPO', '')),
                        "CONCEPTO": str(row.get('CONCEPTO', ''))
                    })
            except ImportError:
                return jsonify({"status": "error", "message": "El servidor necesita la librería 'pandas' y 'openpyxl' para procesar archivos Excel."}), 500
            except Exception as e:
                if "openpyxl" in str(e) or "xlrd" in str(e):
                    return jsonify({"status": "error", "message": "El servidor necesita las librerías 'pandas', 'openpyxl' y 'xlrd' para procesar archivos Excel."}), 500
                return jsonify({"status": "error", "message": f"Error al leer el archivo Excel: {str(e)}"}), 500

        elif filename.endswith('.pdf'):
            # Para PDF intentamos una extracción genérica de texto
            # Nota: Requiere pdfplumber o similar. Si no está, devolvemos error informativo.
            try:
                import pdfplumber
                try:
                    with pdfplumber.open(file) as pdf:
                        for page in pdf.pages:
                            table = page.extract_table()
                            if table:
                                for row in table[1:]: # Saltamos cabecera del PDF
                                    if any(row):
                                        datos_leidos.append({
                                            "FECHA": format_date_for_frontend(row[0] if len(row) > 0 else ''),
                                            "DESCRIPCION": row[1] if len(row) > 1 else 'Importación PDF',
                                            "IMPORTE": parse_amount(row[-1] if len(row) > 0 else '0')
                                        })
                except Exception as pdf_e:
                    return jsonify({"status": "error", "message": f"Error al procesar el archivo PDF: {str(pdf_e)}. Asegúrate de que el PDF es válido y no está protegido."}), 500
            except ImportError:
                return jsonify({"status": "error", "message": "El servidor no tiene instalada la librería para procesar PDFs (pdfplumber)."}), 500

        return jsonify({"status": "success", "data": datos_leidos})
    except Exception as e:
        print(f"Error fatal en carga_masiva: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500