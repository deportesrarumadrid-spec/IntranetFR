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
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d%m%Y', '%Y%m%d'):
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
    if lookup in meses_map:
        return meses_map[lookup]
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

@financiero_bp.route('/api/presupuesto/siguiente_numero')
def api_presupuesto_siguiente_numero():
    from app import client, NOMBRE_EXCEL
    try:
        sheet_fin = client.open(NOMBRE_EXCEL).worksheet("FINANCIERO")
        all_fin = sheet_fin.get_all_values()
        headers_fin_norm = normalizar_cabeceras(all_fin[0]) if all_fin else []
        idx_asi = -1
        for variant in ['N_ASIENTO', 'Nº_ASIENTO', 'ASIENTO']:
            if variant in headers_fin_norm:
                idx_asi = headers_fin_norm.index(variant); break
        max_asi = 0
        for row in all_fin[1:]:
            if idx_asi != -1 and len(row) > idx_asi and row[idx_asi]:
                try:
                    val_limpio = str(row[idx_asi]).strip().replace('#', '').split('_')[0]
                    if val_limpio.isdigit():
                        max_asi = max(max_asi, int(val_limpio))
                except: continue
        return jsonify({"numero": max_asi + 1})
    except Exception as e:
        print(f"Error en api_presupuesto_siguiente_numero: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

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

            # Asignar Nº ASIENTO secuencial global (000001...). Las filas que comparten la misma base
            # de Nº ASIENTO (p.ej. "47_1" y "47_2", de un pago repartido en varios conceptos) reciben
            # el mismo número global con su sufijo, en vez de números independientes.
            grupos_vistos = {}
            contador_global = 0
            grupos_totales = {}
            grupos_conteo = {}
            for item in historial_completo:
                raw_n = str(item.get('REAL_ID') or '').strip().replace('#', '')
                base, _, suf = raw_n.partition('_')
                if base not in grupos_vistos:
                    contador_global += 1
                    grupos_vistos[base] = contador_global
                numero_global = grupos_vistos[base]
                item['Nº_ASIENTO_GLOBAL'] = f"{str(numero_global).zfill(6)}_{suf}" if suf else str(numero_global).zfill(6)
                grupos_conteo[base] = grupos_conteo.get(base, 0) + 1
                try:
                    grupos_totales[base] = grupos_totales.get(base, 0) + float(str(item.get('IMPORTE', '0')).replace(',', '.').strip() or 0)
                except ValueError:
                    pass

            for item in historial_completo:
                raw_n = str(item.get('REAL_ID') or '').strip().replace('#', '')
                base = raw_n.split('_')[0]
                if grupos_conteo.get(base, 0) > 1:
                    item['IMPORTE_TOTAL_GRUPO'] = round(grupos_totales[base], 2)

            # Invertimos para que por defecto el GET devuelva los más nuevos arriba
            historial_completo.reverse()

            # Evitar cache de navegador
            resp = jsonify(historial_completo)
            resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            return resp
        
        datos = request.json
        pilar = datos.get('pilar')
        # duplicate_action: None (preguntar al usuario) | 'force_new' | 'overwrite' | 'compensate'
        duplicate_action = datos.get('duplicate_action')
        print(f"[api_presupuesto] pilar={pilar!r} nombre={datos.get('nombre')!r} concepto={datos.get('concepto')!r} importe={datos.get('importe')} dup={duplicate_action!r}")

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

            # pagado_total: importe acumulado total para PAGOS JUGADORES (en pago complementario
            # = ya_pagado + nuevo). Si no viene, se usa importe (primer pago o pago normal).
            pagado_total_pj = datos.get('pagado_total', datos.get('importe'))
            if idx_existente != -1:
                # ACTUALIZAR: Evitamos duplicar la línea modificando la existente
                print(f"[PAGOS JUGADORES] Actualizando fila {idx_existente}: pagado_total={pagado_total_pj} esperado={datos.get('esperado')}")
                col_pag = rowcol_to_a1(idx_existente, idx_pag + 1)
                col_esp = rowcol_to_a1(idx_existente, idx_esp + 1)
                col_tp  = rowcol_to_a1(idx_existente, idx_tp  + 1)
                col_fp  = rowcol_to_a1(idx_existente, idx_fp  + 1)
                updates = [
                    {'range': f"'PAGOS JUGADORES'!{rowcol_to_a1(idx_existente, 1)}", 'values': [[datos.get('fecha')]]},
                    {'range': f"'PAGOS JUGADORES'!{col_pag}", 'values': [[pagado_total_pj]]},
                    {'range': f"'PAGOS JUGADORES'!{col_esp}", 'values': [[datos.get('esperado')]]},
                    {'range': f"'PAGOS JUGADORES'!{col_tp}",  'values': [[datos.get('tipo_pago')]]},
                    {'range': f"'PAGOS JUGADORES'!{col_fp}",  'values': [[datos.get('forma_pago')]]},
                ]
                sheet.spreadsheet.values_batch_update({
                    'valueInputOption': 'USER_ENTERED',
                    'data': updates
                })
                print(f"[PAGOS JUGADORES] OK fila {idx_existente}")
            else:
                print(f"[PAGOS JUGADORES] Insertando nueva fila: nombre={datos.get('nombre')} concepto={datos.get('concepto')} pagado_total={pagado_total_pj}")
                sheet.append_row([datos.get('fecha'), datos.get('equipo'), datos.get('nombre'), datos.get('tipo_pago'), datos.get('forma_pago'), datos.get('concepto'), pagado_total_pj, datos.get('esperado')])
                print(f"[PAGOS JUGADORES] OK nueva fila")

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

            pagado_total_ps = datos.get('pagado_total', datos.get('importe'))
            if idx_existente != -1:
                updates = [
                    {'range': f"'PAGOS STAFF'!{rowcol_to_a1(idx_existente, 1)}", 'values': [[datos.get('fecha')]]},
                    {'range': f"'PAGOS STAFF'!{rowcol_to_a1(idx_existente, idx_pag+1)}:{rowcol_to_a1(idx_existente, idx_esp+1)}", 'values': [[pagado_total_ps, datos.get('esperado')]]}
                ]
                sheet.spreadsheet.values_batch_update({'valueInputOption': 'USER_ENTERED', 'data': updates})
            else:
                sheet.append_row([datos.get('fecha'), datos.get('nombre'), datos.get('concepto'), pagado_total_ps, datos.get('esperado')])

            # Preparamos los metadatos para el registro maestro en FINANCIERO
            if not datos.get('descripcion'):
                friendly = get_friendly_concepto(datos.get('concepto'))
                datos['descripcion'] = f"Pago Staff {friendly}: {datos.get('nombre')}"
            datos['departamento'] = "Sueldos"

        # REGISTRO MAESTRO EN PESTAÑA FINANCIERO (Homólogo)
        try:
            sheet_fin = client.open(NOMBRE_EXCEL).worksheet("FINANCIERO")
        except gspread.exceptions.WorksheetNotFound:
            sheet_fin = client.open(NOMBRE_EXCEL).add_worksheet(title="FINANCIERO", rows="1000", cols="11")
            sheet_fin.update('A1', [["FECHA", "Nº ASIENTO", "DEPARTAMENTO", "PILAR", "DESCRIPCION", "IMPORTE", "NOMBRE", "EQUIPO", "CONCEPTO", "ESPERADO", "FACTURA", "FORMA_PAGO"]])

        all_fin = sheet_fin.get_all_values()
        headers_fin_raw = all_fin[0] if all_fin else []
        headers_fin_norm = normalizar_cabeceras(headers_fin_raw)
        
        if 'N_ASIENTO' not in headers_fin_norm:
            sheet_fin.update('A1', [["FECHA", "Nº ASIENTO", "DEPARTAMENTO", "PILAR", "DESCRIPCION", "IMPORTE", "NOMBRE", "EQUIPO", "CONCEPTO", "ESPERADO", "FACTURA", "FORMA_PAGO"]])
            all_fin = sheet_fin.get_all_values()
            headers_fin_raw = all_fin[0]
            headers_fin_norm = normalizar_cabeceras(headers_fin_raw)
        else:
            missing = [c for c in ["NOMBRE", "EQUIPO", "CONCEPTO", "ESPERADO", "FACTURA", "FORMA_PAGO"] if c not in headers_fin_norm]
            if missing:
                new_headers_raw = headers_fin_raw + missing
                sheet_fin.update('A1', [new_headers_raw])
                all_fin = sheet_fin.get_all_values()
                headers_fin_raw = all_fin[0]
                headers_fin_norm = normalizar_cabeceras(headers_fin_raw)

        # BÚSQUEDA ROBUSTA DE DUPLICADOS EN FINANCIERO (MAESTRA)
        # force_new=True lo usa la carga masiva: cada transacción bancaria es única → siempre nueva fila
        idx_fin_existente = -1
        try:
            idx_p = headers_fin_norm.index('PILAR') if 'PILAR' in headers_fin_norm else -1
            idx_n = headers_fin_norm.index('NOMBRE') if 'NOMBRE' in headers_fin_norm else -1
            idx_c = headers_fin_norm.index('CONCEPTO') if 'CONCEPTO' in headers_fin_norm else -1

            if duplicate_action != 'force_new' and pilar in ["Cuotas", "Pagos Staff"] and idx_p != -1 and idx_n != -1 and idx_c != -1:
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
            old_row    = all_fin[idx_fin_existente - 1]
            idx_imp_col  = headers_fin_norm.index('IMPORTE')     if 'IMPORTE'     in headers_fin_norm else -1
            idx_desc_col = headers_fin_norm.index('DESCRIPCION') if 'DESCRIPCION' in headers_fin_norm else -1
            idx_asi_col  = next((headers_fin_norm.index(v) for v in ['N_ASIENTO','Nº_ASIENTO','ASIENTO'] if v in headers_fin_norm), -1)
            try:
                old_importe = float(str(old_row[idx_imp_col]).replace(',', '.')) if idx_imp_col != -1 and len(old_row) > idx_imp_col and old_row[idx_imp_col] else 0
            except:
                old_importe = 0
            old_desc        = old_row[idx_desc_col] if idx_desc_col != -1 and len(old_row) > idx_desc_col else ''
            old_num_asiento = old_row[idx_asi_col]  if idx_asi_col  != -1 and len(old_row) > idx_asi_col  else ''

            if not duplicate_action:
                if pilar in ("Cuotas", "Pagos Staff"):
                    # Para cuotas/staff: sobreescribir siempre (PAGOS JUGADORES ya se actualizó arriba)
                    duplicate_action = 'overwrite'
                else:
                    # Para entradas manuales: informar al frontend para que el usuario decida
                    return jsonify({
                        'status': 'duplicate',
                        'existing': {
                            'asiento':     str(old_num_asiento),
                            'fecha':       old_row[0] if old_row else '',
                            'descripcion': old_desc,
                            'importe':     str(old_importe),
                        }
                    }), 200

            if duplicate_action == 'overwrite':
                # Sobreescribir la fila existente
                print(f"[FINANCIERO] Overwrite fila {idx_fin_existente}: importe={datos.get('importe')} esperado={datos.get('esperado')}")
                updates = []
                mapping = {'FECHA': datos.get('fecha'), 'DESCRIPCION': datos.get('descripcion'),
                           'IMPORTE': datos.get('importe'), 'ESPERADO': datos.get('esperado', 0)}
                for key, val in mapping.items():
                    if key in headers_fin_norm:
                        col_idx = headers_fin_norm.index(key) + 1
                        updates.append({'range': f"'FINANCIERO'!{rowcol_to_a1(idx_fin_existente, col_idx)}", 'values': [[val]]})
                if updates:
                    sheet_fin.spreadsheet.values_batch_update({'valueInputOption': 'USER_ENTERED', 'data': updates})
                print(f"[FINANCIERO] Overwrite OK fila {idx_fin_existente}")
                return jsonify({"status": "success", "asiento": old_num_asiento})

            elif duplicate_action == 'compensate':
                # Abonar el anterior (línea de signo contrario) y crear el nuevo
                max_asi = 0
                for row in all_fin[1:]:
                    if idx_asi_col != -1 and len(row) > idx_asi_col and row[idx_asi_col]:
                        try:
                            v = str(row[idx_asi_col]).strip().replace('#', '').split('_')[0]
                            if v.isdigit():
                                max_asi = max(max_asi, int(v))
                        except: continue
                rev_num = max_asi + 1
                rev_mapeo = {
                    "FECHA":        datos.get('fecha'),
                    "N_ASIENTO":    rev_num,
                    "DEPARTAMENTO": datos.get('departamento'),
                    "PILAR":        pilar,
                    "DESCRIPCION":  f"Anulación #{old_num_asiento}: {old_desc}" if old_num_asiento else f"Anulación: {old_desc}",
                    "IMPORTE":      -old_importe,
                    "NOMBRE":       datos.get('nombre', ''),
                    "EQUIPO":       datos.get('equipo', ''),
                    "CONCEPTO":     datos.get('concepto', ''),
                    "ESPERADO":     0,
                    "FACTURA":      ''
                }
                sheet_fin.append_row([rev_mapeo.get(h, "") for h in headers_fin_norm])
                datos['n_asiento'] = rev_num + 1
                # Fall through → append nuevo asiento a continuación

            # duplicate_action == 'force_new': el índice nunca se encontró (la búsqueda se saltó),
            # pero si por algún motivo llegamos aquí con force_new, también caemos en el append.

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
                        val_limpio = str(row[idx_asi]).strip().replace('#', '').split('_')[0]
                        if val_limpio.isdigit():
                            max_asi = max(max_asi, int(val_limpio))
                    except: continue
            num_asiento = max_asi + 1
        else:
            # OJO: int() de Python acepta "_" como separador de miles (PEP 515), p.ej. int("49_1") == 491.
            # Por eso comprobamos primero si es un número "puro" antes de convertir, para no corromper
            # los Nº ASIENTO con sufijo de grupo (ej. "49_1", "49_2") usados en pagos repartidos.
            num_str = str(num_asiento).strip()
            if num_str.isdigit():
                num_asiento = int(num_str)
            else:
                num_asiento = num_str # Mantener como string (incluye los que llevan sufijo "_N")

        # Verificar asientos de apertura obligatorios (#000001 BAN y #000002 EFE)
        # Excepción: si el propio asiento que se crea ES el #1 o el #2
        # Excepción también para cuotas y pagos de staff (no son asientos contables manuales)
        _num_asi_int = None
        try:
            _num_asi_int = int(str(num_asiento).strip().replace('#', '').split('_')[0])
        except: pass

        if pilar not in ("Cuotas", "Pagos Staff") and (_num_asi_int is None or _num_asi_int > 2):
            _idx_asi2 = next((headers_fin_norm.index(v) for v in ['N_ASIENTO','Nº_ASIENTO','ASIENTO'] if v in headers_fin_norm), -1)
            _idx_fp   = headers_fin_norm.index('FORMA_PAGO') if 'FORMA_PAGO' in headers_fin_norm else -1
            _tiene_001_ban = _tiene_002_efe = False
            for _row in all_fin[1:]:
                if _idx_asi2 == -1: break
                _asi_v = str(_row[_idx_asi2]).strip().replace('#', '').split('_')[0] if len(_row) > _idx_asi2 else ''
                _fp_v  = str(_row[_idx_fp]).strip().upper() if _idx_fp != -1 and len(_row) > _idx_fp else ''
                if _asi_v in ('1', '000001') and _fp_v == 'BAN': _tiene_001_ban = True
                if _asi_v in ('2', '000002') and _fp_v == 'EFE': _tiene_002_efe = True
            if not _tiene_001_ban or not _tiene_002_efe:
                _falt = []
                if not _tiene_001_ban: _falt.append('#000001 (BAN · Saldo apertura banco)')
                if not _tiene_002_efe: _falt.append('#000002 (EFE · Saldo apertura efectivo)')
                return jsonify({'status': 'error', 'message': f"Faltan asientos de apertura: {', '.join(_falt)}. Créalos primero."}), 400
        elif pilar not in ("Cuotas", "Pagos Staff") and _num_asi_int == 2:
            _idx_asi2 = next((headers_fin_norm.index(v) for v in ['N_ASIENTO','Nº_ASIENTO','ASIENTO'] if v in headers_fin_norm), -1)
            _idx_fp   = headers_fin_norm.index('FORMA_PAGO') if 'FORMA_PAGO' in headers_fin_norm else -1
            _tiene_001_ban = any(
                str(r[_idx_asi2]).strip().replace('#','').split('_')[0] in ('1','000001') and
                str(r[_idx_fp]).strip().upper() == 'BAN'
                for r in all_fin[1:] if _idx_asi2 != -1 and _idx_fp != -1 and len(r) > max(_idx_asi2, _idx_fp)
            )
            if not _tiene_001_ban:
                return jsonify({'status': 'error', 'message': 'Primero debes crear el asiento #000001 (BAN · Saldo apertura banco).'}), 400

        # Los pagos a staff son un gasto (dinero que sale) → importe negativo en FINANCIERO
        importe_fin = datos.get('importe')
        if pilar == "Pagos Staff":
            try:
                importe_fin = -abs(float(importe_fin or 0))
            except:
                pass

        mapeo_fila = {
            "FECHA": datos.get('fecha'),
            "N_ASIENTO": num_asiento,
            "DEPARTAMENTO": datos.get('departamento'),
            "PILAR": pilar,
            "DESCRIPCION": datos.get('descripcion') or f"Asiento {num_asiento}",
            "IMPORTE": importe_fin,
            "NOMBRE": datos.get('nombre', ''),
            "EQUIPO": datos.get('equipo', ''),
            "CONCEPTO": datos.get('concepto', ''),
            "ESPERADO": datos.get('esperado', datos.get('importe', 0)),
            "FACTURA": datos.get('factura_estado', ''),
            "FORMA_PAGO": datos.get('forma_pago', '')
        }

        nueva_fila_fin = []
        for h_norm in headers_fin_norm:
            nueva_fila_fin.append(mapeo_fila.get(h_norm, ""))

        sheet_fin.append_row(nueva_fila_fin)
        print(f"[api_presupuesto] FINANCIERO OK → asiento={num_asiento}")

        # Generar recibo desde los datos exactos recién registrados, sin agrupar por N_ASIENTO
        recibo_html = None
        if pilar in ("Cuotas", "Pagos Staff"):
            try:
                from app import _generar_html_recibo
                concepto_friendly = get_friendly_concepto(datos.get('concepto', '')) or datos.get('concepto', '')
                recibo_html = _generar_html_recibo({
                    'n_asiento': num_asiento,
                    'fecha': datos.get('fecha', ''),
                    'nombre': datos.get('nombre', ''),
                    'equipo': datos.get('equipo', ''),
                    'importe': importe_fin,
                    'conceptos': [{'concepto': concepto_friendly, 'importe': importe_fin}]
                })
            except Exception as e_rec:
                print(f"[recibo_html] Error generando recibo inline: {e_rec}")

        return jsonify({"status": "success", "asiento": num_asiento, "recibo_html": recibo_html})
    except Exception as e:
        import traceback
        print(f"[api_presupuesto] ERROR: {e}")
        traceback.print_exc()
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

        importe_original = parse_amount(fila_actual[idx_importe]) if idx_importe != -1 and len(fila_actual) > idx_importe else 0
        # Permite asignar solo una parte del importe original (reparto multi-concepto desde el frontend).
        importe_override = datos.get('importe')
        importe = parse_amount(importe_override) if importe_override is not None else importe_original
        fecha_actual = fila_actual[idx_fecha] if idx_fecha != -1 and len(fila_actual) > idx_fecha else ''
        nuevo_n_asiento = (datos.get('nuevo_n_asiento') or '').strip()

        updates = [
            {'range': f"'FINANCIERO'!{rowcol_to_a1(idx_fila, idx_nombre + 1)}", 'values': [[nombre]]},
            {'range': f"'FINANCIERO'!{rowcol_to_a1(idx_fila, idx_equipo + 1)}", 'values': [[equipo]]},
            {'range': f"'FINANCIERO'!{rowcol_to_a1(idx_fila, idx_concepto + 1)}", 'values': [[concepto]]},
            {'range': f"'FINANCIERO'!{rowcol_to_a1(idx_fila, idx_asignacion + 1)}", 'values': [['']]},
        ]
        if idx_pilar != -1:
            updates.append({'range': f"'FINANCIERO'!{rowcol_to_a1(idx_fila, idx_pilar + 1)}", 'values': [['Cuotas']]})
        if importe_override is not None and idx_importe != -1:
            updates.append({'range': f"'FINANCIERO'!{rowcol_to_a1(idx_fila, idx_importe + 1)}", 'values': [[importe]]})
        if nuevo_n_asiento and idx_asi != -1:
            updates.append({'range': f"'FINANCIERO'!{rowcol_to_a1(idx_fila, idx_asi + 1)}", 'values': [[nuevo_n_asiento]]})
        descripcion_override = (datos.get('descripcion') or '').strip()
        if descripcion_override and idx_descripcion != -1:
            updates.append({'range': f"'FINANCIERO'!{rowcol_to_a1(idx_fila, idx_descripcion + 1)}", 'values': [[descripcion_override]]})
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

        return jsonify({"status": "success", "n_asiento": nuevo_n_asiento or n_asiento})
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
            # Determinar columnas desde headers reales para no asumir orden fijo
            headers_esp_raw = normalizar_cabeceras(all_v[0]) if all_v else []
            if tipo_origen == 'JUGADOR':
                col_n = next((headers_esp_raw.index(v) for v in ['NOMBRE','JUGADOR'] if v in headers_esp_raw), 2)
                col_c = next((headers_esp_raw.index(v) for v in ['CONCEPTO'] if v in headers_esp_raw), 5)
            else:
                col_n = next((headers_esp_raw.index(v) for v in ['NOMBRE','JUGADOR'] if v in headers_esp_raw), 1)
                col_c = next((headers_esp_raw.index(v) for v in ['CONCEPTO'] if v in headers_esp_raw), 2)
            print(f"[operacion_asiento] {nombre_hoja} headers={headers_esp_raw!r} col_n={col_n} col_c={col_c}")
            
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
        headers_fin_raw = all_fin[0] if all_fin else []
        headers_fin = normalizar_cabeceras(headers_fin_raw)
        # Migración: asegurar columna FORMA_PAGO existe
        if 'FORMA_PAGO' not in headers_fin:
            headers_fin_raw = headers_fin_raw + ['FORMA_PAGO']
            sheet_fin.update('A1', [headers_fin_raw])
            all_fin = sheet_fin.get_all_values()
            headers_fin = normalizar_cabeceras(all_fin[0])
        
        idx_asi = -1
        for v in ['Nº_ASIENTO', 'N_ASIENTO', 'ASIENTO']:
            if v in headers_fin:
                idx_asi = headers_fin.index(v)
                break
        
        fila_fin_idx = -1
        if idx_asi != -1:
            for i, row in enumerate(all_fin):
                if i > 0 and len(row) > idx_asi:
                    # Comparación flexible de IDs (ignora símbolos y ceros a la izquierda)
                    val_row = str(row[idx_asi]).strip().replace('#','')
                    val_target = str(asiento_id).strip().replace('#','')
                    if val_row == val_target or (val_row.isdigit() and val_target.isdigit() and int(val_row) == int(val_target)):
                        fila_fin_idx = i + 1
                        break

        # Fallback: buscar por NOMBRE+CONCEPTO en FINANCIERO si el ID no encontró nada
        if fila_fin_idx == -1 and tipo_origen in ['JUGADOR', 'STAFF']:
            idx_nom = next((headers_fin.index(v) for v in ['NOMBRE', 'JUGADOR'] if v in headers_fin), -1)
            idx_con = next((headers_fin.index(v) for v in ['CONCEPTO'] if v in headers_fin), -1)
            print(f"[borrar] Fallback contenido: idx_asi={idx_asi!r} idx_nom={idx_nom!r} idx_con={idx_con!r} headers={headers_fin!r}")
            if idx_nom != -1 and idx_con != -1:
                nom_bus = data.get('nombre_orig', '').strip().lower()
                con_bus = normalizar_concepto_interno(data.get('concepto_orig', ''))
                for i, row in enumerate(all_fin):
                    if i > 0 and len(row) > max(idx_nom, idx_con):
                        if (row[idx_nom].strip().lower() == nom_bus and
                                normalizar_concepto_interno(row[idx_con]) == con_bus):
                            fila_fin_idx = i + 1
                            print(f"[borrar] Fallback encontró fila {fila_fin_idx} por nombre+concepto")
                            break

        if fila_fin_idx == -1:
            if tipo_origen not in ['JUGADOR', 'STAFF']:
                return jsonify({"status": "error", "message": "Asiento no encontrado en el historial"}), 404
            print(f"[borrar] FINANCIERO: asiento {asiento_id!r} no encontrado — borrando solo de sub-hoja")

        if accion == 'borrar':
            if fila_fin_idx != -1:
                sheet_fin.delete_rows(fila_fin_idx)
            # También borrar de la hoja específica si corresponde
            if tipo_origen in ['JUGADOR', 'STAFF']:
                sheet_esp = client.open(NOMBRE_EXCEL).worksheet("PAGOS STAFF" if tipo_origen == 'STAFF' else "PAGOS JUGADORES")
                all_esp = sheet_esp.get_all_values()
                # Recalcular col_n/col_c por si los headers de PAGOS JUGADORES difieren del orden asumido
                if all_esp:
                    _h2 = normalizar_cabeceras(all_esp[0])
                    if tipo_origen == 'JUGADOR':
                        col_n = next((h for h in [_h2.index(v) if v in _h2 else None for v in ['NOMBRE','JUGADOR']] if h is not None), col_n)
                        col_c = _h2.index('CONCEPTO') if 'CONCEPTO' in _h2 else col_c
                    else:
                        col_n = next((h for h in [_h2.index(v) if v in _h2 else None for v in ['NOMBRE','JUGADOR']] if h is not None), col_n)
                        col_c = _h2.index('CONCEPTO') if 'CONCEPTO' in _h2 else col_c
                    print(f"[borrar] PAGOS sub-hoja headers={_h2!r} col_n={col_n} col_c={col_c}")
                for i, row in enumerate(all_esp):
                    if i > 0 and len(row) > max(col_n, col_c) and row[col_n].strip().lower() == data.get('nombre_orig','').strip().lower() and normalizar_concepto_interno(row[col_c]) == normalizar_concepto_interno(data.get('concepto_orig')):
                        sheet_esp.delete_rows(i + 1)
                        break

            # Desconciliar la línea de remesa vinculada a este jugador
            # Solo si ya no quedan pagos registrados en PAGOS JUGADORES para ese jugador
            nombre_del = (data.get('nombre_orig') or '').strip().lower()
            if nombre_del:
                try:
                    _puede_desconciliar = True
                    if tipo_origen == 'JUGADOR':
                        # sheet_esp es PAGOS JUGADORES (ya abierto arriba); re-leer tras borrar
                        try:
                            _rows_pj = sheet_esp.get_all_records()
                            _tiene_pagos = any(
                                str(_r.get('NOMBRE', '')).strip().lower() == nombre_del and
                                float(str(_r.get('PAGADO', '0')).replace(',', '.') or '0') > 0
                                for _r in _rows_pj
                            )
                            if _tiene_pagos:
                                _puede_desconciliar = False  # quedan otros asientos → mantener verde
                        except Exception:
                            _puede_desconciliar = False  # por seguridad: no desconciliar si error
                    if _puede_desconciliar:
                        with open(_REMESAS_PATH, 'r', encoding='utf-8') as _f:
                            _historial = json.load(_f)
                        _cambio = False
                        for _remesa in _historial:
                            for _linea in _remesa.get('lineas', []):
                                if _linea.get('conciliada') and (_linea.get('jugador') or '').strip().lower() == nombre_del:
                                    _linea['conciliada'] = False
                                    _cambio = True
                        if _cambio:
                            with open(_REMESAS_PATH, 'w', encoding='utf-8') as _f:
                                json.dump(_historial, _f, ensure_ascii=False, indent=2)
                            print(f"[borrar_asiento] Desconciliadas líneas de remesa para '{nombre_del}'")
                except Exception as _e_rem:
                    print(f"[borrar_asiento] No se pudo desconciliar remesa: {_e_rem}")
        
        elif accion == 'editar':
            if fila_fin_idx == -1:
                return jsonify({"status": "error", "message": "Asiento no encontrado en el historial"}), 404
            print(f"[editar] fila_fin_idx={fila_fin_idx} tipo_origen={tipo_origen!r} asiento_id={asiento_id!r}")
            print(f"[editar] headers_fin={headers_fin}")
            print(f"[editar] data keys={list(data.keys())}")

            if tipo_origen in ['JUGADOR', 'STAFF']:
                mapeo_fin = {'fecha': 'FECHA', 'departamento': 'DEPARTAMENTO', 'descripcion': 'DESCRIPCION', 'forma_pago': 'FORMA_PAGO'}
            else:
                mapeo_fin = {'fecha': 'FECHA', 'departamento': 'DEPARTAMENTO', 'pilar': 'PILAR', 'descripcion': 'DESCRIPCION', 'importe': 'IMPORTE', 'forma_pago': 'FORMA_PAGO'}

            updates_fin = []
            skipped = []
            for k_json, k_head in mapeo_fin.items():
                in_data = k_json in data
                in_headers = k_head in headers_fin
                if in_data and in_headers:
                    col_idx = headers_fin.index(k_head) + 1
                    cell_ref = rowcol_to_a1(fila_fin_idx, col_idx)
                    updates_fin.append({'range': f"'FINANCIERO'!{cell_ref}", 'values': [[data[k_json]]]})
                    print(f"[editar] → actualizar {k_head} ({cell_ref}) = {data[k_json]!r}")
                else:
                    skipped.append(f"{k_json}(in_data={in_data},in_headers={in_headers})")
            if skipped:
                print(f"[editar] saltados: {skipped}")

            if tipo_origen in ['JUGADOR', 'STAFF'] and 'ESPERADO' in headers_fin:
                updates_fin.append({'range': f"'FINANCIERO'!{rowcol_to_a1(fila_fin_idx, headers_fin.index('ESPERADO')+1)}", 'values': [[data.get('esperado', 0)]]})

            if not updates_fin:
                return jsonify({"status": "error", "message": f"No hay campos que actualizar. Saltados: {skipped}"}), 400

            print(f"[editar] ejecutando batch update con {len(updates_fin)} celdas")
            sheet_fin.spreadsheet.values_batch_update({'valueInputOption': 'USER_ENTERED', 'data': updates_fin})
            print(f"[editar] batch update OK")

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
            staff_sueldos = {}
            staff_extras_count = 1

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
                    elif key == "STAFF_SUELDOS":
                        try: staff_sueldos = json.loads(value)
                        except json.JSONDecodeError: pass
                    elif key == "STAFF_EXTRAS_COUNT":
                        try: staff_extras_count = int(value)
                        except: pass
            
            # Valores por defecto si no se encuentran en la hoja
            if not formas_pago:
                formas_pago = [
                    {"nombre": "Efectivo", "total": 490, "modalidad": "mensual", "cuota": 49, "meses": 10, "tipo": "Efectivo"},
                    {"nombre": "Domiciliación", "total": 490, "modalidad": "mensual", "cuota": 49, "meses": 10, "tipo": "Domiciliación"},
                    {"nombre": "Transferencia", "total": 490, "modalidad": "mensual", "cuota": 49, "meses": 10, "tipo": "Transferencia"}
                ]

            return jsonify({"inscripciones": inscripciones, "formas_pago": formas_pago, "cuotas_mes": cuotas_mes, "extra_names": extra_names, "staff_sueldos": staff_sueldos, "staff_extras_count": staff_extras_count})
        
        elif request.method == 'POST':
            data = request.json
            # Guardado robusto de configuración en dos columnas
            config_sheet.update('A2', [
                ['INSCRIPCIONES_EQUIPO', json.dumps(data.get('inscripciones', {}))],
                ['FORMAS_PAGO', json.dumps(data.get('formas_pago', []))],
                ['CUOTAS_MES', json.dumps(data.get('cuotas_mes', {}))],
                ['EXTRA_NAMES', json.dumps(data.get('extra_names', {}))],
                ['STAFF_SUELDOS', json.dumps(data.get('staff_sueldos', {}))],
                ['STAFF_EXTRAS_COUNT', str(data.get('staff_extras_count', 1))]
            ], value_input_option='RAW')
            return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error api_config_financiera: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/sync_entrenadores_staff', methods=['POST'])
def api_sync_entrenadores_staff():
    from app import client, NOMBRE_EXCEL
    data = request.json
    try:
        sheet = client.open(NOMBRE_EXCEL).worksheet("STAFF")
        all_v = sheet.get_all_values()
        if not all_v:
            headers = ["CARGO", "NOMBRE", "TELEFONO", "EQUIPO", "DIAS ENTRENAMIENTO"]
            sheet.append_row(headers)
            all_v = [headers]
        headers_norm = normalizar_cabeceras(all_v[0])
        try:
            idx_nombre = headers_norm.index('NOMBRE')
        except ValueError:
            idx_nombre = 1
        nombres_existentes = set()
        for row in all_v[1:]:
            if len(row) > idx_nombre and row[idx_nombre].strip():
                nombres_existentes.add(row[idx_nombre].strip().lower())
        equipos = data.get('equipos', [])
        added = []
        for eq in equipos:
            equipo_nombre = eq.get('nombre', '').strip()
            for ent in eq.get('entrenadores', []):
                ent_clean = (ent or '').strip()
                if not ent_clean or ent_clean.lower() in nombres_existentes:
                    continue
                fila = [""] * max(len(headers_norm), 5)
                mapping = {'CARGO': 'Entrenador', 'NOMBRE': ent_clean, 'EQUIPO': equipo_nombre,
                           'TELEFONO': '', 'DIAS_ENTRENAMIENTO': ','.join(eq.get('dias', []))}
                for i, h in enumerate(headers_norm):
                    if h in mapping:
                        fila[i] = mapping[h]
                sheet.append_row(fila)
                nombres_existentes.add(ent_clean.lower())
                added.append(ent_clean)
        return jsonify({"status": "success", "added": added})
    except Exception as e:
        print(f"Error api_sync_entrenadores_staff: {e}")
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
                try:
                    df = pd.read_excel(file, engine='openpyxl').fillna("")
                except Exception as _e1:
                    if "zip" in str(_e1).lower() or "not a zip" in str(_e1).lower():
                        file.seek(0)
                        df = pd.read_excel(file, engine='xlrd').fillna("")
                    else:
                        raise _e1
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
            except ImportError as ie:
                print(f"ImportError leyendo Excel: {ie}")
                return jsonify({"status": "error", "message": f"Falta dependencia para leer Excel: {ie}. Ejecuta: pip install pandas openpyxl"}), 500
            except Exception as e:
                print(f"Error leyendo Excel: {type(e).__name__}: {e}")
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


# ─────────────────────────────────────────────
#  STAFF RESUMEN MES
# ─────────────────────────────────────────────
@financiero_bp.route('/api/staff_resumen_mes')
def api_staff_resumen_mes():
    import os, json as _json, glob as _glob, calendar as _cal
    from app import client, NOMBRE_EXCEL
    from datetime import datetime as _dt
    equipo = request.args.get('equipo', '').strip()
    mes = request.args.get('mes', '').strip().zfill(2)  # "01"-"12"
    nombre = request.args.get('nombre', '').strip().lower()
    if not equipo or not mes:
        return jsonify({"error": "equipo y mes requeridos"}), 400

    # Deducir año de la temporada actual (Sep-Ago)
    hoy = _dt.now()
    mes_int = int(mes)
    base_anio = hoy.year if hoy.month >= 9 else hoy.year - 1
    anio = base_anio if mes_int >= 9 else base_anio + 1

    resultado = {
        "dias_warning": 0,
        "sesiones_no_subidas": 0,
        "formularios_pendientes": 0,
        "balones_perdidos": 0,
        "faltas": 0,
        "suplencias": 0,
    }

    try:
        equipo_norm = equipo.strip().lower()
        sh = client.open(NOMBRE_EXCEL)

        # 1. ASISTENCIAS: días con datos incompletos o sin registrar
        try:
            def _nt(s):  # quitar tildes y pasar a minúsculas
                for a, b in [('á','a'),('é','e'),('í','i'),('ó','o'),('ú','u'),('ü','u'),
                              ('Á','a'),('É','e'),('Í','i'),('Ó','o'),('Ú','u')]:
                    s = s.replace(a, b)
                return s.strip().lower()
            # Días de entrenamiento desde config
            _DIA_MAP = {'lunes':0,'martes':1,'miercoles':2,'jueves':3,'viernes':4,'sabado':5,'domingo':6}
            dias_semana = []
            try:
                with open(os.path.join('static', 'data', 'equipos_config.json'), 'r', encoding='utf-8') as _fp:
                    for _eq in _json.load(_fp).get('equipos', []):
                        if _nt(_eq.get('nombre','')) == _nt(equipo):
                            dias_semana = [_DIA_MAP.get(_nt(d), -1) for d in _eq.get('dias', [])]
                            dias_semana = [d for d in dias_semana if d >= 0]
                            break
            except: pass

            fechas_entreno = set()
            _nm = _cal.monthrange(anio, mes_int)[1]
            for _d in range(1, _nm + 1):
                if _cal.weekday(anio, mes_int, _d) in dias_semana:
                    fechas_entreno.add(f"{_d:02d}/{mes_int:02d}/{anio}")

            ws_asis = client.open(NOMBRE_EXCEL).worksheet("ASISTENCIAS")
            all_asis = ws_asis.get_all_values()
            datos = {}  # fecha → [(estado, valoracion)]
            for row in (all_asis[1:] if all_asis else []):
                if len(row) < 3 or row[1].strip().lower() != equipo_norm: continue
                partes = row[0].strip().split('/')
                if len(partes) < 3: continue
                try:
                    fd, fm, fy = int(partes[0]), int(partes[1]), int(partes[2])
                    if fy < 100: fy += 2000
                except: continue
                if fm != mes_int or fy != anio: continue
                fecha = f"{fd:02d}/{fm:02d}/{fy}"
                estado = row[4].strip() if len(row) > 4 else ''
                val    = row[5].strip() if len(row) > 5 else ''
                datos.setdefault(fecha, []).append((estado, val))

            def _vacio(v): return not v or v.strip() == '-'
            dias_warning = 0
            fechas_base = fechas_entreno if fechas_entreno else set(datos.keys())
            for fecha in fechas_base:
                if fecha not in datos:
                    dias_warning += 1
                else:
                    if any(_vacio(e) or (e.upper() == 'SI' and _vacio(v)) for e, v in datos[fecha]):
                        dias_warning += 1
            resultado['dias_warning'] = dias_warning
        except Exception as e:
            print(f"staff_resumen_mes ASISTENCIAS: {e}")

        # 2. Sesiones no subidas = días totales del mes – sesiones subidas
        try:
            equipo_folder = equipo.upper().replace(' ', '_')
            sesiones_dir = os.path.join('static', 'data', 'sesiones', equipo_folder)
            sesiones_subidas = 0
            if os.path.exists(sesiones_dir):
                for f in os.listdir(sesiones_dir):
                    if not f.endswith('.json'): continue
                    try:
                        with open(os.path.join(sesiones_dir, f), 'r', encoding='utf-8') as fp:
                            meta = _json.load(fp)
                        fecha_ses = meta.get('fecha', '')
                        partes_ses = fecha_ses.split('/')
                        if len(partes_ses) >= 3 and partes_ses[1].zfill(2) == mes:
                            try:
                                if int(partes_ses[2]) == anio:
                                    sesiones_subidas += 1
                            except: pass
                        elif len(partes_ses) == 2 and partes_ses[1].zfill(2) == mes:
                            sesiones_subidas += 1
                    except: pass
            total_dias_mes = _cal.monthrange(anio, mes_int)[1]
            resultado['sesiones_no_subidas'] = max(0, total_dias_mes - sesiones_subidas)
        except Exception as e:
            print(f"staff_resumen_mes sesiones: {e}")

        # 3. Formularios pendientes = sábados del mes – formularios enviados
        try:
            _nm2 = _cal.monthrange(anio, mes_int)[1]
            saturdays = sum(1 for _d in range(1, _nm2 + 1) if _cal.weekday(anio, mes_int, _d) == 5)
            resultado['formularios_pendientes'] = saturdays  # default si falla gspread
            ws_form = client.open(NOMBRE_EXCEL).worksheet('FORMULARIO_PARTIDOS')
            all_form = ws_form.get_all_values()
            count_enviados = 0
            if all_form and len(all_form) > 1:
                hdrs = [h.strip().upper().replace(' ', '') for h in all_form[0]]
                idx_eq = next((i for i, h in enumerate(hdrs) if 'EQUIPO' in h), 1)
                idx_fe = next((i for i, h in enumerate(hdrs) if 'MARCA' in h or 'FECHA' in h), 0)
                for row in all_form[1:]:
                    if len(row) <= max(idx_eq, idx_fe): continue
                    if row[idx_eq].strip().lower() != equipo_norm: continue  # match exacto
                    fecha_str = row[idx_fe].strip()
                    if not fecha_str: continue
                    pf = fecha_str.split()[0].split('/')  # "DD/MM/YYYY HH:MM:SS" → ["DD","MM","YYYY"]
                    try:
                        if len(pf) >= 3 and int(pf[1]) == mes_int and int(pf[2]) == anio:
                            count_enviados += 1
                    except: pass
            resultado['formularios_pendientes'] = max(0, saturdays - count_enviados)
        except Exception as e:
            print(f"staff_resumen_mes formularios: {e}")

        # 4. Balones perdidos (JSON en static/data/)
        try:
            balones_perdidos = 0
            patron = os.path.join('static', 'data', f'balones_{equipo}_*.json')
            for bpath in _glob.glob(patron):
                try:
                    with open(bpath, 'r', encoding='utf-8') as fp:
                        bdata = _json.load(fp)
                    for _k, vals in bdata.items():
                        ini_list = vals.get('inicio', [])
                        fin_list = vals.get('final', [])
                        if not ini_list or not fin_list: continue
                        ts = ini_list[-1].get('timestamp', '')
                        try:
                            ts_mes = ts.split('/')[1].zfill(2) if '/' in ts else ''
                        except: ts_mes = ''
                        if ts_mes != mes: continue
                        try:
                            q_ini = int(ini_list[-1].get('cantidad', 0))
                            q_fin = int(fin_list[-1].get('cantidad', 0))
                            if q_ini > q_fin:
                                balones_perdidos += q_ini - q_fin
                        except: pass
                except: pass
            resultado['balones_perdidos'] = balones_perdidos
        except Exception as e:
            print(f"staff_resumen_mes balones: {e}")

        # 5. Faltas y suplencias del entrenador (ASISTENCIAS_STAFF)
        if nombre:
            try:
                ws_staff_asis = sh.worksheet("ASISTENCIAS_STAFF")
                all_sa = ws_staff_asis.get_all_values()
                faltas = 0
                suplencias = 0
                for row in (all_sa[1:] if all_sa else []):
                    if len(row) < 4: continue
                    if row[1].strip().lower() != equipo.strip().lower(): continue
                    if row[2].strip().lower() != nombre: continue
                    partes_sa = row[0].strip().split('/')
                    if len(partes_sa) < 2 or partes_sa[1].zfill(2) != mes: continue
                    estado_sa = row[3].strip().upper()
                    if estado_sa in ('FALTA', 'FALTA_SUPLIDA'):
                        faltas += 1
                    elif estado_sa == 'SUPLENCIA':
                        suplencias += 1
                resultado['faltas'] = faltas
                resultado['suplencias'] = suplencias
            except Exception as e:
                print(f"staff_resumen_mes ASISTENCIAS_STAFF: {e}")

        return jsonify(resultado)

    except Exception as e:
        print(f"Error staff_resumen_mes: {e}")
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
#  MULTAS CONFIG
# ─────────────────────────────────────────────
_MULTAS_CONFIG_PATH = os.path.join('static', 'data', 'multas_config.json')
_MULTAS_DEFAULT = {
    "perdida_balon": 10,
    "falta": 5,
    "suplencia": 10,
    "penalizacion_asistencias": 5,
    "umbral_asistencias": 2,
    "penalizacion_entrenamientos": 5,
    "umbral_entrenamientos": 2,
    "penalizacion_formularios": 2,
    "umbral_formularios": 2,
}

@financiero_bp.route('/api/multas_config', methods=['GET', 'POST'])
def api_multas_config():
    import os, json as _json
    if request.method == 'GET':
        cfg = dict(_MULTAS_DEFAULT)
        if os.path.exists(_MULTAS_CONFIG_PATH):
            try:
                with open(_MULTAS_CONFIG_PATH, 'r', encoding='utf-8') as f:
                    cfg.update(_json.load(f))
            except: pass
        return jsonify(cfg)
    data = request.get_json() or {}
    os.makedirs(os.path.dirname(_MULTAS_CONFIG_PATH), exist_ok=True)
    with open(_MULTAS_CONFIG_PATH, 'w', encoding='utf-8') as f:
        import json as _json2; _json2.dump(data, f)
    return jsonify({"status": "success"})


# ─────────────────────────────────────────────
#  ASISTENCIAS STAFF
# ─────────────────────────────────────────────
_ASIS_STAFF_HEADERS = ["FECHA", "EQUIPO", "NOMBRE", "ESTADO", "DETALLE"]

def _get_asis_staff_sheet():
    from app import client, NOMBRE_EXCEL
    sh = client.open(NOMBRE_EXCEL)
    try:
        return sh.worksheet("ASISTENCIAS_STAFF")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="ASISTENCIAS_STAFF", rows="2000", cols="6")
        ws.append_row(_ASIS_STAFF_HEADERS)
        return ws

def _asis_staff_upsert(ws, all_v, fecha, equipo, nombre, estado, detalle):
    for i, row in enumerate(all_v[1:], start=2):
        if (len(row) > 2 and
                row[0].strip() == fecha and
                row[1].strip().lower() == equipo.lower() and
                row[2].strip().lower() == nombre.lower()):
            ws.update(f'A{i}', [[fecha, equipo, nombre, estado, detalle]])
            return
    ws.append_row([fecha, equipo, nombre, estado, detalle])

@financiero_bp.route('/api/asistencias_staff', methods=['GET'])
def api_asistencias_staff_get():
    equipo = request.args.get('equipo', '').strip()
    mes = request.args.get('mes', '').strip().zfill(2)
    try:
        ws = _get_asis_staff_sheet()
        all_v = ws.get_all_values()
        result = []
        equipo_norm = equipo.lower()
        for row in (all_v[1:] if all_v else []):
            if not any(c.strip() for c in row): continue
            if len(row) < 3: continue
            if row[1].strip().lower() != equipo_norm: continue
            partes = row[0].strip().split('/')
            if len(partes) < 2 or partes[1].zfill(2) != mes: continue
            result.append({
                'FECHA': row[0].strip(),
                'EQUIPO': row[1].strip() if len(row) > 1 else '',
                'NOMBRE': row[2].strip() if len(row) > 2 else '',
                'ESTADO': row[3].strip() if len(row) > 3 else '',
                'DETALLE': row[4].strip() if len(row) > 4 else '',
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@financiero_bp.route('/api/asistencias_staff/guardar', methods=['POST'])
def api_asistencias_staff_guardar():
    data = request.get_json() or {}
    fecha = data.get('fecha', '').strip()
    equipo = data.get('equipo', '').strip()
    nombre = data.get('nombre', '').strip()
    estado = data.get('estado', 'SI').strip().upper()
    detalle = data.get('detalle', '').strip()
    try:
        ws = _get_asis_staff_sheet()
        all_v = ws.get_all_values()
        _asis_staff_upsert(ws, all_v, fecha, equipo, nombre, estado, detalle)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@financiero_bp.route('/api/staff_lista', methods=['GET'])
def api_staff_lista():
    """Devuelve la lista de staff (nombre, equipo, cargo) para el modal de asistencias."""
    from app import client, NOMBRE_EXCEL
    try:
        sh = client.open(NOMBRE_EXCEL)
        ws = sh.worksheet("STAFF")
        all_v = ws.get_all_values()
        if not all_v or len(all_v) < 2:
            return jsonify([])
        headers = [h.strip().upper() for h in all_v[0]]
        result = []
        for row in all_v[1:]:
            if not any(c.strip() for c in row): continue
            obj = {}
            for i, h in enumerate(headers):
                obj[h] = row[i].strip() if i < len(row) else ''
            result.append(obj)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@financiero_bp.route('/api/asistencias_staff/suplencia', methods=['POST'])
def api_asistencias_staff_suplencia():
    data = request.get_json() or {}
    fecha = data.get('fecha', '').strip()
    equipo = data.get('equipo', '').strip()
    suplente = data.get('suplente', '').strip()   # quien hace la suplencia
    suplido = data.get('suplido', '').strip()     # quien recibe (faltó)
    try:
        ws = _get_asis_staff_sheet()
        all_v = ws.get_all_values()
        _asis_staff_upsert(ws, all_v, fecha, equipo, suplente, 'SUPLENCIA', f'Suplencia a: {suplido}')
        all_v2 = ws.get_all_values()
        _asis_staff_upsert(ws, all_v2, fecha, equipo, suplido, 'FALTA_SUPLIDA', f'Suplencia de: {suplente}')
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
#  SUBVENCIONES
# ─────────────────────────────────────────────
SUBVENCIONES_HEADERS = ["ID", "NOMBRE", "DESCRIPCION", "FECHA_APERTURA", "PLAZO_MAXIMO", "FECHA_PRESENTADA", "IMPORTE", "COBRADO", "FECHA_COBRO", "ADJUNTOS"]

def _get_subvenciones_sheet():
    from app import client, NOMBRE_EXCEL
    try:
        sh = client.open(NOMBRE_EXCEL).worksheet("SUBVENCIONES")
    except gspread.exceptions.WorksheetNotFound:
        sh = client.open(NOMBRE_EXCEL).add_worksheet(title="SUBVENCIONES", rows="200", cols="12")
        sh.append_row(SUBVENCIONES_HEADERS)
        return sh
    # Migración: añadir cualquier header requerido que falte
    try:
        all_v = sh.get_all_values()
        if all_v:
            headers_actual = [h.strip().upper() for h in all_v[0]]
            for req_h in SUBVENCIONES_HEADERS:
                if req_h not in headers_actual:
                    headers_actual.append(req_h)
                    sh.update_cell(1, len(headers_actual), req_h)
    except Exception:
        pass
    return sh

def _subvenciones_dir():
    base = os.path.join(os.path.dirname(__file__), 'static', 'data', 'subvenciones')
    os.makedirs(base, exist_ok=True)
    return base

@financiero_bp.route('/api/subvenciones', methods=['GET'])
def api_subvenciones_get():
    try:
        sh = _get_subvenciones_sheet()
        rows = sh.get_all_values()
        if not rows or len(rows) < 2:
            return jsonify([])
        headers = [h.strip().upper() for h in rows[0]]
        result = []
        for row_idx, row in enumerate(rows[1:], start=2):
            if not any(str(c).strip() for c in row):
                continue
            obj = {'_row_idx': row_idx}
            for h in SUBVENCIONES_HEADERS:
                idx = headers.index(h) if h in headers else -1
                obj[h] = row[idx] if idx != -1 and idx < len(row) else ""
            result.append(obj)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/subvenciones/guardar', methods=['POST'])
def api_subvenciones_guardar():
    try:
        data = request.get_json()
        sh = _get_subvenciones_sheet()
        all_v = sh.get_all_values()
        headers = [h.strip().upper() for h in all_v[0]] if all_v else SUBVENCIONES_HEADERS

        sid = str(data.get('ID', '')).strip()
        row_idx = data.get('_row_idx')

        # Buscar fila existente — primero por _row_idx, luego por ID
        idx_existente = -1
        if row_idx:
            target = int(row_idx)
            if 2 <= target <= len(all_v):
                idx_existente = target
        elif sid:
            id_col = headers.index('ID') if 'ID' in headers else 0
            for i, row in enumerate(all_v[1:], start=2):
                if row and str(row[id_col] if id_col < len(row) else '').strip() == sid:
                    idx_existente = i
                    break

        # Si es nuevo, generar ID
        if idx_existente == -1:
            max_id = 0
            for row in all_v[1:]:
                try:
                    val = int(str(row[0]).strip())
                    max_id = max(max_id, val)
                except: pass
            sid = str(max_id + 1)
            data['ID'] = sid

        nueva_fila = [data.get(h, '') for h in headers]

        if idx_existente != -1:
            sh.update(f'A{idx_existente}', [nueva_fila])
        else:
            sh.append_row(nueva_fila)

        return jsonify({"status": "success", "ID": sid})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/subvenciones/eliminar', methods=['POST'])
def api_subvenciones_eliminar():
    try:
        data = request.get_json()
        row_idx = data.get('_row_idx')
        sid = str(data.get('ID', '')).strip()
        from app import client, NOMBRE_EXCEL
        sh = client.open(NOMBRE_EXCEL).worksheet("SUBVENCIONES")
        if row_idx:
            sh.delete_rows(int(row_idx))
            return jsonify({"status": "success"})
        # Fallback: buscar por ID en columna 0
        all_v = sh.get_all_values()
        headers = [h.strip().upper() for h in all_v[0]] if all_v else SUBVENCIONES_HEADERS
        id_col = headers.index('ID') if 'ID' in headers else 0
        for i, row in enumerate(all_v[1:], start=2):
            if row and str(row[id_col] if id_col < len(row) else '').strip() == sid:
                sh.delete_rows(i)
                return jsonify({"status": "success"})
        return jsonify({"status": "not_found"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/subvenciones/adjunto', methods=['POST'])
def api_subvenciones_adjunto():
    try:
        import base64, mimetypes
        data = request.get_json()
        sid = str(data.get('ID', '')).strip()
        nombre_archivo = data.get('nombre', 'adjunto')
        contenido_b64 = data.get('contenido', '')

        safe_name = re.sub(r'[^a-zA-Z0-9._\-]', '_', nombre_archivo)
        fname = f"{sid}_{safe_name}"
        fpath = os.path.join(_subvenciones_dir(), fname)

        with open(fpath, 'wb') as f:
            f.write(base64.b64decode(contenido_b64))

        # Actualizar columna ADJUNTOS en la hoja
        sh = _get_subvenciones_sheet()
        all_v = sh.get_all_values()
        headers = [h.strip().upper() for h in all_v[0]] if all_v else SUBVENCIONES_HEADERS
        idx_adj = headers.index('ADJUNTOS') if 'ADJUNTOS' in headers else -1

        for i, row in enumerate(all_v[1:], start=2):
            if row and str(row[0]).strip() == sid:
                existing = row[idx_adj] if idx_adj != -1 and idx_adj < len(row) else ''
                adjuntos_list = [a for a in existing.split(',') if a.strip()]
                if fname not in adjuntos_list:
                    adjuntos_list.append(fname)
                sh.update_cell(i, idx_adj + 1, ','.join(adjuntos_list))
                break

        return jsonify({"status": "success", "archivo": fname})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/api/subvenciones/adjunto/<path:fname>', methods=['DELETE'])
def api_subvenciones_adjunto_delete(fname):
    try:
        data = request.get_json() or {}
        sid = str(data.get('ID', '')).strip()
        fpath = os.path.join(_subvenciones_dir(), fname)
        if os.path.exists(fpath):
            os.remove(fpath)
        if sid:
            sh = _get_subvenciones_sheet()
            all_v = sh.get_all_values()
            headers = [h.strip().upper() for h in all_v[0]] if all_v else SUBVENCIONES_HEADERS
            idx_adj = headers.index('ADJUNTOS') if 'ADJUNTOS' in headers else -1
            for i, row in enumerate(all_v[1:], start=2):
                if row and str(row[0]).strip() == sid:
                    existing = row[idx_adj] if idx_adj != -1 and idx_adj < len(row) else ''
                    adjuntos_list = [a for a in existing.split(',') if a.strip() and a != fname]
                    sh.update_cell(i, idx_adj + 1, ','.join(adjuntos_list))
                    break
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@financiero_bp.route('/static/data/subvenciones/<path:fname>', methods=['GET'])
def api_subvenciones_archivo(fname):
    from flask import send_from_directory
    return send_from_directory(_subvenciones_dir(), fname)


# ─────────────────────────────────────────────
#  GENERAR REMESA BANCARIA
# ─────────────────────────────────────────────
TEAM_CODES_REMESA = {
    'CHUPETIN': 'CHU',
    'PREBENJAMIN A': 'PRA', 'PREBENJAMIN B': 'PRB', 'PREBENJAMIN C': 'PRC',
    'BENJAMIN A': 'BEA', 'BENJAMIN B': 'BEB', 'BENJAMIN C': 'BEC',
    'ALEVIN F7 A': 'A7A', 'ALEVIN F7 B': 'A7B',
    'ALEVIN FEM': 'ALF',
    'ALEVIN F11 A': 'A1A', 'ALEVIN F11 B': 'A1B',
    'INFANTIL A': 'INA', 'INFANTIL B': 'INB',
    'CADETE A': 'CDA', 'CADETE B': 'CDB',
    'JUVENIL A': 'JUA', 'JUVENIL B': 'JUB',
    'AFICIONADO A': 'AFA', 'AFICIONADO B': 'AFB',
}
MES_NUM = {'ENE':'01','FEB':'02','MAR':'03','ABR':'04','MAY':'05','JUN':'06',
           'JUL':'07','AGO':'08','SEP':'09','OCT':'10','NOV':'11','DIC':'12'}
MES_LABEL = {v: k for k, v in MES_NUM.items()}

@financiero_bp.route('/api/estado_pagos_jugadores', methods=['GET'])
def api_estado_pagos_jugadores():
    """Combina PAGOS JUGADORES + FINANCIERO para devolver estado completo de pagos."""
    from app import client, NOMBRE_EXCEL
    try:
        wb = client.open(NOMBRE_EXCEL)
        resultado = []
        pj_keys = set()  # (nombre_lower, equipo_lower, concepto) ya cubiertos por PAGOS JUGADORES

        # Construir lookup de remesas guardadas: (jugador_lower, pj_concepto) → {'info': str, 'cobrada': bool}
        remesa_lookup = {}
        try:
            with open(_REMESAS_PATH, 'r', encoding='utf-8') as _f:
                _hist = json.load(_f)
            for _entrada in _hist:
                _info    = f"{_entrada.get('titulo','')} · {_entrada.get('fecha','')}"
                _cobrada = bool(_entrada.get('cobrada', False))
                for _lin in _entrada.get('lineas', []):
                    _jug = str(_lin.get('jugador','')).strip().lower()
                    _lin_conciliada = bool(_lin.get('conciliada', False))
                    _des = _lin.get('desglose', [])
                    if _des:
                        for _d in _des:
                            _dc = str(_d.get('concepto', '')).strip().rstrip('*')
                            _pj = MES_NUM.get(_dc.upper()) or \
                                  ('Inscripción' if _dc.upper() == 'INSC' else
                                   ('PACK_ROPA'   if _dc.upper() == 'ROPA' else _dc))
                            _key = (_jug, _pj)
                            if _key not in remesa_lookup:
                                remesa_lookup[_key] = {'info': _info, 'cobrada': _cobrada or _lin_conciliada}
                    else:
                        _key = (_jug, str(_lin.get('concepto','')).strip())
                        if _key not in remesa_lookup:
                            remesa_lookup[_key] = {'info': _info, 'cobrada': _cobrada or _lin_conciliada}
        except Exception:
            pass

        # 1. PAGOS JUGADORES — fuente primaria (nuevos pagos registrados desde la UI)
        try:
            sh_pj = wb.worksheet("PAGOS JUGADORES")
            for r in sh_pj.get_all_records():
                nombre   = str(r.get('NOMBRE', '')).strip()
                equipo   = str(r.get('EQUIPO', '')).strip()
                concepto = str(r.get('CONCEPTO', '')).strip()
                pagado   = str(r.get('PAGADO', '0')).strip()
                esperado = str(r.get('ESPERADO', '0')).strip()
                if not nombre or not concepto:
                    continue
                _r = remesa_lookup.get((nombre.lower(), concepto), {})
                remesa          = _r.get('info', '')    if _r else ''
                remesa_cobrada  = _r.get('cobrada', False) if _r else False
                resultado.append({'NOMBRE': nombre, 'EQUIPO': equipo,
                                   'CONCEPTO': concepto, 'PAGADO': pagado,
                                   'ESPERADO': esperado, 'REMESA': remesa,
                                   'REMESA_COBRADA': remesa_cobrada})
                pj_keys.add((nombre.lower(), equipo.lower(), concepto))
        except Exception:
            pass

        # 2. FINANCIERO — fuente secundaria para pagos históricos no en PAGOS JUGADORES
        try:
            sh_fin = wb.worksheet("FINANCIERO")
            all_fin = sh_fin.get_all_values()
            if len(all_fin) > 1:
                hdrs = normalizar_cabeceras(all_fin[0])
                def col(name): return hdrs.index(name) if name in hdrs else -1
                i_pilar = col('PILAR'); i_nom = col('NOMBRE'); i_eq = col('EQUIPO')
                i_con = col('CONCEPTO'); i_imp = col('IMPORTE'); i_esp = col('ESPERADO')
                i_desc = col('DESCRIPCION'); i_fecha = col('FECHA')

                for row in all_fin[1:]:
                    def v(i): return str(row[i]).strip() if 0 <= i < len(row) else ''
                    pilar = v(i_pilar).upper()
                    if 'CUOTA' not in pilar:
                        continue
                    desc   = v(i_desc)
                    nombre = v(i_nom)
                    if not nombre and i_desc >= 0:
                        m = re.search(r'[:\-]\s*([^:\-]+)$', desc)
                        if m:
                            nombre = m.group(1).strip()
                    if not nombre:
                        continue
                    equipo   = v(i_eq)
                    concepto = v(i_con)
                    if not concepto:
                        continue
                    key = (nombre.lower(), equipo.lower(), concepto)
                    if key in pj_keys:
                        continue
                    # Extraer info de remesa desde DESCRIPCION o lookup
                    remesa = ''
                    remesa_cobrada = False
                    if desc.upper().startswith('REMESA '):
                        partes = desc.split(' · ', 1)
                        remesa_titulo = partes[0][len('REMESA '):]
                        remesa_fecha  = v(i_fecha)
                        remesa = f"{remesa_titulo} · {remesa_fecha}" if remesa_titulo else ''
                    if not remesa:
                        _r2 = remesa_lookup.get((nombre.lower(), concepto), {})
                        remesa         = _r2.get('info', '')    if _r2 else ''
                        remesa_cobrada = _r2.get('cobrada', False) if _r2 else False
                    resultado.append({'NOMBRE': nombre, 'EQUIPO': equipo,
                                      'CONCEPTO': concepto, 'PAGADO': v(i_imp),
                                      'ESPERADO': v(i_esp), 'REMESA': remesa,
                                      'REMESA_COBRADA': remesa_cobrada})
                    pj_keys.add(key)
        except Exception:
            pass

        return jsonify({'status': 'success', 'data': resultado})
    except Exception as e:
        import traceback; print(traceback.format_exc())
        return jsonify({'status': 'error', 'message': str(e)}), 500

@financiero_bp.route('/api/generar_remesa', methods=['POST'])
def api_generar_remesa():
    import unicodedata
    from app import client, NOMBRE_EXCEL
    params = request.json or {}
    meses_sel   = params.get('meses', [])          # ['ENE','FEB',...]
    impagos     = params.get('impagos', False)
    formas_pago = [f.strip().upper() for f in params.get('formas_pago', [])]
    incluir_ropa = params.get('ropa', False)
    incluir_insc = params.get('incluir_insc', False)
    config_cuotas = params.get('config_cuotas', {})        # {EQUIPO-01: 44}
    config_insc   = params.get('config_inscripciones', {}) # {EQUIPO: 10}
    grupos_anchor = params.get('grupos_anchor', {})        # {'04':'03','10':'09',...}
    grupos_labels = params.get('grupos_labels', {})        # {'SEP':'SEP-OCT-NOV','DIC':'DIC-ENE-FEB',...}
    anio_actual = params.get('anio', 26)  # últimos 2 dígitos del año

    def norm(s):
        s = str(s).strip().upper()
        nfkd = unicodedata.normalize('NFKD', s)
        return ' '.join(''.join(c for c in nfkd if not unicodedata.category(c).startswith('M')).split())

    def get_code(equipo):
        eq = norm(equipo)
        return TEAM_CODES_REMESA.get(eq, eq[:3])

    try:
        wb = client.open(NOMBRE_EXCEL)

        # 0. Leer remesas pendientes → excluir jugadores/meses ya en otra remesa sin cobrar
        ya_en_remesa = set()  # (norm_nombre, mes_label_upper)  ej: ('HUGO', 'SEP')
        try:
            with open(_REMESAS_PATH, 'r', encoding='utf-8') as _f:
                _historial = json.load(_f)
            for _r in _historial:
                if _r.get('cobrada'):
                    continue  # remesa ya cobrada → no bloquea
                for _lin in (_r.get('lineas') or []):
                    _nombre = norm(str(_lin.get('jugador', '')))
                    for _d in (_lin.get('desglose') or []):
                        _con = str(_d.get('concepto', '')).replace('*', '').strip().upper()
                        if _nombre and _con:
                            ya_en_remesa.add((_nombre, _con))
        except Exception:
            pass

        # 1. Jugadores (roster)
        jugadores = leer_hoja_limpia(client, NOMBRE_EXCEL, "JUGADORES")

        # 1b. Datos de ropa: packs + prendas individuales (CONFIGURACION + hoja ROPA)
        pack_config_ropa = {"pequeno": {"prendas": [], "precio": 0}, "grande": {"prendas": [], "precio": 0}}
        jugadores_pack_ropa = {}   # "EQ_NORM|||NOM_NORM" → "pequeno"|"grande"
        precios_prendas_ropa = {}  # prenda_nombre → precio
        # (nombre_norm, equipo_norm) → total esperado de prendas individuales
        ropa_prendas_esperado = {}
        if incluir_ropa:
            try:
                todas_hojas_conf = {s.title.upper().strip(): s for s in wb.worksheets()}
                if "CONFIGURACION" in todas_hojas_conf:
                    conf_vals = todas_hojas_conf["CONFIGURACION"].get_all_values()
                    for _row in conf_vals:
                        if len(_row) < 2:
                            continue
                        if _row[0].strip() == 'PACK_ROPA_CONFIG':
                            try:
                                pack_config_ropa = json.loads(_row[1])
                            except Exception:
                                pass
                        elif _row[0].strip() == 'JUGADORES_PACK_ROPA':
                            try:
                                _raw = json.loads(_row[1])
                                for _k, _v in _raw.items():
                                    _parts = _k.split('|||')
                                    if len(_parts) == 2:
                                        jugadores_pack_ropa[norm(_parts[0]) + '|||' + norm(_parts[1])] = _v
                            except Exception:
                                pass
                        elif _row[0].strip() == 'PRECIOS_PRENDAS':
                            try:
                                precios_prendas_ropa = json.loads(_row[1])
                            except Exception:
                                pass
            except Exception:
                pass

            # Prendas que forman parte de algún pack → no se cobran sueltas
            _prendas_en_pack = set(
                (pack_config_ropa.get('grande') or {}).get('prendas', []) +
                (pack_config_ropa.get('pequeno') or {}).get('prendas', [])
            )

            # Leer hoja ROPA para calcular total de prendas individuales por jugador
            try:
                _ropa_vals = wb.worksheet("ROPA").get_all_values()
                if len(_ropa_vals) > 1:
                    _hdr = _ropa_vals[0]  # ['FECHA','EQUIPO','JUGADOR','PRENDA1',...]
                    # Prendas individuales empiezan en índice 3
                    _prendas_hdr = _hdr[3:] if len(_hdr) > 3 else []
                    for _rrow in _ropa_vals[1:]:
                        _req  = norm(str(_rrow[1])) if len(_rrow) > 1 else ''  # col B = EQUIPO
                        _rnom = norm(str(_rrow[2])) if len(_rrow) > 2 else ''  # col C = JUGADOR
                        if not _req or not _rnom:
                            continue
                        _total_prendas = 0.0
                        for _pi, _pnm in enumerate(_prendas_hdr):
                            if _pnm in _prendas_en_pack:
                                continue  # pertenece a pack, no se suma suelto
                            _cell_val = _rrow[_pi + 3] if (_pi + 3) < len(_rrow) else ''
                            if _cell_val and _cell_val.strip():
                                _talla = _cell_val.split('|')[0].strip()
                                if _talla:
                                    _total_prendas += float(precios_prendas_ropa.get(_pnm, 0) or 0)
                        if _total_prendas > 0:
                            ropa_prendas_esperado[(_rnom, _req)] = _total_prendas
            except Exception:
                pass

        precio_pack_pequeno = float((pack_config_ropa.get('pequeno') or {}).get('precio') or 0)
        precio_pack_grande  = float((pack_config_ropa.get('grande')  or {}).get('precio') or 0)

        # 2. PAGOS JUGADORES → historial de lo ya cobrado
        pagados_idx = {}  # (norm_nombre, norm_equipo, concepto) → total_pagado
        esperado_idx = {}
        try:
            sh_pj = wb.worksheet("PAGOS JUGADORES")
            for r in sh_pj.get_all_records():
                kn  = norm(str(r.get('NOMBRE', '')))
                keq = norm(str(r.get('EQUIPO', '')))
                con = str(r.get('CONCEPTO', '')).strip()
                pag = float(str(r.get('PAGADO', 0)).replace(',', '.') or 0)
                esp = float(str(r.get('ESPERADO', 0)).replace(',', '.') or 0)
                k = (kn, keq, con)
                pagados_idx[k]  = pagados_idx.get(k, 0) + pag
                esperado_idx[k] = max(esperado_idx.get(k, 0), esp)
        except Exception:
            pass

        # 2b. FINANCIERO → complementa pagados_idx con pagos no registrados en PAGOS JUGADORES
        try:
            sh_fin = wb.worksheet("FINANCIERO")
            all_fin = sh_fin.get_all_values()
            if len(all_fin) > 1:
                hdrs_fin = normalizar_cabeceras(all_fin[0])
                def _col(n): return hdrs_fin.index(n) if n in hdrs_fin else -1
                _i_pilar = _col('PILAR'); _i_nom = _col('NOMBRE'); _i_eq = _col('EQUIPO')
                _i_con = _col('CONCEPTO'); _i_imp = _col('IMPORTE'); _i_esp = _col('ESPERADO')
                for _row in all_fin[1:]:
                    def _v(i): return str(_row[i]).strip() if 0 <= i < len(_row) else ''
                    if 'CUOTA' not in _v(_i_pilar).upper():
                        continue
                    _kn  = norm(_v(_i_nom))
                    _keq = norm(_v(_i_eq))
                    _con = _v(_i_con)
                    if not _kn or not _con:
                        continue
                    _k = (_kn, _keq, _con)
                    if _k in pagados_idx:
                        continue  # ya cubierto por PAGOS JUGADORES, no sobreescribir
                    _pag = float(str(_v(_i_imp)).replace(',', '.') or 0)
                    _esp = float(str(_v(_i_esp)).replace(',', '.') or 0)
                    pagados_idx[_k]  = pagados_idx.get(_k, 0) + _pag
                    esperado_idx[_k] = max(esperado_idx.get(_k, 0), _esp)
        except Exception:
            pass

        # 3. DATOS JUGADORES → IBAN, titular, apellidos
        datos_jug_idx = {}
        try:
            for d in wb.worksheet("DATOS JUGADORES").get_all_records():
                kn  = norm(str(d.get('JUGADOR_NOMBRE', '')))
                keq = norm(str(d.get('JUGADOR_EQUIPO_LETRA', '')))
                if kn:
                    datos_jug_idx[(kn, keq)] = d
        except Exception:
            pass

        # 4. Construir filas de remesa
        team_counter = {}
        filas = []

        for jug in jugadores:
            forma = norm(str(jug.get('FORMA_PAGO', jug.get('FORMA PAGO', ''))))
            if formas_pago and forma not in formas_pago:
                continue

            nombre_corto = norm(str(jug.get('NOMBRE', '')))
            equipo       = norm(str(jug.get('EQUIPO', '')))
            if not nombre_corto or not equipo:
                continue

            # Obtener datos de ficha
            datos_ficha = datos_jug_idx.get((nombre_corto, equipo)) or \
                          next((v for (kn, keq), v in datos_jug_idx.items()
                                if kn == nombre_corto and norm(keq).startswith(equipo[:5])), {})

            iban    = str(datos_ficha.get('SEPA_IBAN', '')).strip().replace(' ', '')
            titular = str(datos_ficha.get('SEPA_NOMBRE_DEUDOR', '')).strip()
            apellidos = str(datos_ficha.get('JUGADOR_APELLIDOS', '')).strip()
            nombre_largo = f"{apellidos} {nombre_corto}".strip() if apellidos else nombre_corto

            # Fecha de firma del mandato SEPA (extraída de CIERRE_FECHA_LOCALIDAD)
            _fecha_loc = str(datos_ficha.get('CIERRE_FECHA_LOCALIDAD', '')).strip()
            _m = re.search(r'\b(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})\b', _fecha_loc)
            firma_sepa = _m.group(1).replace('-', '/') if _m else ''

            desglose = []

            # Meses seleccionados
            for mes_label in meses_sel:
                mes_num = MES_NUM.get(mes_label.upper(), '')
                if not mes_num:
                    continue
                # Excluir si ya está en una remesa pendiente sin cobrar
                if (nombre_corto, mes_label.upper()) in ya_en_remesa:
                    continue
                pag = pagados_idx.get((nombre_corto, equipo, mes_num), 0)
                esp = float(config_cuotas.get(f"{equipo}-{mes_num}",
                       config_cuotas.get(f"{jug.get('EQUIPO','')}-{mes_num}", 0)) or 0)
                pendiente = max(0, esp - pag)
                if pendiente > 0:
                    desglose.append({'concepto': mes_label, 'importe': round(pendiente, 2)})

            # Impagos pasados — agrupa meses del mismo trimestre por su anchor
            if impagos:
                meses_sel_nums = {MES_NUM.get(m, '') for m in meses_sel}
                impago_por_anchor = {}  # anchor_num → {pagado, esperado}
                for con, pag in pagados_idx.items():
                    kn, keq, concepto = con
                    if kn != nombre_corto or keq != equipo:
                        continue
                    if concepto not in MES_LABEL:
                        continue
                    anchor = grupos_anchor.get(concepto, concepto)
                    if anchor in meses_sel_nums:
                        continue  # ya cubierto por los meses seleccionados
                    esp = esperado_idx.get(con, 0)
                    if anchor not in impago_por_anchor:
                        impago_por_anchor[anchor] = {'pagado': 0, 'esperado': 0}
                    impago_por_anchor[anchor]['pagado'] += pag
                    impago_por_anchor[anchor]['esperado'] = max(impago_por_anchor[anchor]['esperado'], esp)
                for anchor, vals in sorted(impago_por_anchor.items()):
                    anchor_label = MES_LABEL.get(anchor, anchor)
                    # Excluir si el anchor ya está en remesa pendiente
                    if (nombre_corto, anchor_label.upper()) in ya_en_remesa:
                        continue
                    pendiente = max(0, vals['esperado'] - vals['pagado'])
                    if pendiente > 0:
                        desglose.append({'concepto': anchor_label + '*', 'importe': round(pendiente, 2)})

            # Inscripción impagada (por impagos o por checkbox INSC explícito)
            if (impagos or incluir_insc):
                if (nombre_corto, 'INSC') not in ya_en_remesa:
                    pag_insc = pagados_idx.get((nombre_corto, equipo, 'Inscripción'), 0)
                    esp_insc = float(config_insc.get(equipo, config_insc.get(jug.get('EQUIPO',''), 0)) or 0)
                    if esp_insc > 0 and pag_insc < esp_insc:
                        desglose.append({'concepto': 'INSC*', 'importe': round(esp_insc - pag_insc, 2)})

            # ROPA pendiente — pack asignado o prendas individuales
            if incluir_ropa:
                _pack_key = f"{equipo}|||{nombre_corto}"
                _pack_tipo = jugadores_pack_ropa.get(_pack_key, '')
                if _pack_tipo == 'pequeno':
                    esp_ropa = precio_pack_pequeno
                elif _pack_tipo == 'grande':
                    esp_ropa = precio_pack_grande
                else:
                    # Sin pack → suma prendas individuales desde hoja ROPA
                    esp_ropa = ropa_prendas_esperado.get((nombre_corto, equipo), 0)
                # Lo ya pagado (columna PACK_ROPA en PAGOS JUGADORES)
                pag_ropa = pagados_idx.get((nombre_corto, equipo, 'PACK_ROPA'), 0)
                if esp_ropa > 0 and pag_ropa < esp_ropa:
                    desglose.append({'concepto': 'ROPA', 'importe': round(esp_ropa - pag_ropa, 2)})

            if not desglose:
                continue

            total = round(sum(d['importe'] for d in desglose), 2)
            cod_eq = get_code(equipo)
            team_counter[cod_eq] = team_counter.get(cod_eq, 0) + 1
            cod = f"{anio_actual}F{cod_eq}{team_counter[cod_eq]}"

            # Concepto: APELLIDO NOMBRE ENSENANZA FUTBOL meses
            # Solo TRIMESTRAL expande anchor → rango (SEP → SEP-OCT-NOV); MENSUAL deja la etiqueta tal cual
            tipo_pago_jug = norm(str(jug.get('TIPO_PAGO', jug.get('TIPO PAGO', '')))).upper()
            es_trimestral = tipo_pago_jug == 'TRIMESTRAL'
            def _expand(c):
                lbl = c.rstrip('*')
                suffix = '*' if c.endswith('*') else ''
                return (grupos_labels.get(lbl, lbl) if es_trimestral else lbl) + suffix
            conceptos_str = '+'.join(_expand(d['concepto']) for d in desglose)
            concepto_full = f"{nombre_largo} ENSENANZA FUTBOL {conceptos_str}".upper()

            filas.append({
                'cod': cod, 'jugador': nombre_largo.upper(),
                'firma': firma_sepa, 'cc': iban, 'titular': titular.upper(),
                'importe': total, 'desglose': desglose,
                'concepto': concepto_full, 'equipo': equipo
            })

        return jsonify({'status': 'success', 'data': filas})
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ── GUARDAR REMESA ────────────────────────────────────────────────
_REMESAS_PATH = os.path.join(os.path.dirname(__file__), 'static', 'data', 'remesas_historial.json')

@financiero_bp.route('/api/guardar_remesa', methods=['POST'])
def api_guardar_remesa():
    """Guarda el historial de remesa en JSON y actualiza celdas a azul.
    NO crea asientos en FINANCIERO: los asientos de cobro se crean más tarde,
    cuando el banco confirma el ingreso (vía Carga Masiva o asiento manual)."""
    from datetime import date
    try:
        datos = request.get_json()
        titulo = datos.get('titulo', 'Remesa sin título').strip()
        lineas = datos.get('lineas', [])
        if not lineas:
            return jsonify({'status': 'error', 'message': 'No hay líneas'}), 400

        hoy = date.today().strftime('%d/%m/%Y')

        # Historial JSON
        try:
            with open(_REMESAS_PATH, 'r', encoding='utf-8') as f:
                historial = json.load(f)
        except Exception:
            historial = []

        historial.insert(0, {
            'titulo':   titulo,
            'fecha':    hoy,
            'cobrada':  False,
            'total':    round(sum(float(l.get('importe', 0)) for l in lineas), 2),
            'n_lineas': len(lineas),
            'lineas':   lineas
        })
        os.makedirs(os.path.dirname(_REMESAS_PATH), exist_ok=True)
        with open(_REMESAS_PATH, 'w', encoding='utf-8') as f:
            json.dump(historial, f, ensure_ascii=False, indent=2)

        return jsonify({'status': 'success', 'asientos_guardados': 0})
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'status': 'error', 'message': str(e)}), 500


@financiero_bp.route('/api/historico_remesas', methods=['GET'])
def api_historico_remesas():
    try:
        with open(_REMESAS_PATH, 'r', encoding='utf-8') as f:
            historial = json.load(f)
    except Exception:
        historial = []
    return jsonify({'status': 'success', 'data': historial})


@financiero_bp.route('/api/marcar_remesa_cobrada', methods=['POST'])
def api_marcar_remesa_cobrada():
    try:
        datos   = request.get_json() or {}
        titulo  = datos.get('titulo', '').strip()
        fecha   = datos.get('fecha', '').strip()
        cobrada = bool(datos.get('cobrada', True))
        try:
            with open(_REMESAS_PATH, 'r', encoding='utf-8') as f:
                historial = json.load(f)
        except Exception:
            historial = []
        found = False
        for entrada in historial:
            if entrada.get('titulo') == titulo and entrada.get('fecha') == fecha:
                entrada['cobrada'] = cobrada
                found = True
                break
        if not found:
            return jsonify({'status': 'error', 'message': 'Remesa no encontrada'}), 404
        with open(_REMESAS_PATH, 'w', encoding='utf-8') as f:
            json.dump(historial, f, ensure_ascii=False, indent=2)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@financiero_bp.route('/api/marcar_linea_conciliada', methods=['POST'])
def api_marcar_linea_conciliada():
    """Marca una línea concreta de una remesa como conciliada (pagada parcialmente)."""
    try:
        datos     = request.get_json() or {}
        titulo    = datos.get('titulo', '').strip()
        fecha     = datos.get('fecha', '').strip()
        linea_idx = int(datos.get('linea_idx', -1))
        if not titulo or linea_idx < 0:
            return jsonify({'status': 'error', 'message': 'Parámetros inválidos'}), 400
        try:
            with open(_REMESAS_PATH, 'r', encoding='utf-8') as f:
                historial = json.load(f)
        except Exception:
            historial = []
        found = False
        for entrada in historial:
            if entrada.get('titulo') == titulo and entrada.get('fecha') == fecha:
                lineas = entrada.get('lineas', [])
                if 0 <= linea_idx < len(lineas):
                    lineas[linea_idx]['conciliada'] = True
                    found = True
                break
        if not found:
            return jsonify({'status': 'error', 'message': 'Remesa o línea no encontrada'}), 404
        with open(_REMESAS_PATH, 'w', encoding='utf-8') as f:
            json.dump(historial, f, ensure_ascii=False, indent=2)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@financiero_bp.route('/api/eliminar_remesa', methods=['POST'])
def api_eliminar_remesa():
    """Elimina una remesa del historial JSON.
    Bloquea si la remesa está cobrada globalmente o si alguna línea
    conciliada aún tiene pago real en PAGOS JUGADORES."""
    try:
        datos  = request.get_json() or {}
        titulo = datos.get('titulo', '').strip()
        fecha  = datos.get('fecha', '').strip()
        idx    = datos.get('index', -1)
        if not titulo:
            return jsonify({'status': 'error', 'message': 'Parámetros inválidos'}), 400

        try:
            with open(_REMESAS_PATH, 'r', encoding='utf-8') as f:
                historial = json.load(f)
        except Exception:
            historial = []

        # Buscar por índice exacto para no borrar remesas gemelas (mismo título y fecha)
        remesa_obj = None
        remesa_idx = None
        if isinstance(idx, int) and 0 <= idx < len(historial):
            candidate = historial[idx]
            if candidate.get('titulo') == titulo and candidate.get('fecha') == fecha:
                remesa_obj = candidate
                remesa_idx = idx
        if remesa_obj is None:
            # Fallback: primera coincidencia por titulo+fecha
            for i, e in enumerate(historial):
                if e.get('titulo') == titulo and e.get('fecha') == fecha:
                    remesa_obj = e
                    remesa_idx = i
                    break
        if remesa_obj is None:
            return jsonify({'status': 'error', 'message': 'Remesa no encontrada'}), 404

        if remesa_obj.get('cobrada'):
            return jsonify({
                'status': 'error', 'blocked': True,
                'message': 'La remesa está marcada como COBRADA. Usa REVERTIR antes de eliminar.'
            }), 409

        lineas_conciliadas = [l for l in remesa_obj.get('lineas', []) if l.get('conciliada')]
        if lineas_conciliadas:
            from app import client, NOMBRE_EXCEL
            pj_pagados = None  # None = no se pudo leer; {} = se leyó pero vacío
            try:
                wb  = client.open(NOMBRE_EXCEL)
                sh  = wb.worksheet('PAGOS JUGADORES')
                pj_pagados = {}
                for row in sh.get_all_records():
                    n = str(row.get('NOMBRE', '')).strip().lower()
                    c = str(row.get('CONCEPTO', '')).strip()
                    try:
                        p = float(str(row.get('PAGADO', '0')).replace(',', '.') or 0)
                    except Exception:
                        p = 0.0
                    if n and c and p > 0:
                        pj_pagados[(n, c)] = p
            except Exception:
                pass  # pj_pagados queda en None → bloqueamos por seguridad

            nombres_conciliados = [str(l.get('jugador', '')).strip() for l in lineas_conciliadas]

            if pj_pagados is None:
                # No se pudo leer PAGOS JUGADORES → bloquear por precaución
                return jsonify({
                    'status': 'error', 'blocked': True,
                    'message': (
                        'Hay líneas con ✔ COBRADA. Elimina primero el asiento de cobro '
                        'en Contabilidad para: ' + ', '.join(nombres_conciliados)
                    ),
                    'jugadores': nombres_conciliados
                }), 409

            bloqueados = []
            lineas_a_resetear = []
            for lin in lineas_conciliadas:
                jug       = str(lin.get('jugador', '')).strip()
                jug_lower = jug.lower()
                tiene_pago = False
                for d in lin.get('desglose', []):
                    dc   = str(d.get('concepto', '')).strip().rstrip('*').upper()
                    pj_c = MES_NUM.get(dc) or \
                           ('Inscripción' if dc == 'INSC' else
                            ('PACK_ROPA'   if dc == 'ROPA' else dc))
                    if (jug_lower, pj_c) in pj_pagados:
                        tiene_pago = True
                        if jug not in bloqueados:
                            bloqueados.append(jug)
                        break
                if not tiene_pago:
                    lineas_a_resetear.append(lin)  # flag obsoleto, se limpiará al borrar

            if bloqueados:
                return jsonify({
                    'status': 'error', 'blocked': True,
                    'message': (
                        'Elimina primero el asiento de cobro en Contabilidad para: '
                        + ', '.join(bloqueados)
                    ),
                    'jugadores': bloqueados
                }), 409

        del historial[remesa_idx]
        with open(_REMESAS_PATH, 'w', encoding='utf-8') as f:
            json.dump(historial, f, ensure_ascii=False, indent=2)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@financiero_bp.route('/api/desconciliar_asiento', methods=['POST'])
def api_desconciliar_asiento():
    """Desmarca una línea de remesa como conciliada y pone PAGADO=0 en PAGOS JUGADORES."""
    try:
        from app import client, NOMBRE_EXCEL
        datos = request.get_json() or {}
        titulo        = datos.get('titulo', '').strip()
        fecha         = datos.get('fecha', '').strip()
        jugador       = datos.get('jugador', '').strip()
        conceptos_raw = datos.get('conceptos', [])
        nueva_desc    = datos.get('nueva_descripcion') or None
        real_id       = str(datos.get('real_id', '')).strip()

        if not titulo or not jugador:
            return jsonify({'status': 'error', 'message': 'Parámetros inválidos'}), 400

        jug_lower = jugador.lower()

        # ── 1. Desmarcar línea en remesas_historial.json ────────────────────────
        try:
            with open(_REMESAS_PATH, 'r', encoding='utf-8') as f:
                historial = json.load(f)
        except Exception:
            historial = []

        remesa_encontrada = False
        for remesa in historial:
            if remesa.get('titulo', '').strip() != titulo:
                continue
            if fecha and remesa.get('fecha', '').strip() != fecha:
                continue
            for lin in remesa.get('lineas', []):
                if str(lin.get('jugador', '')).strip().lower() == jug_lower:
                    lin['conciliada'] = False
                    remesa_encontrada = True
                    break
            if remesa_encontrada:
                break

        if remesa_encontrada:
            with open(_REMESAS_PATH, 'w', encoding='utf-8') as f:
                json.dump(historial, f, ensure_ascii=False, indent=2)

        # ── 2. Poner PAGADO=0 en PAGOS JUGADORES para los conceptos de esta línea ─
        conceptos_mapeados = []
        for c in conceptos_raw:
            cu = str(c).strip().upper().rstrip('*')
            mapped = MES_NUM.get(cu) or (
                'Inscripción' if cu == 'INSC' else (
                'PACK_ROPA'   if cu == 'ROPA' else cu))
            conceptos_mapeados.append(mapped)

        if conceptos_mapeados:
            try:
                wb  = client.open(NOMBRE_EXCEL)
                sh  = wb.worksheet('PAGOS JUGADORES')
                all_vals = sh.get_all_values()
                if len(all_vals) > 1:
                    hdrs = [h.strip().upper().replace(' ', '_') for h in all_vals[0]]
                    idx_nom = next((i for i, h in enumerate(hdrs) if 'NOMBRE' in h), -1)
                    idx_con = next((i for i, h in enumerate(hdrs) if 'CONCEPTO' in h), -1)
                    idx_pag = next((i for i, h in enumerate(hdrs) if 'PAGADO' in h), -1)
                    if idx_nom >= 0 and idx_con >= 0 and idx_pag >= 0:
                        updates = []
                        for row_i, row in enumerate(all_vals[1:], start=2):
                            if len(row) <= max(idx_nom, idx_con, idx_pag):
                                continue
                            r_nom = row[idx_nom].strip().lower()
                            r_con_norm = normalizar_concepto_interno(row[idx_con].strip())
                            if r_nom == jug_lower:
                                for c_map in conceptos_mapeados:
                                    if normalizar_concepto_interno(c_map) == r_con_norm:
                                        updates.append({
                                            'range': f"'PAGOS JUGADORES'!{rowcol_to_a1(row_i, idx_pag + 1)}",
                                            'values': [[0]]
                                        })
                                        break
                        if updates:
                            sh.spreadsheet.values_batch_update({
                                'valueInputOption': 'USER_ENTERED',
                                'data': updates
                            })
            except Exception as e_pj:
                print(f"[desconciliar] Error en PAGOS JUGADORES: {e_pj}")

        # ── 3. Actualizar descripción en FINANCIERO si se solicitó ──────────────
        if nueva_desc and real_id:
            try:
                sheet_fin = client.open(NOMBRE_EXCEL).worksheet('FINANCIERO')
                all_fin   = sheet_fin.get_all_values()
                if all_fin:
                    headers_fin = normalizar_cabeceras(all_fin[0])
                    idx_asi = next(
                        (headers_fin.index(v) for v in ['Nº_ASIENTO', 'N_ASIENTO', 'ASIENTO'] if v in headers_fin),
                        -1
                    )
                    if idx_asi >= 0 and 'DESCRIPCION' in headers_fin:
                        idx_desc = headers_fin.index('DESCRIPCION')
                        for i, row in enumerate(all_fin):
                            if i == 0: continue
                            if len(row) > idx_asi:
                                val_row    = str(row[idx_asi]).strip().replace('#', '')
                                val_target = real_id.replace('#', '')
                                if val_row == val_target or (
                                    val_row.isdigit() and val_target.isdigit()
                                    and int(val_row) == int(val_target)
                                ):
                                    sheet_fin.update_cell(i + 1, idx_desc + 1, nueva_desc)
                                    break
            except Exception as e_fin:
                print(f"[desconciliar] Error actualizando FINANCIERO: {e_fin}")

        return jsonify({'status': 'success', 'remesa_encontrada': remesa_encontrada})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
