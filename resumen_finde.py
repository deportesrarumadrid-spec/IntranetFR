"""Resumen del finde al grupo de WhatsApp "FR Entrenadores" (lunes 20:00).

Imagen 1: RESULTADOS — CUMPLIMIENTO de OBJ FINES DE SEMANA (resultado, objetivo y si se cumplió), con el texto
"RESULTADOS dd-dd-dd MES / Objetivos cumplidos… / Formularios… · Audios…" debajo.
Imagen 2: INFORME FINDE WHATSAPP (formulario y audio de cada equipo, con el TOTAL).
A las 11:00 se actualizan los datos de la Federación para que estén los resultados del domingo.
Estado en datos_privados/resumen_finde.json.
"""
import os
import json
from html import escape
from datetime import datetime, date, timedelta
from flask import Blueprint, request, jsonify, Response

resumen_bp = Blueprint('resumen_finde', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(BASE_DIR, 'datos_privados', 'resumen_finde.json')
HORA_SYNC = (11, 0)
HORA_ENVIO = (20, 0)


def cargar_cfg():
    try:
        with open(CFG_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    cfg.setdefault('activo', False)  # se activa tras la prueba
    return cfg


def guardar_cfg(cfg):
    os.makedirs(os.path.dirname(CFG_FILE), exist_ok=True)
    tmp = CFG_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CFG_FILE)


def viernes_pasado(d):
    """Viernes del finde que acaba de terminar (lunes → hace 3 días; de martes a jueves, el último)."""
    return d - timedelta(days=(d.weekday() - 4) % 7)


# ---------- datos ----------

def informe_finde(sat):
    """Equipos del INFORME FINDE WHATSAPP de ese sábado: [{nombre, formulario: bool, audio: bool}]."""
    from app import app as _app
    from deportivo import api_informes_semanales_get
    from flask import session
    with _app.test_request_context(f'/api/informes_semanales?finde={sat.isoformat()}'):
        session['usuario'] = 'admin'
        r = api_informes_semanales_get()
        datos = (r[0] if isinstance(r, tuple) else r).get_json() or {}
    return [{'nombre': e['nombre'], 'formulario': bool(e.get('formulario')), 'audio': (e.get('audio') or '').upper() == 'SI'}
            for e in datos.get('equipos', [])]


def _stats_cumplimiento(partidos):
    v = sum(1 for p in partidos if p.get('cumplido') is True)
    r = sum(1 for p in partidos if p.get('cumplido') is False)
    return v, r, (round(100 * v / (v + r)) if (v + r) else 0)


def _pct(x, n):
    return round(100 * x / n) if n else 0


# ---------- imágenes ----------

_TH = 'padding:6px 8px;font-size:9.5px;font-weight:bold;color:#64748b;background-color:#f8fafc;border-bottom:2px solid #e2e8f0;text-align:left;'
_TD = 'padding:7px 8px;font-size:10.5px;border-bottom:1px solid #e2e8f0;vertical-align:top;'
_COL = {'G': '#16a34a', 'E': '#d97706', 'P': '#dc2626'}


def _nuestro_primero(p):
    """Resultado con nuestros goles primero ('9 - 0' jugando fuera → '0-9')."""
    import re
    m = re.search(r'(\d+)\s*-\s*(\d+)', str(p.get('resultado') or ''))
    if not m:
        return str(p.get('resultado') or '')
    a, b = m.group(1), m.group(2)
    return f'{a}-{b}' if p.get('es_local') else f'{b}-{a}'


def html_cumplimiento(partidos, vie):
    from objetivos_finde import texto_finde
    v, r, pct = _stats_cumplimiento(partidos)
    filas = []
    for p in partidos:
        equipo = escape(f"{p.get('categoria', '')} {p.get('letra', '')}".strip())
        if p.get('formato'):
            equipo += f' <span style="color:#94a3b8;font-size:8px;">({escape(p["formato"])})</span>'
        est = p.get('estado') or ''
        res = (f'<b style="color:{_COL.get(est, "#64748b")};">{escape(est)} {escape(_nuestro_primero(p))}</b>'
               if est else '<span style="color:#94a3b8;">Sin jugar</span>')
        cum = ('<b style="color:#16a34a;font-size:14px;">&#10004;</b>' if p.get('cumplido') is True else
               '<b style="color:#dc2626;font-size:14px;">&#10008;</b>' if p.get('cumplido') is False else '<span style="color:#cbd5e1;">–</span>')
        obj = f'<b>{escape(p["objetivo"])}</b>' if p.get('objetivo') else '<span style="color:#94a3b8;">–</span>'
        filas.append(f'<tr><td style="{_TD}font-weight:bold;">{equipo}</td>'
                     f'<td style="{_TD}">{escape(str(p.get("rival", "")))}</td>'
                     f'<td style="{_TD}">{res}</td><td style="{_TD}text-align:center;">{cum}</td><td style="{_TD}">{obj}</td></tr>')
    titulo = texto_finde(vie).replace('OBJETIVOS', 'RESULTADOS')
    return (f'<p style="font-family:sans-serif;font-size:15px;font-weight:bold;color:#1e293b;margin:0 0 4px 0;">{escape(titulo)} — CUMPLIMIENTO</p>'
            f'<p style="font-family:sans-serif;font-size:11px;font-weight:bold;color:#1e293b;margin:0 0 8px 0;">{pct}% cumplidos · '
            f'<span style="color:#16a34a;">{v} cumplidos</span> · <span style="color:#dc2626;">{r} no cumplidos</span></p>'
            f'<table style="width:100%;border-collapse:collapse;font-family:sans-serif;">'
            f'<tr><th style="{_TH}width:150px;">EQUIPO</th>'
            f'<th style="{_TH}">RIVAL</th><th style="{_TH}width:90px;">RESULTADO</th><th style="{_TH}width:75px;text-align:center;">CUMPLIDO</th>'
            f'<th style="{_TH}width:110px;">OBJETIVO</th></tr>' + ''.join(filas) + '</table>')


def html_informe(equipos, vie):
    from objetivos_finde import texto_finde
    si = lambda b: '<b style="color:#16a34a;">SÍ</b>' if b else '<b style="color:#dc2626;">NO</b>'
    n = len(equipos)
    nf, na = sum(e['formulario'] for e in equipos), sum(e['audio'] for e in equipos)
    filas = ''.join(f'<tr><td style="{_TD}font-weight:bold;">{escape(e["nombre"])}</td>'
                    f'<td style="{_TD}text-align:center;">{si(e["formulario"])}</td>'
                    f'<td style="{_TD}text-align:center;">{si(e["audio"])}</td></tr>' for e in equipos)
    total = (f'<tr><td style="{_TD}font-weight:bold;background-color:#f1f5f9;">TOTAL</td>'
             f'<td style="{_TD}text-align:center;font-weight:bold;background-color:#f1f5f9;">{nf} de {n} · {_pct(nf, n)}%</td>'
             f'<td style="{_TD}text-align:center;font-weight:bold;background-color:#f1f5f9;">{na} de {n} · {_pct(na, n)}%</td></tr>')
    titulo = texto_finde(vie).replace('OBJETIVOS', 'FORMULARIOS Y AUDIOS')
    return (f'<p style="font-family:sans-serif;font-size:15px;font-weight:bold;color:#1e293b;margin:0 0 8px 0;">{escape(titulo)}</p>'
            f'<table style="width:100%;border-collapse:collapse;font-family:sans-serif;">'
            f'<tr><th style="{_TH}">EQUIPO</th><th style="{_TH}text-align:center;width:160px;">FORMULARIO PARTIDO</th>'
            f'<th style="{_TH}text-align:center;width:160px;">AUDIO ENVIADO</th></tr>' + filas + total + '</table>')


def preparar(vie):
    """(png_cumplimiento, png_informe, texto) del finde del viernes `vie`; None si ese finde no hubo partidos."""
    from objetivos_finde import partidos_finde, html_a_png, texto_finde
    partidos = partidos_finde(vie)
    if not partidos:
        return None
    equipos = informe_finde(vie + timedelta(days=1))
    v, r, pct = _stats_cumplimiento(partidos)
    n, nf, na = len(equipos), sum(e['formulario'] for e in equipos), sum(e['audio'] for e in equipos)
    texto = (f"{texto_finde(vie).replace('OBJETIVOS', 'RESULTADOS')}\n"
             f"Objetivos cumplidos: {v} de {v + r} ({pct}%)\n"
             f"Formularios: {nf} de {n} ({_pct(nf, n)}%) · Audios: {na} de {n} ({_pct(na, n)}%)")
    png1 = html_a_png(html_cumplimiento(partidos, vie), filas=len(partidos), ancho=900)
    png2 = html_a_png(html_informe(equipos, vie), filas=n + 2, ancho=600)
    return png1, png2, texto


def enviar(vie, chat_id=None):
    """Envía las dos imágenes (la primera con el texto) al grupo FR Entrenadores, o a chat_id (prueba)."""
    from objetivos_finde import _grupo_fr, enviar_imagen
    datos = preparar(vie)
    if not datos:
        return False, 'Ese fin de semana no hubo partidos: no se envía nada'
    destino = chat_id or _grupo_fr()
    if not destino:
        return False, 'No encuentro el grupo "FR Entrenadores" en el WhatsApp del club'
    png1, png2, texto = datos
    ok, detalle = enviar_imagen(destino, png1, texto)
    if not ok:
        return ok, detalle
    return enviar_imagen(destino, png2, '')


def tick(ahora):
    """Lunes: 11:00 actualiza los datos de la Federación; 20:00 envía el resumen (una vez)."""
    if ahora.weekday() != 0:
        return
    cfg = cargar_cfg()
    if not cfg.get('activo'):
        return
    hoy = ahora.date().isoformat()
    sync = ahora.replace(hour=HORA_SYNC[0], minute=HORA_SYNC[1], second=0, microsecond=0)
    envio = ahora.replace(hour=HORA_ENVIO[0], minute=HORA_ENVIO[1], second=0, microsecond=0)
    if cfg.get('sync') != hoy and sync <= ahora < sync + timedelta(hours=2):
        cfg['sync'] = hoy
        guardar_cfg(cfg)
        try:
            import competicion_scraper as cs
            from sincronizar_rffm import credenciales
            ok, msg = cs.sync_rffm(*credenciales())
            print(f"[RESUMEN] Actualización de la Federación: {'OK' if ok else msg}", flush=True)
        except Exception as e:
            print(f"[RESUMEN] Actualización de la Federación: {e}", flush=True)
    if cfg.get('ultimo') != hoy and envio <= ahora < envio + timedelta(minutes=30):
        cfg['ultimo'] = hoy
        guardar_cfg(cfg)
        try:
            ok, detalle = enviar(viernes_pasado(ahora.date()))
            print(f"[RESUMEN] {'Enviado al grupo' if ok else detalle}", flush=True)
        except Exception as e:
            print(f"[RESUMEN] Error: {e}", flush=True)


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@resumen_bp.route('/api/whatsapp/resumen', methods=['GET', 'POST'])
def api_cfg():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    cfg = cargar_cfg()
    if request.method == 'POST':
        cfg['activo'] = bool((request.json or {}).get('activo'))
        guardar_cfg(cfg)
    return jsonify({'status': 'ok', 'activo': cfg['activo'], 'ultimo': cfg.get('ultimo', '')})


@resumen_bp.route('/api/whatsapp/resumen/imagen/<int:n>')
def api_imagen(n):
    """Vista previa: n=1 cumplimiento, n=2 formularios y audios (finde que acaba de terminar)."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        datos = preparar(viernes_pasado(date.today()))
        if not datos:
            return jsonify({'status': 'error', 'message': 'Ese fin de semana no hubo partidos'}), 404
        return Response(datos[0] if n == 1 else datos[1], mimetype='image/png', headers={'Cache-Control': 'no-store'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]}), 500


@resumen_bp.route('/api/whatsapp/resumen/prueba', methods=['POST'])
def api_prueba():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    num = ''.join(c for c in str((request.json or {}).get('telefono') or '') if c.isdigit())
    num = '34' + num if len(num) == 9 else num
    if len(num) < 10:
        return jsonify({'status': 'error', 'message': 'Teléfono no válido'}), 400
    try:
        ok, detalle = enviar(viernes_pasado(date.today()), f'{num}@c.us')
        return jsonify({'status': 'ok' if ok else 'error', 'message': detalle or 'Enviado'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]})
