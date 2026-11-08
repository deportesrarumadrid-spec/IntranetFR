"""Objetivos del fin de semana al grupo de WhatsApp "FR Entrenadores" (viernes 17:00).

Imagen de la tabla PLANIFICACIÓN — OBJETIVOS de OBJ FINES DE SEMANA (viernes, sábado y domingo; todos
los equipos, también los que descansan) con el texto "OBJETIVOS 13-14-15 NOVIEMBRE" debajo (pie de foto).
La imagen se dibuja en el servidor con PyMuPDF (nadie tiene la intranet abierta a esa hora).
Si ese finde no juega nadie, no se envía. Estado en datos_privados/objetivos_finde.json.
"""
import io
import os
import json
import base64
from html import escape
from datetime import datetime, date, timedelta
from flask import Blueprint, request, jsonify, Response

objetivos_bp = Blueprint('objetivos_finde', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(BASE_DIR, 'datos_privados', 'objetivos_finde.json')
HORA_ENVIO = '17:00'
_MESES = ['ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 'JULIO', 'AGOSTO',
          'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE']
_DIAS = ['LUN', 'MAR', 'MIÉ', 'JUE', 'VIE', 'SÁB', 'DOM']


def cargar_cfg():
    try:
        with open(CFG_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    cfg.setdefault('activo', True)
    return cfg


def guardar_cfg(cfg):
    os.makedirs(os.path.dirname(CFG_FILE), exist_ok=True)
    tmp = CFG_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CFG_FILE)


def viernes_de(d):
    """Viernes del finde de referencia: de viernes a domingo el de ese finde; de lunes a jueves el siguiente."""
    return d - timedelta(days=(d.weekday() - 4) % 7) if d.weekday() >= 4 else d + timedelta(days=4 - d.weekday())


def texto_finde(vie):
    dias = [vie + timedelta(days=i) for i in range(3)]
    meses = []
    for d in dias:
        if _MESES[d.month - 1] not in meses:
            meses.append(_MESES[d.month - 1])
    return f"OBJETIVOS {'-'.join(f'{d:%d}' for d in dias)} {'/'.join(meses)}"


def partidos_finde(vie):
    """Partidos de viernes, sábado y domingo, igual que la tabla de la pantalla (mismo orden y sin repetidos)."""
    from competicion_scraper import construir_obj_semanales
    dias = {vie + timedelta(days=i) for i in range(3)}
    vistos, out = set(), []
    for p in construir_obj_semanales():
        try:
            dd, mm, yy = [int(x) for x in (p.get('fecha') or '').split('-')]
            if date(yy, mm, dd) not in dias:
                continue
        except Exception:
            continue
        clave = (p.get('categoria'), p.get('letra'), p.get('formato') or '', p.get('rival'), p.get('jornada'))
        if clave in vistos:
            continue
        vistos.add(clave)
        out.append(p)
    return out


def _jornada(p):
    num = ''.join(c for c in str(p.get('jornada') or '') if c.isdigit())
    try:
        dd, mm, yy = [int(x) for x in p['fecha'].split('-')]
        return f"J.{num} {_DIAS[date(yy, mm, dd).weekday()]} {dd:02d}/{mm:02d}"
    except Exception:
        return f'J.{num}'


def html_tabla(partidos):
    """Misma tabla que PLANIFICACIÓN — OBJETIVOS (columnas Equipo, Jorn., Campo, Rival, Objetivo, Datos rival)."""
    th = 'padding:6px 8px;font-size:9.5px;font-weight:bold;color:#64748b;background-color:#f8fafc;border-bottom:2px solid #e2e8f0;text-align:left;'
    td = 'padding:7px 8px;font-size:10.5px;border-bottom:1px solid #e2e8f0;vertical-align:top;'
    cnt = {}
    for p in partidos:
        k = (p.get('categoria'), p.get('letra'), p.get('formato') or '')
        cnt[k] = cnt.get(k, 0) + 1
    filas = []
    for p in partidos:
        k = (p.get('categoria'), p.get('letra'), p.get('formato') or '')
        equipo = escape(f"{p.get('categoria', '')} {p.get('letra', '')}".strip())
        if p.get('formato'):
            equipo += f' <span style="color:#94a3b8;font-size:8px;">({escape(p["formato"])})</span>'
        if cnt[k] > 1 and p.get('grupo_short'):
            equipo += f' <span style="color:#94a3b8;font-size:8px;">[{escape(p["grupo_short"])}]</span>'
        objetivo = (f'<b>{escape(p["objetivo"])}</b>' if p.get('objetivo') else '<span style="color:#94a3b8;">–</span>')
        datos = '<span style="color:#94a3b8;">–</span>'
        dn, dr = p.get('datos_nuestro'), p.get('datos_rival')
        if dn and dr:
            c = 'padding:1px 5px;font-size:9.5px;'
            fila = lambda et, x: (f'<tr><td style="{c}color:#94a3b8;font-weight:bold;width:30px;">{et}</td>'
                                   f'<td style="{c}font-weight:bold;width:28px;">{escape(str(x.get("posicion", "")))}°</td>'
                                   f'<td style="{c}font-weight:bold;color:#E31E24;width:40px;">{escape(str(x.get("puntos", "")))}pts</td>'
                                   f'<td style="{c}color:#64748b;">Goles Favor: {escape(str(x.get("goles_favor", "")))}, '
                                   f'Goles Contra: {escape(str(x.get("goles_contra", "")))}</td></tr>')
            ult = ' '.join(f'({escape(str(u.get("estado", "")))} {escape(str(u.get("resultado", "")))} vs {escape(str(u.get("rival", "")))})'
                           for u in (p.get('ultimos_rival') or []))
            datos = (f'<table style="border-collapse:collapse;width:100%;">{fila("FR", dn)}{fila("Rival", dr)}</table>'
                     + (f'<div style="font-size:8px;color:#334155;margin-top:2px;">{ult}</div>' if ult else ''))
        filas.append(
            f'<tr><td style="{td}font-weight:bold;">{equipo}</td>'
            f'<td style="{td}color:#64748b;font-weight:bold;">{escape(_jornada(p))}</td>'
            f'<td style="{td}color:#64748b;">{"Casa" if p.get("es_local") else "Fuera"}</td>'
            f'<td style="{td}">{escape(str(p.get("rival", "")))}</td>'
            f'<td style="{td}">{objetivo}</td><td style="{td}">{datos}</td></tr>')
    return (f'<table style="width:100%;border-collapse:collapse;font-family:sans-serif;">'
            f'<tr><th style="{th}width:120px;">EQUIPO</th><th style="{th}width:85px;">JORN.</th>'
            f'<th style="{th}width:45px;">CAMPO</th><th style="{th}width:150px;">RIVAL</th>'
            f'<th style="{th}width:105px;">OBJETIVO</th><th style="{th}">DATOS RIVAL</th></tr>'
            + ''.join(filas) + '</table>')


def imagen_png(partidos, ancho=1000):
    """Dibuja la tabla y devuelve el PNG (bytes), recortado a la altura del contenido."""
    import fitz
    html = html_tabla(partidos)
    margen = 14
    alto_max = 60 + 95 * max(1, len(partidos))

    def _maquetar(alto):
        story = fitz.Story(html=html)
        buf = io.BytesIO()
        writer = fitz.DocumentWriter(buf)
        mediabox = fitz.Rect(0, 0, ancho, alto)
        dev = writer.begin_page(mediabox)
        mas, llenado = story.place(mediabox + (margen, margen, -margen, -margen))
        story.draw(dev)
        writer.end_page()
        writer.close()
        return buf.getvalue(), llenado

    _, llenado = _maquetar(alto_max)
    pdf, _ = _maquetar(int(fitz.Rect(llenado).y1) + margen)
    doc = fitz.open('pdf', pdf)
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    return pix.tobytes('png')


def _grupo_fr():
    """Id del grupo de WhatsApp 'FR Entrenadores' (se busca por nombre y se guarda)."""
    from whatsapp_envios import _waha, WAHA_SESION
    cfg = cargar_cfg()
    if cfg.get('grupo_id'):
        return cfg['grupo_id']
    r = _waha('GET', f'/api/{WAHA_SESION}/groups', timeout=40)
    datos = r.json() if r.status_code < 300 else []
    for g in (datos.values() if isinstance(datos, dict) else datos or []):
        if not isinstance(g, dict):
            continue
        gid = g.get('id') or g.get('JID') or g.get('jid') or ''
        if isinstance(gid, dict):
            gid = gid.get('_serialized') or ''
        nombre = (g.get('subject') or g.get('name') or g.get('Name') or '').lower()
        if 'fr entrenadores' in nombre and str(gid).endswith('@g.us'):
            cfg['grupo_id'] = gid
            guardar_cfg(cfg)
            return gid
    return None


def enviar_imagen(chat_id, png, pie):
    """Envía la imagen con pie de foto. Devuelve (ok, detalle)."""
    from whatsapp_envios import _waha, WAHA_SESION
    if not os.environ.get('WAHA_URL'):
        return False, 'El WhatsApp del servidor (WAHA) no está configurado'
    try:
        r = _waha('POST', '/api/sendImage', timeout=60, json={
            'session': WAHA_SESION, 'chatId': chat_id, 'caption': pie,
            'file': {'mimetype': 'image/png', 'filename': 'objetivos.png', 'data': base64.b64encode(png).decode()}})
        if r.status_code < 300:
            return True, ''
        try:
            detalle = r.json().get('message') or r.text
        except Exception:
            detalle = r.text
        return False, f'WhatsApp no ha aceptado la imagen (HTTP {r.status_code}): {str(detalle)[:200]}'
    except Exception as e:
        return False, f'No se pudo conectar con WhatsApp del servidor: {e}'[:200]


def enviar(vie, chat_id=None):
    """Genera y envía los objetivos del finde del viernes `vie` (al grupo FR Entrenadores si no se indica otro)."""
    partidos = partidos_finde(vie)
    if not partidos:
        return False, 'Ese fin de semana no hay partidos: no se envía nada'
    destino = chat_id or _grupo_fr()
    if not destino:
        return False, 'No encuentro el grupo "FR Entrenadores" en el WhatsApp del club'
    return enviar_imagen(destino, imagen_png(partidos), texto_finde(vie))


def tick(ahora):
    """Lo llama push_scheduler cada minuto: los viernes, una vez, en la media hora siguiente a las 17:00."""
    if ahora.weekday() != 4:
        return
    cfg = cargar_cfg()
    if not cfg.get('activo'):
        return
    hh, mm = [int(x) for x in HORA_ENVIO.split(':')]
    inicio = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
    hoy = ahora.date().isoformat()
    if cfg.get('ultimo') == hoy or not (inicio <= ahora < inicio + timedelta(minutes=30)):
        return
    cfg['ultimo'] = hoy  # marcar antes de enviar: nunca dos veces el mismo viernes
    guardar_cfg(cfg)
    ok, detalle = enviar(ahora.date())
    print(f"[OBJETIVOS] Viernes {hoy}: {'enviado al grupo' if ok else detalle}", flush=True)


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@objetivos_bp.route('/api/whatsapp/objetivos', methods=['GET', 'POST'])
def api_cfg():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    cfg = cargar_cfg()
    if request.method == 'POST':
        cfg['activo'] = bool((request.json or {}).get('activo'))
        guardar_cfg(cfg)
    vie = viernes_de(date.today())
    return jsonify({'status': 'ok', 'activo': cfg['activo'], 'ultimo': cfg.get('ultimo', ''),
                    'viernes': vie.isoformat(), 'texto': texto_finde(vie)})


@objetivos_bp.route('/api/whatsapp/objetivos/imagen')
def api_imagen():
    """Vista previa de la imagen que se enviará (finde de referencia)."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    partidos = partidos_finde(viernes_de(date.today()))
    if not partidos:
        return jsonify({'status': 'error', 'message': 'Ese fin de semana no hay partidos'}), 404
    return Response(imagen_png(partidos), mimetype='image/png', headers={'Cache-Control': 'no-store'})


@objetivos_bp.route('/api/whatsapp/objetivos/enviar', methods=['POST'])
def api_enviar():
    """destino='prueba' (a un teléfono, para revisarlo) o 'grupo' (FR Entrenadores, ahora mismo)."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    data = request.json or {}
    vie = viernes_de(date.today())
    if data.get('destino') == 'prueba':
        num = ''.join(c for c in str(data.get('telefono') or '') if c.isdigit())
        if len(num) == 9:
            num = '34' + num
        if len(num) < 10:
            return jsonify({'status': 'error', 'message': 'Teléfono no válido'}), 400
        ok, detalle = enviar(vie, f'{num}@c.us')
    else:
        ok, detalle = enviar(vie)
    return jsonify({'status': 'ok' if ok else 'error', 'message': detalle})
