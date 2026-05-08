from flask import Blueprint, render_template, request, session, jsonify

# Creamos el Blueprint para la sección financiera
financiero_bp = Blueprint('financiero', __name__)

def leer_hoja_limpia(client, nombre_excel, nombre_hoja):
    """Función de utilidad para leer datos de Excel de forma estructurada."""
    try:
        sheet = client.open(nombre_excel).worksheet(nombre_hoja)
        all_v = sheet.get_all_values()
        if not all_v: return []
        # Limpiamos cabeceras: quitamos espacios, a mayúsculas y quitamos tildes comunes
        headers = [h.strip().upper().replace('Ó','O').replace('Í','I').replace('É','E').replace('Á','A').replace('Ú','U') for h in all_v[0]]
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
            datos = leer_hoja_limpia(client, NOMBRE_EXCEL, "FINANCIERO")
            return jsonify(datos)
        
        # POST: Guardar Asiento Manual
        sheet = client.open(NOMBRE_EXCEL).worksheet("FINANCIERO")
        datos = request.json
        
        # El Nº Asiento es el total de filas actuales (incluyendo cabecera)
        num_asiento = len(sheet.get_all_values())
        
        nueva_fila = [
            datos.get('fecha'),
            num_asiento,
            datos.get('departamento'),
            datos.get('pilar'),
            datos.get('descripcion'),
            datos.get('importe')
        ]
        sheet.append_row(nueva_fila)
        return jsonify({"status": "success", "asiento": num_asiento})
    except Exception as e:
        print(f"Error en api_presupuesto: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500