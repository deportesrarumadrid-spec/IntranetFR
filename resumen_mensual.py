"""Resumen mensual por WhatsApp a cada entrenador (día 1 a las 10:00), uno por equipo (sin Aficionados ni Debutantes).

Imagen con el mes que acaba de terminar: asistencias (días con lista pasada, % de asistencia de los jugadores y
quién más ha faltado), entrenamientos subidos, objetivos del mes (✓/✗) y progresión (jornadas de la temporada con
✓/✗ y % de cumplimiento, e ida/vuelta contra cada rival). Debajo, como texto, los puntos de la IA.
Estado en datos_privados/resumen_mensual.json.
"""
import os
import re
import json
import unicodedata
from html import escape
from datetime import datetime, date, timedelta
from flask import Blueprint, request, jsonify, Response

mensual_bp = Blueprint('resumen_mensual', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'datos_privados', 'resumen_mensual.json')
HORA_ENVIO = (10, 0)
_MESES = ['ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE']
_COL = {'G': '#16a34a', 'E': '#d97706', 'P': '#dc2626'}


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.replace('"', ' ').upper().split())


def _bonito(eq):
    return (' '.join(eq.upper().split()).replace('PREBENJAMIN', 'PREBENJAMÍN')
            .replace('BENJAMIN', 'BENJAMÍN').replace('ALEVIN', 'ALEVÍN'))


def _leer():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault('activo', True)
    return d


def _escribir(d):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def mes_anterior(d):
    primero = d.replace(day=1) - timedelta(days=1)
    return primero.year, primero.month


def _equipos():
    from informe_veo import _equipos_config
    return [e for e in _equipos_config() if not _norm(e).startswith(('AFICIONADO', 'DEBUTANTE'))]


# ---------- datos ----------

def _informe_mensual(anio, mes):
    """{EQUIPO_NORMALIZADO: datos del INFORME mensual de la intranet (asistencias pasadas, entrenos subidos…)}."""
    from app import app as _app
    from deportivo import api_informe_mensual
    from flask import session
    with _app.test_request_context(f'/api/informe_mensual?mes={anio}-{mes:02d}'):
        session['usuario'] = 'admin'
        r = api_informe_mensual()
        datos = (r[0] if isinstance(r, tuple) else r).get_json() or {}
    return {_norm(e['nombre']): e for e in datos.get('equipos', [])}


def _asistencia_jugadores(filas, equipo, anio, mes):
    """(% de asistencia de los jugadores del mes, [(nombre, nº de NO)] de los que más faltaron).
    Los jugadores dados de baja en el mes (o antes, sin alta posterior) no cuentan."""
    import calendar
    from faltas_seguidas import _nombre, bajas, de_baja
    lb = bajas()
    fin_mes = date(anio, mes, calendar.monthrange(anio, mes)[1])
    si = total = 0
    faltas = {}
    for r in filas:
        if len(r) < 5 or _norm(r[1]) != _norm(equipo):
            continue
        p = r[0].strip().split('/')
        try:
            if int(p[1]) != mes or int(p[2]) != anio:
                continue
        except Exception:
            continue
        est = r[4].strip().upper()
        if est not in ('SI', 'NO', 'AV', 'EXC') or de_baja(lb, r[2], fin_mes):
            continue
        total += 1
        si += est == 'SI'
        if est == 'NO':
            faltas[_nombre(r[2])] = faltas.get(_nombre(r[2]), 0) + 1
    top = sorted(((n, c) for n, c in faltas.items() if c >= 2), key=lambda x: -x[1])[:3]
    return (round(100 * si / total) if total else None), top


def _partidos_equipo(equipo, obj_todos):
    """Partidos de la temporada del equipo (OBJ FINES DE SEMANA), por jornada."""
    from previa_rival import _equipo_config
    out = []
    for p in obj_todos:
        if _equipo_config(p, [equipo]) == equipo:
            try:
                dd, mm, yy = [int(x) for x in p['fecha'].split('-')]
                p['_fecha'] = date(yy, mm, dd)
            except Exception:
                continue
            out.append(p)
    return sorted(out, key=lambda p: p['_fecha'])


def _res(p):
    """'G 2-0' con nuestros goles primero, o '' si no se ha jugado."""
    m = re.search(r'(\d+)\s*-\s*(\d+)', str(p.get('resultado') or ''))
    if not m or not p.get('estado'):
        return ''
    a, b = m.group(1), m.group(2)
    return f"{p['estado']} {a}-{b}" if p.get('es_local') else f"{p['estado']} {b}-{a}"


def datos_equipo(equipo, anio, mes, filas_asis=None, informe=None, obj_todos=None):
    if filas_asis is None:
        from app import client, NOMBRE_EXCEL
        filas_asis = client.open(NOMBRE_EXCEL).worksheet('ASISTENCIAS').get_all_values()
    if informe is None:
        informe = _informe_mensual(anio, mes)
    if obj_todos is None:
        from competicion_scraper import construir_obj_semanales
        obj_todos = construir_obj_semanales()
    inf = informe.get(_norm(equipo), {})
    pct_asis, top_faltas = _asistencia_jugadores(filas_asis, equipo, anio, mes)
    # Días de entrenamiento del mes según la configuración del equipo, y días en que se pasó lista
    import calendar
    from aviso_diario import _dias_entreno
    dias_semana = _dias_entreno().get(_norm(equipo), set())
    entrenos = {date(anio, mes, n) for n in range(1, calendar.monthrange(anio, mes)[1] + 1) if date(anio, mes, n).weekday() in dias_semana}
    con_lista = set()
    for r in filas_asis:
        if len(r) > 1 and _norm(r[1]) == _norm(equipo):
            try:
                dd, mm, yy = [int(x) for x in r[0].strip().split()[0].split('/')]
                con_lista.add(date(yy, mm, dd))
            except Exception:
                pass
    asis_pasadas = {'hechas': len(con_lista & entrenos), 'total': len(entrenos)}
    sesiones = {'subidos': min((inf.get('entrenamientos') or {}).get('subidos', 0), len(entrenos)), 'total': len(entrenos)}
    partidos = [p for p in _partidos_equipo(equipo, obj_todos) if 'DESCANSA' not in _norm(p.get('rival'))]
    del_mes = [p for p in partidos if p['_fecha'].year == anio and p['_fecha'].month == mes and p.get('estado')]
    pct = lambda ps: (round(100 * sum(1 for p in ps if p.get('cumplido') is True) /
                            max(1, sum(1 for p in ps if p.get('cumplido') is not None))) if any(p.get('cumplido') is not None for p in ps) else None)
    jugados_temp = [p for p in partidos if p['_fecha'] <= date(anio, mes, 28) + timedelta(days=4) and p.get('estado')]
    rivales = {}
    for p in partidos:
        if p.get('estado'):
            rivales.setdefault(p.get('rival'), []).append(_res(p))
    return {
        'equipo': equipo, 'anio': anio, 'mes': mes,
        'asis_pasadas': asis_pasadas, 'entrenos': sesiones,
        'pct_asistencia': pct_asis, 'top_faltas': top_faltas,
        'partidos_mes': del_mes, 'pct_mes': pct(del_mes), 'pct_temporada': pct(jugados_temp),
        'jornadas': jugados_temp, 'ida_vuelta': [(r, v[0], v[1] if len(v) > 1 else '') for r, v in rivales.items()],
    }


# ---------- imagen ----------

_TH = 'padding:5px 8px;font-size:9.5px;font-weight:bold;color:#64748b;background-color:#f8fafc;border-bottom:2px solid #e2e8f0;text-align:left;'
_TD = 'padding:5px 8px;font-size:10.5px;border-bottom:1px solid #e2e8f0;'
_TIT = 'font-family:sans-serif;font-size:12px;font-weight:bold;color:#E31E24;margin:14px 0 6px 0;'
_TXT = 'font-family:sans-serif;font-size:11px;color:#1e293b;margin:2px 0;'


def _marca(c):
    return ('<b style="color:#16a34a;">&#10004;</b>' if c is True else
            '<b style="color:#dc2626;">&#10008;</b>' if c is False else '<span style="color:#cbd5e1;">–</span>')


def _color_pct(v):
    return '#94a3b8' if v is None else ('#16a34a' if v >= 75 else '#d97706' if v >= 50 else '#dc2626')


def html_resumen(d):
    eq, mes_txt = _bonito(d['equipo']), f"{_MESES[d['mes'] - 1]} {d['anio']}"
    ap, en = d['asis_pasadas'] or {}, d['entrenos'] or {}
    h = [f'<p style="font-family:sans-serif;font-size:17px;font-weight:bold;color:#1e293b;margin:0;">RESUMEN MENSUAL · {escape(eq)}</p>',
         f'<p style="font-family:sans-serif;font-size:22px;font-weight:bold;color:#E31E24;margin:4px 0 0 0;">{mes_txt}</p>']
    h.append(f'<p style="{_TIT}">ASISTENCIAS</p>')
    if ap.get('total'):
        h.append(f'<p style="{_TXT}">Lista pasada: <b>{ap.get("hechas", 0)} de {ap["total"]}</b> días de entrenamiento</p>')
    if d['pct_asistencia'] is not None:
        h.append(f'<p style="{_TXT}">Asistencia de los jugadores: <b style="color:{_color_pct(d["pct_asistencia"])};">{d["pct_asistencia"]}%</b></p>')
    if d['top_faltas']:
        h.append(f'<p style="{_TXT}">Más faltas: ' + ' · '.join(f'<b>{escape(n)}</b> ({c})' for n, c in d['top_faltas']) + '</p>')
    elif d['pct_asistencia'] is not None:
        h.append(f'<p style="{_TXT}">Más faltas: ningún jugador con más de 1 falta</p>')
    h.append(f'<p style="{_TIT}">ENTRENAMIENTOS</p>')
    h.append(f'<p style="{_TXT}">Sesiones subidas: <b>{en.get("subidos", 0)} de {en.get("total", 0)}</b></p>')

    h.append(f'<p style="{_TIT}">OBJETIVOS DEL MES' + (f' · <span style="color:{_color_pct(d["pct_mes"])};">{d["pct_mes"]}%</span>' if d['pct_mes'] is not None else '') + '</p>')
    if d['partidos_mes']:
        filas = ''.join(f'<tr><td style="{_TD}">{escape(str(p.get("rival", "")))}</td>'
                        f'<td style="{_TD}"><b style="color:{_COL.get(p.get("estado"), "#64748b")};">{escape(_res(p))}</b></td>'
                        f'<td style="{_TD}"><b>{escape(p.get("objetivo") or "–")}</b></td><td style="{_TD}text-align:center;">{_marca(p.get("cumplido"))}</td></tr>'
                        for p in d['partidos_mes'])
        h.append(f'<table style="width:100%;border-collapse:collapse;font-family:sans-serif;"><tr><th style="{_TH}">RIVAL</th>'
                 f'<th style="{_TH}width:70px;">RESULTADO</th><th style="{_TH}width:110px;">OBJETIVO</th><th style="{_TH}width:60px;text-align:center;">CUMPLIDO</th></tr>{filas}</table>')
    else:
        h.append(f'<p style="{_TXT}color:#94a3b8;">Sin partidos este mes.</p>')

    h.append(f'<p style="{_TIT}">PROGRESIÓN DE LA TEMPORADA' + (f' · <span style="color:{_color_pct(d["pct_temporada"])};">{d["pct_temporada"]}% cumplido</span>' if d['pct_temporada'] is not None else '') + '</p>')
    if d['jornadas']:
        celdas = ''.join(f'<td style="padding:3px 4px;text-align:center;font-size:8.5px;color:#64748b;">J{re.sub(r"[^0-9]", "", str(p.get("jornada", ""))) or "?"}<br>{_marca(p.get("cumplido"))}</td>'
                         for p in d['jornadas'])
        h.append(f'<table style="border-collapse:collapse;font-family:sans-serif;"><tr>{celdas}</tr></table>')
    if d['ida_vuelta']:
        filas = ''.join(f'<tr><td style="{_TD}">{escape(str(r))}</td>'
                        f'<td style="{_TD}"><b style="color:{_COL.get(i[:1], "#94a3b8")};">{escape(i) or "–"}</b></td>'
                        f'<td style="{_TD}"><b style="color:{_COL.get(v[:1], "#94a3b8")};">{escape(v) or "–"}</b></td></tr>'
                        for r, i, v in d['ida_vuelta'])
        h.append(f'<table style="width:100%;border-collapse:collapse;font-family:sans-serif;margin-top:8px;"><tr><th style="{_TH}">RIVAL</th>'
                 f'<th style="{_TH}width:80px;">IDA</th><th style="{_TH}width:80px;">VUELTA</th></tr>{filas}</table>')
    return ''.join(h)


def imagen(d):
    from objetivos_finde import html_a_png
    return html_a_png(html_resumen(d), filas=8 + len(d['partidos_mes']) + len(d['ida_vuelta']), ancho=620)


# ---------- texto (IA) ----------

def texto_ia(d):
    from analisis_postpartido import _generar_ia
    resumen = {
        'equipo': d['equipo'], 'mes': f"{_MESES[d['mes'] - 1]} {d['anio']}",
        'dias_de_entrenamiento_con_lista_de_asistencia_pasada': f"{d['asis_pasadas'].get('hechas', 0)} de {d['asis_pasadas'].get('total', 0)}",
        'porcentaje_de_asistencia_de_los_jugadores': d['pct_asistencia'],
        'jugadores_con_2_o_mas_faltas': d['top_faltas'],
        'sesiones_de_entrenamiento_subidas_a_la_intranet': f"{d['entrenos'].get('subidos', 0)} de {d['entrenos'].get('total', 0)} días de entrenamiento",
        'objetivos_mes': [{'rival': p.get('rival'), 'resultado': _res(p), 'objetivo': p.get('objetivo'), 'cumplido': p.get('cumplido')}
                          for p in d['partidos_mes']],
        'pct_objetivos_mes': d['pct_mes'], 'pct_objetivos_temporada': d['pct_temporada'],
        'ida_vuelta': d['ida_vuelta'],
    }
    prompt = ("Eres director deportivo de un club de fútbol con nivel de élite. Con estos datos del mes de un equipo, "
              "escribe para su entrenador, en español, 3 puntos: lo más positivo, lo que hay que mejorar y el foco para el "
              "mes que viene. Cada punto en una línea que empiece por '• ', con la idea clave en negrita de WhatsApp "
              "(*así*), un guion y el dato que lo demuestra. Sin relleno, sin títulos. Habla SOLO de lo que aparece en "
              "estos datos (asistencia, sesiones subidas, objetivos y resultados); no deduzcas ni inventes nada más.\n\n"
              + json.dumps(resumen, ensure_ascii=False, default=str))
    texto = _generar_ia(prompt).replace('**', '*')
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    return '\n'.join(l if l.startswith('•') else '• ' + l.lstrip('-* ') for l in lineas[:4])


def pie(d, nombre=''):
    t = f"{'Hola ' + nombre + ', r' if nombre else 'R'}esumen de {_MESES[d['mes'] - 1].lower()} · *{_bonito(d['equipo'])}*"
    try:
        puntos = texto_ia(d)
        if puntos:
            t += '\n\n' + puntos
    except Exception as e:
        print(f"[RESUMEN MENSUAL] IA: {e}", flush=True)
    return t + '\n\nDirección Deportiva'


# ---------- envío ----------

def enviar_mes(anio, mes, solo_equipo=None, solo_a=None):
    """Envía el resumen de cada equipo a sus entrenadores (o solo a solo_a=(usuario, tel) para probar)."""
    from app import client, NOMBRE_EXCEL
    from competicion_scraper import construir_obj_semanales
    from formulario_finde import _entrenadores
    from objetivos_finde import enviar_imagen
    filas = client.open(NOMBRE_EXCEL).worksheet('ASISTENCIAS').get_all_values()
    informe, obj = _informe_mensual(anio, mes), construir_obj_semanales()
    res = []
    for eq in _equipos():
        if solo_equipo and _norm(eq) != _norm(solo_equipo):
            continue
        destinos = [solo_a] if solo_a else [(u, t) for u, t, _ in _entrenadores([eq]) if t]
        if not destinos:
            continue
        d = datos_equipo(eq, anio, mes, filas, informe, obj)
        png = imagen(d)
        texto = pie(d)
        ok = 0
        for usuario, tel in destinos:
            num = ''.join(c for c in tel if c.isdigit())
            num = '34' + num if len(num) == 9 else num
            bien, _ = enviar_imagen(f'{num}@c.us', png, texto.replace('Resumen de', f"Hola {usuario.split()[0].capitalize()}, resumen de", 1))
            ok += bool(bien)
        res.append(f'{eq}: {ok} de {len(destinos)}')
        print(f"[RESUMEN MENSUAL] {eq}: enviado a {ok} de {len(destinos)}", flush=True)
    return res


def tick(ahora):
    """Día 1 de cada mes a las 10:00: resumen del mes anterior."""
    if ahora.day != 1 or (ahora.hour, ahora.minute) != HORA_ENVIO:
        return
    d = _leer()
    if not d.get('activo') or d.get('ultimo') == ahora.date().isoformat():
        return
    d['ultimo'] = ahora.date().isoformat()
    _escribir(d)
    enviar_mes(*mes_anterior(ahora.date()))


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@mensual_bp.route('/api/whatsapp/mensual', methods=['GET', 'POST'])
def api_cfg():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    d = _leer()
    if request.method == 'POST':
        d['activo'] = bool((request.json or {}).get('activo'))
        _escribir(d)
    anio, mes = mes_anterior(date.today())
    return jsonify({'status': 'ok', 'activo': d['activo'], 'mes': f"{_MESES[mes - 1].capitalize()} {anio}"})


@mensual_bp.route('/api/whatsapp/mensual/imagen')
def api_imagen():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        anio, mes = mes_anterior(date.today())
        return Response(imagen(datos_equipo(request.args.get('equipo') or 'INFANTIL B', anio, mes)),
                        mimetype='image/png', headers={'Cache-Control': 'no-store'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]}), 500
