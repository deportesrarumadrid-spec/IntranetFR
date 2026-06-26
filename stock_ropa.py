import gspread
import json
import traceback
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime

stock_ropa_bp = Blueprint('stock_ropa_bp', __name__)

TALLAS_DEFECTO = ['4XS', '3XS', '2XS', 'XS', 'S', 'M', 'L', 'XL', '2XL']
CONFIG_KEY_TALLAS = 'STOCK_ROPA_TALLAS'


def _client_name():
    return current_app.gs_client, current_app.gs_name


def get_stock_sheet():
    client, name = _client_name()
    try:
        return client.open(name).worksheet("STOCK")
    except gspread.exceptions.WorksheetNotFound:
        sheet = client.open(name).add_worksheet(title="STOCK", rows="500", cols="3")
        sheet.append_row(["PRENDA", "TALLA", "CANTIDAD"])
        return sheet


def get_pedidos_sheet():
    client, name = _client_name()
    try:
        return client.open(name).worksheet("PEDIDOS_ROPA")
    except gspread.exceptions.WorksheetNotFound:
        sheet = client.open(name).add_worksheet(title="PEDIDOS_ROPA", rows="500", cols="3")
        sheet.append_row(["PEDIDO_ID", "FECHA", "ESTADO"])
        return sheet


def get_pedidos_items_sheet():
    client, name = _client_name()
    try:
        return client.open(name).worksheet("PEDIDOS_ROPA_ITEMS")
    except gspread.exceptions.WorksheetNotFound:
        sheet = client.open(name).add_worksheet(title="PEDIDOS_ROPA_ITEMS", rows="1000", cols="5")
        sheet.append_row(["PEDIDO_ID", "PRENDA", "TALLA", "CANTIDAD_PEDIDA", "CANTIDAD_RECIBIDA"])
        return sheet


def get_ropa_sheet():
    client, name = _client_name()
    return client.open(name).worksheet("ROPA")


def get_config_sheet():
    client, name = _client_name()
    spreadsheet = client.open(name)
    todas_hojas = {s.title.upper().strip(): s for s in spreadsheet.worksheets()}
    if "CONFIGURACION" in todas_hojas:
        return todas_hojas["CONFIGURACION"]
    return spreadsheet.add_worksheet(title="CONFIGURACION", rows="100", cols="2")


def get_tallas():
    sheet = get_config_sheet()
    for row in sheet.get_all_values():
        if len(row) > 0 and row[0].strip() == CONFIG_KEY_TALLAS:
            try:
                tallas = json.loads(row[1])
                if isinstance(tallas, list) and tallas:
                    return tallas
            except (json.JSONDecodeError, IndexError):
                pass
    return list(TALLAS_DEFECTO)


def guardar_tallas(tallas):
    sheet = get_config_sheet()
    all_v = sheet.get_all_values()
    valor_json = json.dumps(tallas)
    for i, row in enumerate(all_v):
        if len(row) > 0 and row[0].strip() == CONFIG_KEY_TALLAS:
            sheet.update_cell(i + 1, 2, valor_json)
            return
    sheet.append_row([CONFIG_KEY_TALLAS, valor_json])


def _parse_ropa_valor(valor):
    """Devuelve (talla, estado, fecha, pedido_id) tolerando formatos antiguos sin pedido_id."""
    partes = (valor or '').split('|')
    talla = partes[0] if len(partes) > 0 else ''
    estado = partes[1] if len(partes) > 1 else ''
    fecha = partes[2] if len(partes) > 2 else ''
    pedido_id = partes[3] if len(partes) > 3 else ''
    return talla, estado, fecha, pedido_id


def _siguiente_pedido_id(sheet_pedidos):
    valores = sheet_pedidos.get_all_values()
    max_id = 0
    for row in valores[1:]:
        if row and str(row[0]).strip().isdigit():
            max_id = max(max_id, int(row[0]))
    return max_id + 1


@stock_ropa_bp.route('/api/stock_ropa/grid', methods=['GET'])
def api_stock_ropa_grid():
    try:
        sheet_ropa = get_ropa_sheet()
        all_ropa = sheet_ropa.get_all_values()
        headers_ropa = all_ropa[0] if all_ropa else []
        prendas = headers_ropa[3:]
        tallas = get_tallas()

        reservadas = {}  # (prenda, talla) -> cantidad
        for row in all_ropa[1:]:
            for i, prenda in enumerate(prendas):
                col = i + 3
                if col >= len(row):
                    continue
                talla, estado, _, _ = _parse_ropa_valor(row[col])
                if talla and estado in ('PEDIDA', 'LLEGADO', 'ENTREGADA'):
                    key = (prenda, talla)
                    reservadas[key] = reservadas.get(key, 0) + 1

        sheet_stock = get_stock_sheet()
        stock_map = {}
        for row in sheet_stock.get_all_values()[1:]:
            if len(row) >= 3 and row[0] and row[1]:
                try:
                    stock_map[(row[0], row[1])] = int(float(row[2] or 0))
                except ValueError:
                    pass

        sheet_pedidos = get_pedidos_sheet()
        pedidos_info = {}
        for row in sheet_pedidos.get_all_values()[1:]:
            if row and row[0]:
                pedidos_info[str(row[0]).strip()] = {
                    "fecha": row[1] if len(row) > 1 else '',
                    "estado": row[2] if len(row) > 2 else 'ABIERTO'
                }

        sheet_items = get_pedidos_items_sheet()
        pendientes = {}  # (prenda, talla) -> list of {pedido_id, cantidad, fecha}
        for row in sheet_items.get_all_values()[1:]:
            if len(row) < 4:
                continue
            pedido_id, prenda, talla = row[0], row[1], row[2]
            info = pedidos_info.get(str(pedido_id).strip(), {"fecha": "", "estado": "ABIERTO"})
            if info["estado"] != 'ABIERTO':
                continue
            try:
                cantidad_pedida = int(float(row[3] or 0))
                cantidad_recibida = int(float(row[4] or 0)) if len(row) > 4 and row[4] else 0
            except ValueError:
                continue
            restante = cantidad_pedida - cantidad_recibida
            if restante <= 0:
                continue
            key = (prenda, talla)
            pendientes.setdefault(key, []).append({"pedido_id": pedido_id, "cantidad": restante, "fecha": info["fecha"]})

        grid = []
        for prenda in prendas:
            for talla in tallas:
                key = (prenda, talla)
                grid.append({
                    "prenda": prenda,
                    "talla": talla,
                    "stock": stock_map.get(key, 0),
                    "reservadas": reservadas.get(key, 0),
                    "pedidos": pendientes.get(key, [])
                })

        return jsonify({"prendas": prendas, "tallas": tallas, "grid": grid})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@stock_ropa_bp.route('/api/stock_ropa/set_stock', methods=['POST'])
def api_stock_ropa_set_stock():
    try:
        datos = request.json or {}
        prenda = (datos.get('prenda') or '').strip()
        talla = (datos.get('talla') or '').strip()
        cantidad = int(datos.get('cantidad') or 0)
        if not prenda or not talla:
            return jsonify({"status": "error", "message": "Faltan datos."}), 400

        sheet_stock = get_stock_sheet()
        all_v = sheet_stock.get_all_values()
        for i, row in enumerate(all_v):
            if i == 0:
                continue
            if len(row) >= 2 and row[0] == prenda and row[1] == talla:
                sheet_stock.update_cell(i + 1, 3, cantidad)
                return jsonify({"status": "success"})
        sheet_stock.append_row([prenda, talla, cantidad])
        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@stock_ropa_bp.route('/api/stock_ropa/talla/nueva', methods=['POST'])
def api_stock_ropa_talla_nueva():
    try:
        datos = request.json or {}
        nombre = (datos.get('nombre') or '').strip().upper()
        if not nombre:
            return jsonify({"status": "error", "message": "Nombre de talla vacío."}), 400
        tallas = get_tallas()
        if nombre not in tallas:
            tallas.append(nombre)
            guardar_tallas(tallas)
        return jsonify({"status": "success", "tallas": tallas})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@stock_ropa_bp.route('/api/stock_ropa/talla/renombrar', methods=['POST'])
def api_stock_ropa_talla_renombrar():
    try:
        datos = request.json or {}
        antigua = (datos.get('antigua') or '').strip()
        nueva = (datos.get('nueva') or '').strip().upper()
        if not antigua or not nueva:
            return jsonify({"status": "error", "message": "Faltan datos."}), 400

        tallas = get_tallas()
        tallas = [nueva if t == antigua else t for t in tallas]
        guardar_tallas(tallas)

        sheet_stock = get_stock_sheet()
        all_v = sheet_stock.get_all_values()
        for i, row in enumerate(all_v):
            if i == 0:
                continue
            if len(row) >= 2 and row[1] == antigua:
                sheet_stock.update_cell(i + 1, 2, nueva)

        return jsonify({"status": "success", "tallas": tallas})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@stock_ropa_bp.route('/api/stock_ropa/siguiente_pedido_id', methods=['GET'])
def api_stock_ropa_siguiente_pedido_id():
    try:
        sheet_pedidos = get_pedidos_sheet()
        return jsonify({"pedido_id": _siguiente_pedido_id(sheet_pedidos), "fecha": datetime.now().strftime("%d/%m/%Y")})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@stock_ropa_bp.route('/api/stock_ropa/propuesta', methods=['GET'])
def api_stock_ropa_propuesta():
    try:
        sheet_ropa = get_ropa_sheet()
        all_ropa = sheet_ropa.get_all_values()
        headers_ropa = all_ropa[0] if all_ropa else []
        prendas = headers_ropa[3:]
        tallas = get_tallas()

        grupos = {}  # (prenda, talla) -> list jugadores
        for row in all_ropa[1:]:
            equipo = row[1] if len(row) > 1 else ''
            jugador = row[2] if len(row) > 2 else ''
            if not jugador:
                continue
            for i, prenda in enumerate(prendas):
                col = i + 3
                if col >= len(row):
                    continue
                talla, estado, _, pedido_id = _parse_ropa_valor(row[col])
                # "Sin color" = cualquier estado que no sea PEDIDA/LLEGADO/ENTREGADA
                # (incluye vacío, 'NADA' o el antiguo valor heredado 'SOLICITADO').
                if talla and estado not in ('PEDIDA', 'LLEGADO', 'ENTREGADA') and not pedido_id:
                    key = (prenda, talla)
                    grupos.setdefault(key, []).append({"jugador": jugador, "equipo": equipo})

        propuesta = []
        for (prenda, talla), jugadores in grupos.items():
            propuesta.append({
                "prenda": prenda,
                "talla": talla,
                "cantidad": len(jugadores),
                "jugadores": jugadores
            })

        propuesta.sort(key=lambda x: (x['prenda'], tallas.index(x['talla']) if x['talla'] in tallas else 99))
        return jsonify({"propuesta": propuesta})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@stock_ropa_bp.route('/api/stock_ropa/pedido/crear', methods=['POST'])
def api_stock_ropa_pedido_crear():
    try:
        datos = request.json or {}
        items = datos.get('items', [])
        if not items:
            return jsonify({"status": "error", "message": "No hay items en el pedido."}), 400

        sheet_pedidos = get_pedidos_sheet()
        sheet_items = get_pedidos_items_sheet()
        sheet_ropa = get_ropa_sheet()
        all_ropa = sheet_ropa.get_all_values()
        headers_ropa = all_ropa[0] if all_ropa else []
        prendas = headers_ropa[3:]

        pedido_id = _siguiente_pedido_id(sheet_pedidos)
        fecha_hoy = datetime.now().strftime("%d/%m/%Y")
        sheet_pedidos.append_row([pedido_id, fecha_hoy, "ABIERTO"])

        filas_items = []
        updates_ropa = []
        for item in items:
            prenda = item.get('prenda')
            talla = item.get('talla')
            cantidad = int(item.get('cantidad') or 0)
            jugadores = item.get('jugadores') or []
            if not prenda or not talla or cantidad <= 0:
                continue
            filas_items.append([pedido_id, prenda, talla, cantidad, 0])

            if prenda not in prendas:
                continue
            col_idx = prendas.index(prenda) + 4  # 1-based, col D=4 es la primera prenda

            for jug in jugadores[:cantidad]:
                nombre_j = jug.get('jugador')
                equipo_j = jug.get('equipo')
                for i, row in enumerate(all_ropa):
                    if i == 0:
                        continue
                    if len(row) > 2 and row[2].strip() == (nombre_j or '').strip() and row[1].strip() == (equipo_j or '').strip():
                        valor = f"{talla}|PEDIDA|{fecha_hoy}|{pedido_id}"
                        updates_ropa.append({'fila': i + 1, 'col': col_idx, 'valor': valor})
                        break

        if filas_items:
            sheet_items.append_rows(filas_items)

        for u in updates_ropa:
            sheet_ropa.update_cell(u['fila'], u['col'], u['valor'])

        return jsonify({"status": "success", "pedido_id": pedido_id, "fecha": fecha_hoy})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


def _buscar_fila_ropa(all_ropa, jugador, equipo):
    for i, row in enumerate(all_ropa):
        if i == 0:
            continue
        if len(row) > 2 and row[2].strip() == (jugador or '').strip() and row[1].strip() == (equipo or '').strip():
            return i + 1
    return None


@stock_ropa_bp.route('/api/stock_ropa/pedido/<pedido_id>/reservados', methods=['GET'])
def api_stock_ropa_pedido_reservados(pedido_id):
    try:
        prenda = request.args.get('prenda', '')
        talla = request.args.get('talla', '')
        sheet_ropa = get_ropa_sheet()
        all_ropa = sheet_ropa.get_all_values()
        headers_ropa = all_ropa[0] if all_ropa else []
        prendas = headers_ropa[3:]
        if prenda not in prendas:
            return jsonify({"reservados": []})
        col = prendas.index(prenda) + 3

        incluir_todos = request.args.get('incluir_llegados') == '1'
        estados_validos = ('PEDIDA', 'LLEGADO', 'ENTREGADA') if incluir_todos else ('PEDIDA',)

        reservados = []
        for i, row in enumerate(all_ropa):
            if i == 0 or len(row) <= col:
                continue
            t, estado, _, pid = _parse_ropa_valor(row[col])
            if t == talla and estado in estados_validos and str(pid).strip() == str(pedido_id).strip():
                reservados.append({"jugador": row[2], "equipo": row[1], "estado": estado})
        return jsonify({"reservados": reservados})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


CONFIG_KEY_PRECIOS_PRENDAS = 'PRECIOS_PRENDAS'
CONFIG_KEY_PACK_ROPA = 'PACK_ROPA_CONFIG'
CONFIG_KEY_JUGADORES_PACK = 'JUGADORES_PACK_ROPA'


@stock_ropa_bp.route('/api/stock_ropa/jugadores_pack', methods=['GET', 'POST'])
def api_stock_ropa_jugadores_pack():
    try:
        sheet = get_config_sheet()
        all_v = sheet.get_all_values()

        if request.method == 'GET':
            for row in all_v:
                if len(row) > 0 and row[0].strip() == CONFIG_KEY_JUGADORES_PACK:
                    try:
                        return jsonify(json.loads(row[1]))
                    except (json.JSONDecodeError, IndexError):
                        return jsonify({})
            return jsonify({})

        datos = request.json or {}
        valor_json = json.dumps(datos)
        for i, row in enumerate(all_v):
            if len(row) > 0 and row[0].strip() == CONFIG_KEY_JUGADORES_PACK:
                sheet.update_cell(i + 1, 2, valor_json)
                return jsonify({"status": "success"})
        sheet.append_row([CONFIG_KEY_JUGADORES_PACK, valor_json])
        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@stock_ropa_bp.route('/api/stock_ropa/pack_ropa_config', methods=['GET', 'POST'])
def api_stock_ropa_pack_ropa_config():
    try:
        sheet = get_config_sheet()
        all_v = sheet.get_all_values()
        default = {"pequeno": {"prendas": [], "precio": 0}, "grande": {"prendas": [], "precio": 0}}

        if request.method == 'GET':
            for row in all_v:
                if len(row) > 0 and row[0].strip() == CONFIG_KEY_PACK_ROPA:
                    try:
                        return jsonify(json.loads(row[1]))
                    except (json.JSONDecodeError, IndexError):
                        return jsonify(default)
            return jsonify(default)

        datos = request.json or default
        valor_json = json.dumps(datos)
        for i, row in enumerate(all_v):
            if len(row) > 0 and row[0].strip() == CONFIG_KEY_PACK_ROPA:
                sheet.update_cell(i + 1, 2, valor_json)
                return jsonify({"status": "success"})
        sheet.append_row([CONFIG_KEY_PACK_ROPA, valor_json])
        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@stock_ropa_bp.route('/api/stock_ropa/precios_prendas', methods=['GET', 'POST'])
def api_stock_ropa_precios_prendas():
    try:
        sheet = get_config_sheet()
        all_v = sheet.get_all_values()

        if request.method == 'GET':
            for row in all_v:
                if len(row) > 0 and row[0].strip() == CONFIG_KEY_PRECIOS_PRENDAS:
                    try:
                        return jsonify({"precios": json.loads(row[1])})
                    except (json.JSONDecodeError, IndexError):
                        return jsonify({"precios": {}})
            return jsonify({"precios": {}})

        datos = request.json or {}
        precios = datos.get('precios', {})
        valor_json = json.dumps(precios)
        for i, row in enumerate(all_v):
            if len(row) > 0 and row[0].strip() == CONFIG_KEY_PRECIOS_PRENDAS:
                sheet.update_cell(i + 1, 2, valor_json)
                return jsonify({"status": "success"})
        sheet.append_row([CONFIG_KEY_PRECIOS_PRENDAS, valor_json])
        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@stock_ropa_bp.route('/api/stock_ropa/borrar_celda', methods=['POST'])
def api_stock_ropa_borrar_celda():
    try:
        datos = request.json or {}
        jugador = datos.get('jugador')
        equipo = datos.get('equipo')
        prenda = datos.get('prenda')

        sheet_ropa = get_ropa_sheet()
        all_ropa = sheet_ropa.get_all_values()
        headers_ropa = all_ropa[0] if all_ropa else []
        prendas = headers_ropa[3:]
        if prenda not in prendas:
            return jsonify({"status": "error", "message": "Prenda no encontrada."}), 400
        col_idx = prendas.index(prenda) + 4

        fila = _buscar_fila_ropa(all_ropa, jugador, equipo)
        if not fila:
            return jsonify({"status": "error", "message": "Jugador no encontrado."}), 400

        valor_actual = all_ropa[fila - 1][col_idx - 1] if len(all_ropa[fila - 1]) >= col_idx else ''
        talla, estado, _, _ = _parse_ropa_valor(valor_actual)

        sheet_ropa.update_cell(fila, col_idx, '')

        # Si todavía no se había entregado físicamente al jugador, la unidad vuelve al stock libre
        if talla and estado in ('PEDIDA', 'LLEGADO'):
            sheet_stock = get_stock_sheet()
            all_stock = sheet_stock.get_all_values()
            encontrado = False
            for i, srow in enumerate(all_stock):
                if i == 0:
                    continue
                if len(srow) >= 2 and srow[0] == prenda and srow[1] == talla:
                    actual = int(float(srow[2])) if len(srow) > 2 and srow[2] else 0
                    sheet_stock.update_cell(i + 1, 3, actual + 1)
                    encontrado = True
                    break
            if not encontrado:
                sheet_stock.append_row([prenda, talla, 1])

        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@stock_ropa_bp.route('/api/stock_ropa/reasignar', methods=['POST'])
def api_stock_ropa_reasignar():
    try:
        datos = request.json or {}
        pedido_id = datos.get('pedido_id')
        prenda = datos.get('prenda')
        talla = datos.get('talla')
        origen = datos.get('origen') or {}
        destino = datos.get('destino') or {}

        sheet_ropa = get_ropa_sheet()
        all_ropa = sheet_ropa.get_all_values()
        headers_ropa = all_ropa[0] if all_ropa else []
        prendas = headers_ropa[3:]
        if prenda not in prendas:
            return jsonify({"status": "error", "message": "Prenda no encontrada."}), 400
        col_idx = prendas.index(prenda) + 4

        fila_origen = _buscar_fila_ropa(all_ropa, origen.get('jugador'), origen.get('equipo'))
        if fila_origen:
            sheet_ropa.update_cell(fila_origen, col_idx, '')

        fecha_hoy = datetime.now().strftime("%d/%m/%Y")
        valor_nuevo = f"{talla}|PEDIDA|{fecha_hoy}|{pedido_id}"
        fila_destino = _buscar_fila_ropa(all_ropa, destino.get('jugador'), destino.get('equipo'))
        if fila_destino:
            sheet_ropa.update_cell(fila_destino, col_idx, valor_nuevo)
        else:
            nueva_fila = [''] * len(headers_ropa)
            nueva_fila[0] = fecha_hoy
            nueva_fila[1] = destino.get('equipo', '')
            nueva_fila[2] = destino.get('jugador', '')
            nueva_fila[col_idx - 1] = valor_nuevo
            sheet_ropa.append_row(nueva_fila)

        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@stock_ropa_bp.route('/api/stock_ropa/pedidos', methods=['GET'])
def api_stock_ropa_pedidos():
    try:
        sheet_pedidos = get_pedidos_sheet()
        sheet_items = get_pedidos_items_sheet()

        items_por_pedido = {}
        for row in sheet_items.get_all_values()[1:]:
            if len(row) < 4 or not row[0]:
                continue
            pid = str(row[0]).strip()
            try:
                cantidad_pedida = int(float(row[3] or 0))
                cantidad_recibida = int(float(row[4] or 0)) if len(row) > 4 and row[4] else 0
            except ValueError:
                continue
            items_por_pedido.setdefault(pid, []).append({
                "prenda": row[1], "talla": row[2],
                "cantidad_pedida": cantidad_pedida, "cantidad_recibida": cantidad_recibida
            })

        pedidos = []
        filas_a_completar = []
        for i, row in enumerate(sheet_pedidos.get_all_values()[1:]):
            if not row or not row[0]:
                continue
            pid = str(row[0]).strip()
            items = items_por_pedido.get(pid, [])
            completo = len(items) > 0 and all(it['cantidad_recibida'] >= it['cantidad_pedida'] for it in items)
            estado_actual = row[2] if len(row) > 2 else 'ABIERTO'
            estado_final = 'COMPLETO' if completo else estado_actual
            if completo and estado_actual != 'COMPLETO':
                filas_a_completar.append(i + 2)
            pedidos.append({
                "pedido_id": pid,
                "fecha": row[1] if len(row) > 1 else '',
                "estado": estado_final,
                "items": items
            })

        for fila in filas_a_completar:
            sheet_pedidos.update_cell(fila, 3, 'COMPLETO')

        pedidos.sort(key=lambda p: int(p['pedido_id']) if p['pedido_id'].isdigit() else 0, reverse=True)
        return jsonify({"pedidos": pedidos})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@stock_ropa_bp.route('/api/stock_ropa/pedido/<pedido_id>/recuento', methods=['POST'])
def api_stock_ropa_pedido_recuento(pedido_id):
    try:
        datos = request.json or {}
        items_recuento = datos.get('items', [])  # [{prenda, talla, cantidad_recibida}]

        sheet_items = get_pedidos_items_sheet()
        sheet_pedidos = get_pedidos_sheet()
        sheet_ropa = get_ropa_sheet()
        all_ropa = sheet_ropa.get_all_values()
        headers_ropa = all_ropa[0] if all_ropa else []
        prendas = headers_ropa[3:]

        sheet_stock = get_stock_sheet()
        all_stock = sheet_stock.get_all_values()

        resumen = []
        all_items_rows = sheet_items.get_all_values()
        fecha_hoy = datetime.now().strftime("%d/%m/%Y")

        for item in items_recuento:
            prenda = item.get('prenda')
            talla = item.get('talla')
            jugadores_llegados = item.get('jugadores_llegados') or []
            jugadores_a_stock = item.get('jugadores_a_stock') or []
            try:
                cantidad_recibida = int(item.get('cantidad_recibida') or 0)
            except (TypeError, ValueError):
                continue

            # Actualizar fila de PEDIDOS_ROPA_ITEMS
            for i, row in enumerate(all_items_rows):
                if i == 0:
                    continue
                if len(row) >= 3 and str(row[0]).strip() == str(pedido_id).strip() and row[1] == prenda and row[2] == talla:
                    sheet_items.update_cell(i + 1, 5, cantidad_recibida)
                    break

            if prenda not in prendas:
                continue
            col_idx = prendas.index(prenda) + 4

            # Marcar LLEGADO a los jugadores confirmados (en verde) por el usuario
            asignados = []
            for jug in jugadores_llegados[:max(cantidad_recibida, 0)]:
                fila = _buscar_fila_ropa(all_ropa, jug.get('jugador'), jug.get('equipo'))
                if fila:
                    valor = f"{talla}|LLEGADO|{fecha_hoy}|{pedido_id}"
                    sheet_ropa.update_cell(fila, col_idx, valor)
                    asignados.append(jug.get('jugador'))

            # Los marcados explícitamente "NO ASIGNAR" liberan su celda y la unidad va a stock
            restante = 0
            for jug in jugadores_a_stock:
                fila = _buscar_fila_ropa(all_ropa, jug.get('jugador'), jug.get('equipo'))
                if fila:
                    sheet_ropa.update_cell(fila, col_idx, '')
                restante += 1

            # Unidades recibidas de más sin jugador asignado también van a stock libre
            restante += max(0, cantidad_recibida - len(asignados))

            if restante > 0:
                encontrado = False
                for i, srow in enumerate(all_stock):
                    if i == 0:
                        continue
                    if len(srow) >= 2 and srow[0] == prenda and srow[1] == talla:
                        actual = 0
                        try:
                            actual = int(float(srow[2])) if len(srow) > 2 and srow[2] else 0
                        except ValueError:
                            pass
                        sheet_stock.update_cell(i + 1, 3, actual + restante)
                        encontrado = True
                        break
                if not encontrado:
                    sheet_stock.append_row([prenda, talla, restante])

            resumen.append({"prenda": prenda, "talla": talla, "asignados": asignados, "sobran_a_stock": restante})

        # Comprobar si el pedido queda completo
        items_pedido = [{"cantidad_pedida": int(float(r[3] or 0)), "cantidad_recibida": int(float(r[4] or 0)) if len(r) > 4 and r[4] else 0}
                         for r in sheet_items.get_all_values()[1:] if r and str(r[0]).strip() == str(pedido_id).strip()]
        completo = len(items_pedido) > 0 and all(it['cantidad_recibida'] >= it['cantidad_pedida'] for it in items_pedido)
        if completo:
            for i, row in enumerate(sheet_pedidos.get_all_values()):
                if i == 0:
                    continue
                if row and str(row[0]).strip() == str(pedido_id).strip():
                    sheet_pedidos.update_cell(i + 1, 3, 'COMPLETO')
                    break

        return jsonify({"status": "success", "resumen": resumen, "completo": completo})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500
