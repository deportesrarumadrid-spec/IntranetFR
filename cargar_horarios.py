"""
Script de carga única: popula HORARIOS_TEMPORADA en Google Sheets
con los datos de Lunes/Miércoles y Martes/Jueves (Temporada 2026/2027).
Ejecutar UNA SOLA VEZ desde el VPS:  python3 cargar_horarios.py
"""
import os, json
import gspread
from google.oauth2.service_account import Credentials

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECRETOS = os.path.join(BASE_DIR, 'secretos.json')
NOMBRE_EXCEL = "Control Asistencia Club"
HOJA = "HORARIOS_TEMPORADA"

COLS = ['F7-1', 'F7-2', 'F11-CAMPO 1 1', 'F-11-CAMPO 1 2', 'F11-CAMPO 2 1', 'F11-CAMPO 2 2']

# ── DATOS ──────────────────────────────────────────────────────────────────
# Formato: (horario, [F7-1, F7-2, F11-C11, F11-C12, F11-C21, F11-C22])
# Cadena vacía '' = celda vacía

LUNES_MIERCOLES = [
    ('17.00 a 17.15', ['PRE A',         'PRE B',         'PRE C',          'BEN A',          'BEN B - C',    'BEN D - E']),
    ('17.15 a 17.30', ['PRE A',         'PRE B',         'PRE C',          'BEN A',          'BEN B - C',    'BEN D - E']),
    ('17.30 a 17.45', ['PRE A',         'PRE B',         'PRE C',          'BEN A',          'BEN B - C',    'BEN D - E']),
    ('17.45 a 18.00', ['PRE A',         'PRE B',         'PRE C',          'BEN A',          'BEN B - C',    'BEN D - E']),
    ('18.00 a 18.15', ['PRE A',         'PRE B',         'PRE C',          'BEN A',          'BEN B - C',    'BEN D - E']),
    ('18.15 a 18.30', ['PRE A',         'PRE B',         'PRE C',          'BEN A',          'BEN B - C',    'BEN D - E']),
    ('18.30 a 18.45', ['TECNIFICACION', 'TECNIFICACION', 'INF C',          'INF C',          'INF D',        'INF D']),
    ('18.45 a 19.00', ['TECNIFICACION', 'TECNIFICACION', 'INF C',          'INF C',          'INF D',        'INF D']),
    ('19.00 a 19.15', ['TECNIFICACION', 'TECNIFICACION', 'INF C',          'INF C',          'INF D',        'INF D']),
    ('19.15 a 19.30', ['INF C',         'INF C',         'CDT E',          'CDT E',          'INF D',        'INF D']),
    ('19.30 a 19.45', ['INF C',         'INF C',         'CDT E',          'CDT E',          'INF D',        'INF D']),
    ('19.45 a 20.00', ['CDT B',         'CDT B',         'CDT E',          'CDT E',          '',             '']),
    ('20.00 a 20.15', ['CDT B',         'CDT B',         'CDT E',          'CDT E',          'CDT A',        'CDT A']),
    ('20.15 a 20.30', ['CDT E',         'CDT E',         'CDT B',          'CDT B',          'CDT A',        'CDT A']),
    ('20.30 a 20.45', ['JUV C',         'JUV C',         'CDT B',          'CDT B',          'CDT A',        'CDT A']),
    ('20.45 a 21.00', ['JUV C',         'JUV C',         'CDT B',          'CDT B',          'CDT A',        'CDT A']),
    ('21.00 a 21.15', ['CDT B',         'CDT B',         'JUV C',          'JUV C',          'CDT A',        'CDT A']),
    ('21.15 a 21.30', ['',              '',              'JUV C',          'JUV C',          'CDT A',        'CDT A']),
    ('21.30 a 21.45', ['LOS MARCOS',    'ALVAROS',       'JUV C',          'JUV C',          '',             '']),
    ('21.45 a 22.00', ['LOS MARCOS',    'ALVAROS',       'JUV C',          'JUV C',          '',             '']),
    ('22.00 a 22.15', ['LOS MARCOS',    'ALVAROS',       'LIGA EMPRESAS',  'LIGA EMPRESAS',  'LIGA EMPRESAS','LIGA EMPRESAS']),
    ('22.15 a 22.30', ['LOS MARCOS',    'ALVAROS',       'LIGA EMPRESAS',  'LIGA EMPRESAS',  'LIGA EMPRESAS','LIGA EMPRESAS']),
    ('22.30 a 22.45', ['LOS MARCOS',    'ALVAROS',       'LIGA EMPRESAS',  'LIGA EMPRESAS',  'LIGA EMPRESAS','LIGA EMPRESAS']),
    ('22.45 a 23.00', ['LOS MARCOS',    'ALVAROS',       'LIGA EMPRESAS',  'LIGA EMPRESAS',  'LIGA EMPRESAS','LIGA EMPRESAS']),
]

MARTES_JUEVES = [
    ('17.00 a 17.15', ['ALE FEM',            'ALE B F7',           'ALE A F7',  '',           'ALE B F11', 'ALE B F11']),
    ('17.15 a 17.30', ['ALE FEM',            'ALE B F7',           'ALE A F7',  '',           'ALE B F11', 'ALE B F11']),
    ('17.30 a 17.45', ['ALE FEM',            'ALE B F7',           'ALE A F7',  '',           'ALE B F11', 'ALE B F11']),
    ('17.45 a 18.00', ['ALE FEM',            'ALE B F7',           'ALE A F7',  '',           'ALE B F11', 'ALE B F11']),
    ('18.00 a 18.15', ['ALE FEM',            'ALE B F7',           'ALE A F7',  'ALE A F11',  'ALE B F11', 'ALE B F11']),
    ('18.15 a 18.30', ['ALE FEM',            'ALE B F7',           'ALE A F7',  'ALE A F11',  'ALE B F11', 'ALE B F11']),
    ('18.30 a 18.45', ['TECNIFICACION',      'TECNIFICACION',      'INF B',     'INF B',      'ALE A F11', 'ALE A F11']),
    ('18.45 a 19.00', ['TECNIFICACION',      'TECNIFICACION',      'INF B',     'INF B',      'ALE A F11', 'ALE A F11']),
    ('19.00 a 19.15', ['TECNIFICACION',      'TECNIFICACION',      'INF B',     'INF B',      'ALE A F11', 'ALE A F11']),
    ('19.15 a 19.30', ['INF A',              'INF A',              'INF B',     'INF B',      'ALE A F11', 'ALE A F11']),
    ('19.30 a 19.45', ['INF B',              'INF B',              'INF A',     'INF A',      'CDT D',     'CDT D']),
    ('19.45 a 20.00', ['INF B',              'INF B',              'INF A',     'INF A',      'CDT D',     'CDT D']),
    ('20.00 a 20.15', ['CDT C',              'CDT C',              'INF A',     'INF A',      'CDT D',     'CDT D']),
    ('20.15 a 20.30', ['CDT C',              'CDT C',              'INF A',     'INF A',      'CDT D',     'CDT D']),
    ('20.30 a 20.45', ['CDT D',              'CDT D',              'JUV B',     'JUV B',      'CDT C',     'CDT C']),
    ('20.45 a 21.00', ['JUV A',              'JUV A',              'JUV B',     'JUV B',      'CDT C',     'CDT C']),
    ('21.00 a 21.15', ['JUV A',              'JUV A',              'JUV B',     'JUV B',      'CDT C',     'CDT C']),
    ('21.15 a 21.30', ['LIGA EM - EMILLIO',  'LIGA EM - EMILLIO',  'JUV B',     'JUV B',      'JUV A',     'JUV A']),
    ('21.30 a 21.45', ['LIGA EM - EMILLIO',  'LIGA EM - EMILLIO',  'AFI A',     'AFI A',      'JUV A',     'JUV A']),
    ('21.45 a 22.00', ['LIGA EM - EMILLIO',  'LIGA EM - EMILLIO',  'AFI A',     'AFI A',      'JUV A',     'JUV A']),
    ('22.00 a 22.15', ['LIGA EMPRESAS',      'LIGA EMPRESAS',      'AFI A',     'AFI A',      'AFI B',     'AFI B']),
    ('22.15 a 22.30', ['LIGA EMPRESAS',      'LIGA EMPRESAS',      'AFI A',     'AFI A',      'AFI B',     'AFI B']),
    ('22.30 a 22.45', ['LIGA EMPRESAS',      'LIGA EMPRESAS',      'AFI A',     'AFI A',      'AFI B',     'AFI B']),
    ('22.45 a 23.00', ['LIGA EMPRESAS',      'LIGA EMPRESAS',      'AFI A',     'AFI A',      'AFI B',     'AFI B']),
]

GRUPOS = [
    ('lunes_miercoles', LUNES_MIERCOLES),
    ('martes_jueves',   MARTES_JUEVES),
]

# ── CONEXIÓN ────────────────────────────────────────────────────────────────
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file(SECRETOS, scopes=scope)
client = gspread.authorize(creds)
spreadsheet = client.open(NOMBRE_EXCEL)

try:
    sheet = spreadsheet.worksheet(HOJA)
    print(f"Hoja '{HOJA}' encontrada.")
except Exception:
    sheet = spreadsheet.add_worksheet(title=HOJA, rows="1000", cols="5")
    print(f"Hoja '{HOJA}' creada.")

# Limpiar datos existentes (mantener cabecera)
sheet.clear()
sheet.append_row(["GRUPO", "HORARIO", "COLUMNA", "TEXTO", "COLOR"])
print("Hoja limpiada.")

# ── INSERTAR DATOS ──────────────────────────────────────────────────────────
filas = []
for grupo_id, tabla in GRUPOS:
    for horario, celdas in tabla:
        for col_idx, texto in enumerate(celdas):
            if texto:  # solo insertar celdas con contenido
                filas.append([grupo_id, horario, COLS[col_idx], texto, ''])

sheet.append_rows(filas, value_input_option='USER_ENTERED')
print(f"Insertadas {len(filas)} celdas en '{HOJA}'.")
print("¡Listo!")
