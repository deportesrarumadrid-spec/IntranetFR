"""Agenda del fin de semana (historias de Instagram): dos imágenes, Fútbol 11 y Fútbol 7, sobre las plantillas del club.

Partidos de viernes a domingo del próximo finde, sacados del listado de la Federación (Campos y Horas), con el
local primero: "INFANTIL B - CHAMARTIN ATHLETIC" y debajo "14/11/2026 09:00". Sin Debutantes. Ordenados de mayor
a menor categoría y por letra, llenando primero la columna izquierda. Letras: League Gothic y Poppins (gratuitas, OFL).
"""
import io
import os
import re
import unicodedata
from datetime import date, timedelta
from flask import Blueprint, request, jsonify, Response

agenda_bp = Blueprint('agenda_finde', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLANTILLAS = {'F11': os.path.join(BASE_DIR, 'static', 'img', 'agenda', 'plantilla_f11.png'),
              'F7': os.path.join(BASE_DIR, 'static', 'img', 'agenda', 'plantilla_f7.png')}
FUENTE_TITULO = os.path.join(BASE_DIR, 'static', 'fonts', 'LeagueGothic.ttf')
FUENTE_FECHA = os.path.join(BASE_DIR, 'static', 'fonts', 'Poppins-Regular.ttf')
_MESES = ['ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE']
_ORDEN_CAT = ['AFICIONADO', 'JUVENIL', 'CADETE', 'INFANTIL', 'ALEVIN', 'BENJAMIN', 'PREBENJAMIN']

# Posiciones en la plantilla (1080x1920)
_COL_X = (270, 812)        # centro de cada columna
_ANCHO_COL = 500           # ancho máximo del texto de cada columna
_Y_INI, _Y_FIN = 615, 1805  # zona de los partidos (la línea del medio va de ~585 a ~1815)
_Y_FECHA_TITULO = 70       # centro vertical de "13-14-15 NOVIEMBRE"


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.replace('"', ' ').upper().split())


def _sin_comillas(s):
    return ' '.join(str(s or '').replace('"', ' ').replace("'", ' ').split()).upper()


def proximo_viernes(d):
    """Viernes del próximo fin de semana (de lunes a jueves, el de esa semana; de viernes a domingo, el siguiente)."""
    return d + timedelta(days=(4 - d.weekday()) % 7 or 7) if d.weekday() >= 4 else d + timedelta(days=4 - d.weekday())


def _orden(nombre):
    n = _norm(nombre)
    cat = next((i for i, c in enumerate(_ORDEN_CAT) if n.startswith(c)), len(_ORDEN_CAT))
    letra = next((w for w in n.split()[1:] if len(w) == 1 and w.isalpha()), 'Z')
    return (cat, 'FEMENINO' in n, letra, n)


def partidos(vie):
    """{'F11': [...], 'F7': [...]} con {'texto', 'fecha', 'hora', 'orden'} de los partidos de viernes a domingo."""
    import competicion_scraper as cs
    from horarios import _rffm_session
    from formulario_finde import _claves_federacion
    from informe_veo import _equipos_config
    equipos = [e for e in _equipos_config() if not _norm(e).startswith('DEBUTANTE')]
    cod, _, _ = cs._temporada_actual()
    lista = cs.fetch_temporada_matches(_rffm_session(), cod_temporada=cod, fecha_desde=vie.strftime('%d-%m-%Y'),
                                       fecha_hasta=(vie + timedelta(days=2)).strftime('%d-%m-%Y'))
    out = {'F11': [], 'F7': []}
    for m in lista:
        casa, fuera = m.get('nombre_casa', ''), m.get('nombre_fuera', '')
        local_nuestro = 'FUENTELARREYNA' in casa.upper()
        nuestro = casa if local_nuestro else fuera
        if 'FUENTELARREYNA' not in nuestro.upper():
            continue
        claves, fmt = _claves_federacion({'competicion': m.get('competicion'), 'deporte': m.get('deporte'), 'nuestro': nuestro})
        if not claves or any(_norm(k).startswith('DEBUTANTE') for k in claves):
            continue
        eq = next((e for e in equipos if _norm(e) in {_norm(k) for k in claves}), None)
        if eq is None and any(_norm(e).startswith('DEBUTANTE') and _norm(e) in {_norm(k) for k in claves} for e in _equipos_config()):
            continue
        nombre = ' '.join(w for w in _norm(eq or sorted(claves, key=len)[0]).split() if w not in ('F7', 'F11'))
        rival = _sin_comillas(fuera if local_nuestro else casa)
        texto = f'{nombre} - {rival}' if local_nuestro else f'{rival} - {nombre}'
        hora = (m.get('hora') or '').strip()[:5]
        out['F7' if fmt == 'F7' else 'F11'].append({'texto': texto, 'fecha': (m.get('fecha') or '').replace('-', '/'),
                                                    'hora': '' if hora in ('00:00', '0:00') else hora, 'orden': _orden(nombre)})
    for k in out:
        out[k].sort(key=lambda p: p['orden'])
    return out


def titulo_fechas(vie, lista_partidos):
    """'13-14-15 NOVIEMBRE' con los días que tienen partido (o sáb-dom si no hay)."""
    dias = sorted({p['fecha'] for p in lista_partidos})
    fechas = []
    for f in dias:
        try:
            d, m, y = [int(x) for x in f.split('/')]
            fechas.append(date(y, m, d))
        except Exception:
            pass
    fechas = fechas or [vie + timedelta(days=1), vie + timedelta(days=2)]
    meses = []
    for f in fechas:
        if _MESES[f.month - 1] not in meses:
            meses.append(_MESES[f.month - 1])
    return f"{'-'.join(f'{f.day:02d}' for f in fechas)} {'/'.join(meses)}"


def _fecha_corta(p):
    """'14/11/2026' + '16:00' → 'SAB 14/11 16:00'."""
    try:
        d, m, y = [int(x) for x in p['fecha'].split('/')]
        dia = ['LUN', 'MAR', 'MIE', 'JUE', 'VIE', 'SAB', 'DOM'][date(y, m, d).weekday()]
        return f"{dia} {d:02d}/{m:02d} {p.get('hora', '')}".strip()
    except Exception:
        return f"{p['fecha']} {p.get('hora', '')}".strip()


def _fuente(ruta, tam):
    from PIL import ImageFont
    return ImageFont.truetype(ruta, tam)


def _encajar(draw, texto, ruta, tam, ancho):
    """Fuente al tamaño mayor (hasta tam) con el que el texto cabe en el ancho."""
    while tam > 18:
        f = _fuente(ruta, tam)
        if draw.textlength(texto, font=f) <= ancho:
            return f
        tam -= 2
    return _fuente(ruta, tam)


def imagen(formato, lista, titulo):
    """PNG de la agenda (formato 'F11' o 'F7') con los partidos repartidos en dos columnas."""
    from PIL import Image, ImageDraw
    img = Image.open(PLANTILLAS[formato]).convert('RGB')
    d = ImageDraw.Draw(img)
    f_tit = _encajar(d, titulo, FUENTE_TITULO, 54, 700)
    d.text((540, _Y_FECHA_TITULO), titulo, font=f_tit, fill='white', anchor='mm')
    if not lista:
        d.text((540, 1100), 'SIN PARTIDOS', font=_fuente(FUENTE_TITULO, 70), fill='white', anchor='mm')
    else:
        n_izq = (len(lista) + 1) // 2
        filas = max(n_izq, 1)
        alto = min(160, (_Y_FIN - _Y_INI) / filas)
        tam_eq = int(min(64, alto * 0.42))
        tam_fecha = int(min(26, alto * 0.17))
        f_fecha = _fuente(FUENTE_FECHA, tam_fecha)
        # Todos los partidos con el mismo tamaño: el del texto más largo (el que más hay que reducir)
        f_eq = min((_encajar(d, p['texto'], FUENTE_TITULO, tam_eq, _ANCHO_COL) for p in lista), key=lambda f: f.size)
        for i, p in enumerate(lista):
            col, fila = (0, i) if i < n_izq else (1, i - n_izq)
            x = _COL_X[col]
            y = _Y_INI + alto * fila + alto * 0.26
            d.text((x, y), p['texto'], font=f_eq, fill='white', anchor='mm')
            d.text((x, y + f_eq.size * 0.5 + tam_fecha * 0.85), _fecha_corta(p), font=f_fecha, fill='white', anchor='mm')
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def generar(vie=None):
    """{'F11': png, 'F7': png, 'titulo': ...} del próximo finde."""
    vie = vie or proximo_viernes(date.today())
    p = partidos(vie)
    titulo = titulo_fechas(vie, p['F11'] + p['F7'])
    return {'F11': imagen('F11', p['F11'], titulo), 'F7': imagen('F7', p['F7'], titulo), 'titulo': titulo,
            'n': {k: len(v) for k, v in p.items()}}


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@agenda_bp.route('/api/whatsapp/agenda/imagen/<formato>')
def api_imagen(formato):
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    formato = 'F7' if formato.upper() == 'F7' else 'F11'
    try:
        vie = proximo_viernes(date.today())
        p = partidos(vie)
        png = imagen(formato, p[formato], titulo_fechas(vie, p['F11'] + p['F7']))
        return Response(png, mimetype='image/png', headers={'Cache-Control': 'no-store'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]}), 500


@agenda_bp.route('/api/whatsapp/agenda/prueba', methods=['POST'])
def api_prueba():
    """Envía las dos imágenes de la agenda a Rubén Pozón."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    from informe_veo import _telefono_ruben
    from objetivos_finde import enviar_imagen
    tel = ''.join(c for c in _telefono_ruben() if c.isdigit())
    if not tel:
        return jsonify({'status': 'error', 'message': 'RUBEN POZON no tiene teléfono en su usuario'})
    tel = '34' + tel if len(tel) == 9 else tel
    try:
        g = generar()
        ok1, d1 = enviar_imagen(f'{tel}@c.us', g['F11'], f"AGENDA {g['titulo']} · FÚTBOL 11")
        ok2, d2 = enviar_imagen(f'{tel}@c.us', g['F7'], f"AGENDA {g['titulo']} · FÚTBOL 7")
        if ok1 and ok2:
            return jsonify({'status': 'ok', 'message': f"Enviadas a Rubén Pozón ({g['n']['F11']} partidos F11, {g['n']['F7']} F7)"})
        return jsonify({'status': 'error', 'message': d1 or d2})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]})
