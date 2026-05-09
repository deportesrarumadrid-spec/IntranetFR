import json
import gspread
from gspread.utils import rowcol_to_a1
from flask import Blueprint, render_template, request, session, jsonify

# Creamos el Blueprint para la sección financiera
financiero_bp = Blueprint('financiero', __name__)

def normalizar_cabeceras(headers):
    """Normaliza las cabeceras del Excel para que coincidan con las claves del sistema."""
    headers = [h.strip().upper().replace('Ó','O').replace('Í','I').replace('É','E').replace('Á','A').replace('Ú','U') for h in headers]
    # Mapeo de variantes de nombres de columnas financieras para asegurar persistencia
    return [h.replace(' ', '_').replace('TIPO_DE_PAGO', 'TIPO_PAGO').replace('FORMA_DE_PAGO', 'FORMA_PAGO').replace('JUGADOR', 'NOMBRE') for h in headers]

def leer_hoja_limpia(client, nombre_excel, nombre_hoja):
    """Función de utilidad para leer datos de Excel de forma estructurada."""
    try:
        sheet = client.open(nombre_excel).worksheet(nombre_hoja)
        all_v = sheet.get_all_values()
        if not all_v: return []
        headers = normalizar_cabeceras(all_v[0])
        
        datos = []
        for row in all_v[1:]:
            if any(row):
                registro = {}
                for i, h in enumerate(headers):
                    val = row[i] if i < len(row) else ""
                    registro[h] = val
                datos.append(registro)
        return datos
    except Exception as e:
        print(f"Error leyendo hoja {nombre_hoja}: {e}")
        return []

def get_or_create_sheet(client, nombre_excel, sheet_name):
    """Obtiene una hoja de cálculo o la crea si no existe."""
    try:
        return client.open(nombre_excel).worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        print(f"Creando hoja '{sheet_name}'...")
        sheet = client.open(nombre_excel).add_worksheet(title=sheet_name, rows="100", cols="20")
        sheet.update('A1', [['KEY', 'VALUE']])
        return sheet


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
            # Leer asientos manuales del libro diario general
            asientos = leer_hoja_limpia(client, NOMBRE_EXCEL, "FINANCIERO")
            # Leer pagos detallados de la nueva pestaña de jugadores
            pagos = leer_hoja_limpia(client, NOMBRE_EXCEL, "PAGOS JUGADORES")
            
            # Normalizar los pagos de jugadores para el historial
            for i, p in enumerate(pagos):
                p['PILAR'] = 'Cuotas'
                p['DESCRIPCION'] = f"Pago {p.get('CONCEPTO')}: {p.get('NOMBRE')}"
                p['IMPORTE'] = p.get('PAGADO')
                p['ESPERADO'] = p.get('ESPERADO')
                # Identificador único basado en la fila para el Nº Asiento
                p['Nº_ASIENTO'] = f"P-{str(i+1).zfill(4)}"
                if 'FECHA' not in p: p['FECHA'] = '-'
            
            return jsonify(asientos + pagos)
        
        datos = request.json
        pilar = datos.get('pilar')

        if pilar == "Cuotas":
            # Guardado específico en la nueva pestaña PAGOS JUGADORES
            try:
                sheet = client.open(NOMBRE_EXCEL).worksheet("PAGOS JUGADORES")
            except:
                # Si no existe, la creamos con cabeceras
                sheet = client.open(NOMBRE_EXCEL).add_worksheet(title="PAGOS JUGADORES", rows="100", cols="20")
                sheet.append_row(["FECHA", "EQUIPO", "NOMBRE", "TIPO PAGO", "FORMA PAGO", "CONCEPTO", "PAGADO", "ESPERADO"])
            
            if not sheet.get_all_values(): # Si está vacía, ponemos cabeceras
                sheet.append_row(["FECHA", "EQUIPO", "NOMBRE", "TIPO PAGO", "FORMA PAGO", "CONCEPTO", "PAGADO", "ESPERADO"])

            # Búsqueda de registro existente para evitar duplicados en el presupuesto
            all_v = sheet.get_all_values()
            idx_existente = -1
            if all_v:
                nom_b = str(datos.get('nombre')).strip().lower()
                eq_b = str(datos.get('equipo')).strip().lower()
                con_b = str(datos.get('concepto')).strip().lower()
                con_norm = con_b.zfill(2) if con_b.isdigit() else con_b
                
                for i, row in enumerate(all_v):
                    if i == 0: continue
                    if len(row) >= 6:
                        r_eq = row[1].strip().lower()
                        r_nom = row[2].strip().lower()
                        r_con = row[5].strip().lower()
                        r_con_norm = r_con.zfill(2) if r_con.isdigit() else r_con
                        
                        if r_eq == eq_b and r_nom == nom_b and r_con_norm == con_norm:
                            idx_existente = i + 1
                            break

            if idx_existente != -1:
                # ACTUALIZAR: Evitamos duplicar la línea modificando la existente
                sheet.update_cell(idx_existente, 1, datos.get('fecha'))
                sheet.update_cell(idx_existente, 7, datos.get('importe'))
                sheet.update_cell(idx_existente, 8, datos.get('esperado'))
                return jsonify({"status": "success", "asiento": f"P-{str(idx_existente-1).zfill(4)}"})
            else:
                # INSERTAR NUEVO: FECHA, EQUIPO, JUGADOR, TIPO PAGO, FORMA PAGO, CONCEPTO, PAGADO, ESPERADO
                nueva_fila = [
                    datos.get('fecha'),
                    datos.get('equipo'),
                    datos.get('nombre'),
                    datos.get('tipo_pago'),
                    datos.get('forma_pago'),
                    datos.get('concepto'),
                    datos.get('importe'),
                    datos.get('esperado')
                ]
                sheet.append_row(nueva_fila)
                return jsonify({"status": "success", "asiento": f"P-{str(len(all_v)).zfill(4)}"})
        else:
            # Guardado en el libro diario general FINANCIERO
            sheet = client.open(NOMBRE_EXCEL).worksheet("FINANCIERO")
            # El número de asiento es el siguiente índice disponible
            num_asiento = len(sheet.get_all_values()) 
            nueva_fila = [datos.get('fecha'), num_asiento, datos.get('departamento'), pilar, datos.get('descripcion'), datos.get('importe')]
            sheet.append_row(nueva_fila)
            return jsonify({"status": "success", "asiento": num_asiento})
    except Exception as e:
        print(f"Error en api_presupuesto: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/borrar_pago', methods=['POST'])
def api_borrar_pago():
    from app import client, NOMBRE_EXCEL
    datos = request.json
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("PAGOS JUGADORES")
        all_v = sheet.get_all_values()
        
        idx_to_delete = -1
        # Normalización extrema para evitar fallos de coincidencia
        nombre_buscado = str(datos.get('nombre') or "").strip().lower()
        equipo_buscado = str(datos.get('equipo') or "").strip().lower()
        c_raw = str(datos.get('concepto') or "").strip().lower()
        # Si es un número (ej: "1"), lo convertimos a "01" para el Excel
        concepto_buscado = c_raw.zfill(2) if c_raw.isdigit() else c_raw

        for i, row in enumerate(all_v):
            if i == 0: continue
            if len(row) >= 6:
                # Leemos columnas 1 (Equipo), 2 (Nombre) y 5 (Concepto)
                r_equipo = str(row[1]).strip().lower()
                r_nombre = str(row[2]).strip().lower()
                r_val_concepto = str(row[5]).strip().lower()
                r_concepto = r_val_concepto.zfill(2) if r_val_concepto.isdigit() else r_val_concepto
                
                if r_equipo == equipo_buscado and r_nombre == nombre_buscado and r_concepto == concepto_buscado:
                    idx_to_delete = i + 1
                    break
        
        if idx_to_delete != -1:
            sheet.delete_rows(idx_to_delete)
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
        
        idx_to_update = -1
        nombre_buscado = str(datos.get('nombre', '')).strip().lower()
        equipo_buscado = str(datos.get('equipo', '')).strip().lower()
        concepto_buscado = str(datos.get('concepto', '')).strip().lower().zfill(2) if str(datos.get('concepto', '')).isdigit() else str(datos.get('concepto', '')).strip().lower()

        for i, row in enumerate(all_v):
            if i == 0: continue
            if len(row) >= 6:
                row_equipo = str(row[1]).strip().lower()
                row_nombre = str(row[2]).strip().lower()
                row_concepto = str(row[5]).strip().lower().zfill(2) if str(row[5]).isdigit() else str(row[5]).strip().lower()
                
                if row_equipo == equipo_buscado and row_nombre == nombre_buscado and row_concepto == concepto_buscado:
                    idx_to_update = i + 1
                    break
        
        if idx_to_update != -1:
            # Actualización directa a la columna G (7)
            sheet.update_cell(idx_to_update, 7, datos.get('importe'))
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": "No se encontró el registro"}), 404
    except Exception as e:
        print(f"Error api_actualizar_pago: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/limpiar_historial', methods=['POST'])
def api_limpiar_historial():
    from app import client, NOMBRE_EXCEL
    data = request.json
    tipo = data.get('tipo') # 'FINANCIERO' o 'PAGOS JUGADORES'
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet(tipo)
        rows = len(sheet.get_all_values())
        if rows > 1:
            # Borramos desde la fila 2 hasta el final para mantener cabeceras
            sheet.delete_rows(2, rows)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/operacion_asiento', methods=['POST'])
def api_operacion_asiento():
    from app import client, NOMBRE_EXCEL
    data = request.json
    accion = data.get('accion') # 'borrar' o 'editar'
    asiento_id = str(data.get('id')) # El Nº Asiento
    
    try:
        # Los asientos P-XXXX están en PAGOS JUGADORES, los numéricos en FINANCIERO
        es_pago = asiento_id.startswith('P-')
        nombre_hoja = "PAGOS JUGADORES" if es_pago else "FINANCIERO"
        sheet = client.open(NOMBRE_EXCEL).worksheet(nombre_hoja)
        all_v = sheet.get_all_values()
        
        fila_idx = -1
        # Si es pago, el ID se genera dinámicamente por índice, pero para borrar 
        # buscamos coincidencia en la hoja basándonos en la descripción/nombre que traiga el data
        if es_pago:
            # Reutilizamos la lógica de borrar_pago ya existente arriba si es un P-XXXX
            # o buscamos por índice si el ID P-0005 significa fila 6.
            fila_idx = int(asiento_id.split('-')[1]) + 1
        else:
            # Asientos manuales: el ID está en la columna B (índice 1)
            for i, row in enumerate(all_v):
                if i == 0: continue
                if len(row) > 1 and str(row[1]) == asiento_id:
                    fila_idx = i + 1
                    break
        
        if fila_idx == -1 or fila_idx > len(all_v):
            return jsonify({"status": "error", "message": "Asiento no encontrado"}), 404

        if accion == 'borrar':
            sheet.delete_rows(fila_idx)
        elif accion == 'editar':
            # Solo para FINANCIERO (manuales) en este endpoint
            if not es_pago:
                # FECHA(1), Nº(2), DEP(3), PILAR(4), DESC(5), IMP(6)
                sheet.update_cell(fila_idx, 1, data.get('fecha'))
                sheet.update_cell(fila_idx, 3, data.get('departamento'))
                sheet.update_cell(fila_idx, 4, data.get('pilar'))
                sheet.update_cell(fila_idx, 5, data.get('descripcion'))
                sheet.update_cell(fila_idx, 6, data.get('importe'))
            else:
                return jsonify({"status": "error", "message": "Usa la tabla para editar pagos"}), 400
                
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
            extra_names = {"EXTRA": "EXTRA", "EXTRA2": "EXTRA 2"}

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
            config_sheet.update('A1', [
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