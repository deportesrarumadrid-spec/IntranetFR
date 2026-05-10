import json
import gspread
from gspread.utils import rowcol_to_a1
from flask import Blueprint, render_template, request, session, jsonify
from datetime import datetime # Import datetime for sorting

# Creamos el Blueprint para la sección financiera
financiero_bp = Blueprint('financiero', __name__)

def normalizar_cabeceras(headers):
    """Normaliza las cabeceras del Excel para que coincidan con las claves del sistema."""
    # Limpieza profunda: quitamos tildes, símbolos especiales y espacios
    headers = [h.strip().upper().replace('Ó','O').replace('Í','I').replace('É','E').replace('Á','A').replace('Ú','U').replace('º','') for h in headers]
    # Mapeo de variantes de nombres de columnas financieras para asegurar persistencia
    return [h.replace(' ', '_').replace('Nº', 'N').replace('TIPO_DE_PAGO', 'TIPO_PAGO').replace('FORMA_DE_PAGO', 'FORMA_PAGO').replace('JUGADOR', 'NOMBRE') for h in headers]

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
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return datetime.min

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
    usuario = session.get('usuario', 'admin')
    
    jugadores = leer_hoja_limpia(client, NOMBRE_EXCEL, "JUGADORES")
    # Obtenemos lista única de equipos
    equipos = sorted(list(set(j['EQUIPO'] for j in jugadores if 'EQUIPO' in j)))
    
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

            # Sort by date (most recent first)
            historial_completo.sort(key=lambda x: parse_date_for_sort(x.get('FECHA')), reverse=True)

            # Asignar Nº ASIENTO secuencial global (000001...)
            for i, item in enumerate(historial_completo):
                item['Nº_ASIENTO_GLOBAL'] = str(i + 1).zfill(6)

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
            sheet_fin = client.open(NOMBRE_EXCEL).add_worksheet(title="FINANCIERO", rows="1000", cols="10")
            sheet_fin.update('A1', [["FECHA", "Nº ASIENTO", "DEPARTAMENTO", "PILAR", "DESCRIPCION", "IMPORTE", "NOMBRE", "EQUIPO", "CONCEPTO", "ESPERADO"]])

        all_fin = sheet_fin.get_all_values()
        headers_fin_raw = all_fin[0] if all_fin else []
        headers_fin_norm = normalizar_cabeceras(headers_fin_raw)
        
        if 'N_ASIENTO' not in headers_fin_norm:
            sheet_fin.update('A1', [["FECHA", "Nº ASIENTO", "DEPARTAMENTO", "PILAR", "DESCRIPCION", "IMPORTE", "NOMBRE", "EQUIPO", "CONCEPTO", "ESPERADO"]])
            all_fin = sheet_fin.get_all_values()
            headers_fin_raw = all_fin[0]
            headers_fin_norm = normalizar_cabeceras(headers_fin_raw)
        else:
            missing = [c for c in ["NOMBRE", "EQUIPO", "CONCEPTO", "ESPERADO"] if c not in headers_fin_norm]
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

        max_asi = 0
        idx_asi = headers_fin_norm.index('N_ASIENTO') if 'N_ASIENTO' in headers_fin_norm else -1
        if idx_asi == -1: idx_asi = 1 # Fallback B

        for row in all_fin[1:]:
            if len(row) > idx_asi and str(row[idx_asi]).isdigit():
                max_asi = max(max_asi, int(row[idx_asi]))
        
        num_asiento = max_asi + 1
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
            "ESPERADO": datos.get('esperado', datos.get('importe', 0))
        }

        nueva_fila_fin = []
        for h_norm in headers_fin_norm:
            nueva_fila_fin.append(mapeo_fila.get(h_norm, ""))

        sheet_fin.append_row(nueva_fila_fin)
        return jsonify({"status": "success", "asiento": num_asiento})
    except Exception as e:
        print(f"Error en api_presupuesto: {e}")
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
        # Estructura: CARGO, NOMBRE
        sheet.append_row([datos.get('cargo'), datos.get('nombre')])
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
    
    try:
        # 1. Actualizar en la pestaña STAFF
        sheet_staff = client.open(NOMBRE_EXCEL).worksheet("STAFF")
        celda = sheet_staff.find(nombre_antiguo, in_column=2)
        if celda:
            sheet_staff.update_cell(celda.row, 1, cargo_nuevo)
            sheet_staff.update_cell(celda.row, 2, nombre_nuevo)
            
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
        idx_asi = headers_fin.index('Nº_ASIENTO') if 'Nº_ASIENTO' in headers_fin else headers_fin.index('N_ASIENTO')
        
        fila_fin_idx = -1
        for i, row in enumerate(all_fin):
            if i > 0 and len(row) > idx_asi and str(row[idx_asi]).strip() == asiento_id:
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
            ], value_input_option='USER_ENTERED')
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
        return jsonify({"status": "error", "message": str(e)}), 500