"""
ANÁLISIS POSTPARTIDO
- Los entrenadores lo rellenan desde el móvil (árbol de preguntas + checklist de fallos por líneas).
- Se guarda en la hoja "ANALISIS POSTPARTIDO" del Google Sheets (una fila por análisis, respuestas en JSON).
- Dirección Deportiva lo consulta desde su panel.
- La IA (Gemini) genera un informe a partir de las respuestas, y todo se puede descargar en PDF.
El árbol de preguntas está en static/data/arbol_analisis_<formato>.json.
"""
import os
import io
import re
import json
import uuid
import html
import unicodedata
from datetime import datetime

from flask import Blueprint, jsonify, request, session, current_app, send_file

analisis_bp = Blueprint('analisis_bp', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOJA = "ANALISIS POSTPARTIDO"
CABECERA = ["ID", "FECHA", "EQUIPO", "FORMATO", "RIVAL", "RESULTADO", "AUTOR",
            "CREADO", "ACTUALIZADO", "DATOS", "INFORME_IA"]
_COL = {c: i for i, c in enumerate(CABECERA)}

_arbol_cache = {}


# ───────────────────────── utilidades ─────────────────────────

def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '').upper())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return ' '.join(s.split())


def cargar_arbol(formato='F11'):
    """Árbol de preguntas del formato (F11 / F7). None si todavía no existe."""
    path = os.path.join(BASE_DIR, 'static', 'data', f'arbol_analisis_{formato.lower()}.json')
    if not os.path.exists(path):
        return None
    mtime = os.path.getmtime(path)
    cached = _arbol_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, encoding='utf-8') as f:
        arbol = json.load(f)
    _arbol_cache[path] = (mtime, arbol)
    return arbol


def formato_equipo(equipo):
    """F7 / F11 según CONFIGURACIÓN GENERAL (equipos_config); si no aparece, se deduce del nombre."""
    nombre = _norm(equipo)
    try:
        from deportivo import _get_equipos_config_path
        with open(_get_equipos_config_path(), encoding='utf-8') as f:
            cfg = json.load(f)
        for e in cfg.get('equipos', []):
            if _norm(e.get('nombre')) == nombre:
                return 'F7' if e.get('tipo') == 'F7' else 'F11'
    except Exception:
        pass
    if 'F7' in nombre.split() or any(k in nombre for k in ('BENJAMIN', 'DEBUTANTE', 'CHUPETIN')):
        return 'F7'
    return 'F11'


def _es_direccion():
    usuario = (session.get('usuario') or '').lower()
    perms = session.get('permisos', {}) or {}
    return usuario == 'admin' or perms.get('D.DEPORTIVA') == 'SI'


def _equipos_usuario():
    eqs = list(session.get('equipos_permitidos', []) or [])
    if session.get('equipo_defecto'):
        eqs.append(session['equipo_defecto'])
    return {_norm(e) for e in eqs if e}


def _puede_equipo(equipo):
    return _es_direccion() or _norm(equipo) in _equipos_usuario()


def _hoja():
    """Hoja ANALISIS POSTPARTIDO (se crea con su cabecera si no existe)."""
    import gspread
    ss = current_app.gs_client.open(current_app.gs_name)
    try:
        return ss.worksheet(HOJA)
    except gspread.exceptions.WorksheetNotFound:
        ss.add_worksheet(title=HOJA, rows=1000, cols=len(CABECERA))
        ws = ss.worksheet(HOJA)
        ws.update('A1', [CABECERA])
        return ws


def _filas(ws):
    # Lectura directa (sin la caché de 5s) para no duplicar filas si se guarda dos veces seguidas
    valores = getattr(ws, '_ws', ws).get_all_values()
    return valores[1:] if valores else []


def _fila_a_dict(fila, con_datos=True):
    fila = list(fila) + [''] * (len(CABECERA) - len(fila))
    d = {c.lower(): fila[i] for c, i in _COL.items() if c not in ('DATOS', 'INFORME_IA')}
    if con_datos:
        try:
            d['datos'] = json.loads(fila[_COL['DATOS']] or '{}')
        except ValueError:
            d['datos'] = {}
        d['informe_ia'] = fila[_COL['INFORME_IA']]
    else:
        d['tiene_informe'] = bool(fila[_COL['INFORME_IA']].strip())
    return d


def _buscar(ws, analisis_id):
    for i, fila in enumerate(_filas(ws), start=2):
        if fila and fila[0] == analisis_id:
            return i, fila
    return None, None


# ───────────────────── respuestas → texto legible ─────────────────────

def respuestas_a_lineas(arbol, datos):
    """Convierte las respuestas (ids) en texto. Devuelve [(bloque, [(pregunta, [respuestas])])], checklist, observaciones."""
    resp = (datos or {}).get('respuestas', {}) or {}
    out = []
    for b in arbol.get('bloques', []):
        preguntas = []
        for s in b.get('secciones', []):
            for p in s.get('preguntas', []):
                val = resp.get(p['id'])
                elegidas = val if isinstance(val, list) else ([val] if val else [])
                textos = []
                for o in p.get('opciones', []):
                    if o['id'] not in elegidas:
                        continue
                    t = o['texto'] + (f" ({o['desc']})" if o.get('desc') else '')
                    sub = o.get('sub')
                    if sub:
                        sv = resp.get(f"{p['id']}>{o['id']}")
                        sv = sv if isinstance(sv, list) else ([sv] if sv else [])
                        subt = [so['texto'] for so in sub.get('opciones', []) if so['id'] in sv]
                        if subt:
                            t += f" → {sub['texto']}: " + '; '.join(subt)
                    textos.append(t)
                if textos:
                    preguntas.append((f"{s['titulo']} · {p['texto']}", textos))
        if preguntas:
            out.append((f"{b['titulo']} ({b.get('subtitulo', '')})", preguntas))

    chk = (datos or {}).get('checklist', {}) or {}
    checklist = []
    for linea in arbol.get('checklist', {}).get('lineas', []):
        for fase, etiqueta in (('ofe', 'Ofensivo'), ('def', 'Defensivo')):
            marcados = chk.get(f"{linea['id']}.{fase}") or []
            textos = [o['texto'] for o in linea.get(fase, []) if o['id'] in marcados]
            if textos:
                checklist.append((f"{linea['nombre']} - {etiqueta}", textos))
    return out, checklist, ((datos or {}).get('observaciones') or '').strip()


def _texto_para_ia(arbol, cab, datos):
    bloques, checklist, obs = respuestas_a_lineas(arbol, datos)
    lineas = [f"Equipo: {cab.get('equipo')} ({cab.get('formato')})",
              f"Fecha: {cab.get('fecha')} · Rival: {cab.get('rival') or '-'} · Resultado: {cab.get('resultado') or '-'}", ""]
    for titulo, preguntas in bloques:
        lineas.append(f"## {titulo}")
        for q, rs in preguntas:
            lineas.append(f"- {q}")
            lineas += [f"    · {r}" for r in rs]
    if checklist:
        lineas.append("## Checklist de fallos por líneas")
        for titulo, rs in checklist:
            lineas.append(f"- {titulo}: " + '; '.join(rs))
    if obs:
        lineas += ["## Observaciones del entrenador", obs]
    return '\n'.join(lineas)


# ───────────────────────── markdown sencillo ─────────────────────────

def markdown_a_html(md):
    """Conversión mínima (títulos, listas, negrita) para el informe de la IA."""
    out, lista = [], None
    def cerrar():
        nonlocal lista
        if lista:
            out.append(f'</{lista}>'); lista = None
    for raw in (md or '').splitlines():
        linea = html.escape(raw.rstrip())
        linea = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', linea)
        st = linea.strip()
        if not st:
            cerrar(); continue
        m = re.match(r'^(#{1,4})\s+(.*)', st)
        if m:
            cerrar(); n = min(len(m.group(1)) + 1, 4)
            out.append(f'<h{n}>{m.group(2)}</h{n}>'); continue
        m = re.match(r'^[-*•]\s+(.*)', st)
        if m:
            if lista != 'ul':
                cerrar(); out.append('<ul>'); lista = 'ul'
            out.append(f'<li>{m.group(1)}</li>'); continue
        m = re.match(r'^\d+[.)]\s+(.*)', st)
        if m:
            if lista != 'ol':
                cerrar(); out.append('<ol>'); lista = 'ol'
            out.append(f'<li>{m.group(1)}</li>'); continue
        cerrar(); out.append(f'<p>{st}</p>')
    cerrar()
    return '\n'.join(out)


# ───────────────────────────── rutas ─────────────────────────────

@analisis_bp.route('/api/analisis_pp/partidos')
def api_partidos():
    """Partidos del equipo (RFFM) para rellenar rival y resultado según la fecha."""
    if not session.get('usuario'):
        return jsonify({"status": "error"}), 401
    equipo = (request.args.get('equipo') or '').strip()
    tokens_eq = set(_norm(equipo).split()) - {'F11'}
    partidos = []
    try:
        from competicion_scraper import construir_obj_semanales
        for p in construir_obj_semanales():
            toks = set(_norm(f"{p.get('categoria', '')} {p.get('letra', '')}").split())
            if p.get('formato') == 'F7':
                toks.add('F7')
            if toks - {'F11'} != tokens_eq:
                continue
            try:
                f = datetime.strptime(p.get('fecha') or '', '%d-%m-%Y')
            except ValueError:
                continue
            partidos.append({"fecha": f.strftime('%Y-%m-%d'), "rival": p.get('rival', ''),
                             "resultado": p.get('resultado', ''), "es_local": p.get('es_local'),
                             "jornada": p.get('jornada', '')})
    except Exception as e:
        print(f"Error partidos analisis_pp: {e}")
    partidos.sort(key=lambda x: x['fecha'], reverse=True)
    return jsonify({"status": "ok", "partidos": partidos})


@analisis_bp.route('/api/analisis_pp/lista')
def api_lista():
    if not session.get('usuario'):
        return jsonify({"status": "error"}), 401
    equipo = (request.args.get('equipo') or '').strip()
    if equipo and not _puede_equipo(equipo):
        return jsonify({"status": "error", "message": "Sin acceso a este equipo"}), 403
    if not equipo and not _es_direccion():
        return jsonify({"status": "error", "message": "Indica el equipo"}), 400
    try:
        filas = _filas(_hoja())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    lista = [_fila_a_dict(f, con_datos=False) for f in filas if f and f[0]]
    if equipo:
        lista = [a for a in lista if _norm(a['equipo']) == _norm(equipo)]
    lista.sort(key=lambda a: (a['fecha'], a['actualizado']), reverse=True)
    return jsonify({"status": "ok", "analisis": lista})


@analisis_bp.route('/api/analisis_pp/<analisis_id>')
def api_uno(analisis_id):
    if not session.get('usuario'):
        return jsonify({"status": "error"}), 401
    try:
        _, fila = _buscar(_hoja(), analisis_id)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    if not fila:
        return jsonify({"status": "error", "message": "No encontrado"}), 404
    a = _fila_a_dict(fila)
    if not _puede_equipo(a['equipo']):
        return jsonify({"status": "error", "message": "Sin acceso"}), 403
    a['informe_html'] = markdown_a_html(a.get('informe_ia'))
    # Respuestas ya en texto, para mostrarlas en Dirección Deportiva sin interpretar el árbol
    bloques, checklist, obs = respuestas_a_lineas(cargar_arbol(a.get('formato') or 'F11') or {}, a['datos'])
    a['resumen'] = {
        "bloques": [{"titulo": t, "preguntas": [{"texto": q, "respuestas": rs} for q, rs in ps]} for t, ps in bloques],
        "checklist": [{"titulo": t, "respuestas": rs} for t, rs in checklist],
        "observaciones": obs,
    }
    return jsonify({"status": "ok", "analisis": a})


@analisis_bp.route('/api/analisis_pp/guardar', methods=['POST'])
def api_guardar():
    """Crea o actualiza un análisis. Body: {id?, equipo, fecha, rival, resultado, datos:{respuestas, checklist, observaciones}}"""
    if not session.get('usuario'):
        return jsonify({"status": "error"}), 401
    d = request.json or {}
    equipo = (d.get('equipo') or '').strip()
    fecha = (d.get('fecha') or '').strip()
    if not equipo or not fecha:
        return jsonify({"status": "error", "message": "Faltan equipo o fecha"}), 400
    if not _puede_equipo(equipo):
        return jsonify({"status": "error", "message": "Sin acceso a este equipo"}), 403
    datos = d.get('datos') or {}
    ahora = datetime.now().strftime('%Y-%m-%d %H:%M')
    try:
        ws = _hoja()
        analisis_id = (d.get('id') or '').strip()
        fila_idx, fila = _buscar(ws, analisis_id) if analisis_id else (None, None)
        if fila_idx:
            actual = _fila_a_dict(fila)
            if not _puede_equipo(actual['equipo']):
                return jsonify({"status": "error", "message": "Sin acceso"}), 403
            creado, informe = actual['creado'], actual.get('informe_ia', '')
        else:
            analisis_id = datetime.now().strftime('%Y%m%d') + '-' + uuid.uuid4().hex[:6]
            creado, informe = ahora, ''
        valores = [analisis_id, fecha, equipo, formato_equipo(equipo), (d.get('rival') or '').strip(),
                   (d.get('resultado') or '').strip(), session.get('usuario', ''), creado, ahora,
                   json.dumps(datos, ensure_ascii=False), informe]
        if fila_idx:
            ws.update(f'A{fila_idx}:K{fila_idx}', [valores], value_input_option='RAW')
        else:
            ws.append_row(valores, value_input_option='RAW')
        return jsonify({"status": "ok", "id": analisis_id})
    except Exception as e:
        print(f"Error guardando analisis_pp: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


def _modelo_gemini():
    import google.generativeai as genai
    with open(os.path.join(BASE_DIR, 'secretos.json'), encoding='utf-8') as f:
        cfg = json.load(f)
    api_key = cfg.get('gemini_api_key') or cfg.get('GEMINI_API_KEY')
    if not api_key:
        raise RuntimeError("No hay clave de Gemini configurada")
    genai.configure(api_key=api_key)
    modelos = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    nombre = next((m for m in modelos if 'gemini-2.5-flash' in m),
                  next((m for m in modelos if 'gemini-2.0-flash' in m),
                       next((m for m in modelos if 'flash' in m), modelos[0] if modelos else 'models/gemini-1.5-flash')))
    return genai.GenerativeModel(model_name=nombre)


_PROMPT_IA = """Eres un analista táctico de fútbol con Licencia UEFA Pro y experiencia en análisis de datos de rendimiento.
Un entrenador de cantera del club ha rellenado, tras el partido, un árbol de análisis táctico y un checklist de fallos por líneas.
Analiza SOLO lo que se ha marcado (no inventes datos que no aparezcan) y adapta el nivel a la categoría del equipo (fútbol formativo).
Escribe en español, con lenguaje técnico de alta competencia pero claro y accionable para el cuerpo técnico.

Devuelve el informe en Markdown con EXACTAMENTE estas secciones:

## Diagnóstico estructural colectivo
Causas raíz de lo ocurrido (por qué falló o funcionó la salida, la presión, las transiciones y las vigilancias), relacionando las fases entre sí.

## Evaluación línea por línea
Un apartado breve para Portero, Defensa, Medios y Atacantes (extremos y delanteros).

## Focos prioritarios para el microciclo
Los 3 problemas tácticos más críticos, numerados, de más a menos importante, con una frase sobre por qué.

## Propuesta de tareas de entrenamiento
De 2 a 3 tareas concretas ajustadas a las deficiencias detectadas. Para cada una: nombre, día recomendado del microciclo,
objetivo, organización (espacio, número de jugadores, duración) y consignas clave del entrenador.

Datos del partido y respuestas del entrenador:
"""


@analisis_bp.route('/api/analisis_pp/ia', methods=['POST'])
def api_ia():
    """Genera el informe con IA. Body: {id, equipo, fecha, rival, resultado, datos}. Si hay id, lo guarda en la hoja."""
    if not session.get('usuario'):
        return jsonify({"status": "error"}), 401
    d = request.json or {}
    equipo = (d.get('equipo') or '').strip()
    if not equipo or not _puede_equipo(equipo):
        return jsonify({"status": "error", "message": "Sin acceso a este equipo"}), 403
    formato = formato_equipo(equipo)
    arbol = cargar_arbol(formato)
    if not arbol:
        return jsonify({"status": "error", "message": f"Todavía no hay árbol de análisis para {formato}"}), 400
    datos = d.get('datos') or {}
    cab = {"equipo": equipo, "formato": formato, "fecha": d.get('fecha'), "rival": d.get('rival'), "resultado": d.get('resultado')}
    texto = _texto_para_ia(arbol, cab, datos)
    if texto.count('\n') < 4:
        return jsonify({"status": "error", "message": "Rellena alguna pregunta antes de generar el análisis"}), 400
    try:
        resp = _modelo_gemini().generate_content(_PROMPT_IA + texto, request_options={"timeout": 120})
        informe = (resp.text or '').strip()
    except Exception as e:
        print(f"Error IA analisis_pp: {e}")
        return jsonify({"status": "error", "message": f"Error de la IA: {e}"}), 500

    analisis_id = (d.get('id') or '').strip()
    if analisis_id:
        try:
            ws = _hoja()
            fila_idx, fila = _buscar(ws, analisis_id)
            if fila_idx and _puede_equipo(_fila_a_dict(fila)['equipo']):
                ws.update(f'K{fila_idx}', [[informe]], value_input_option='RAW')
        except Exception as e:
            print(f"Error guardando informe IA analisis_pp: {e}")
    return jsonify({"status": "ok", "informe": informe, "informe_html": markdown_a_html(informe)})


_CSS_PDF = """
body { font-family: sans-serif; font-size: 9.5pt; color: #1e293b; }
h1 { font-size: 16pt; color: #E31E24; margin: 0 0 2pt 0; }
h2 { font-size: 12pt; color: #1e293b; border-bottom: 1px solid #cbd5e1; margin: 12pt 0 4pt 0; padding-bottom: 2pt; }
h3 { font-size: 10.5pt; color: #E31E24; margin: 8pt 0 3pt 0; }
h4 { font-size: 10pt; margin: 6pt 0 2pt 0; }
p { margin: 2pt 0 4pt 0; }
ul, ol { margin: 2pt 0 4pt 12pt; }
li { margin-bottom: 2pt; }
.cab { color: #475569; font-size: 10pt; margin-bottom: 6pt; }
.q { font-weight: bold; margin-top: 5pt; }
.r { margin-left: 10pt; }
"""


@analisis_bp.route('/analisis_pp/<analisis_id>/pdf')
def pdf(analisis_id):
    if not session.get('usuario'):
        return "No autorizado", 401
    _, fila = _buscar(_hoja(), analisis_id)
    if not fila:
        return "No encontrado", 404
    a = _fila_a_dict(fila)
    if not _puede_equipo(a['equipo']):
        return "Sin acceso", 403
    arbol = cargar_arbol(a.get('formato') or 'F11') or {}
    bloques, checklist, obs = respuestas_a_lineas(arbol, a['datos'])
    e = html.escape
    partes = [f"<h1>Análisis postpartido · {e(a['equipo'])}</h1>",
              f"<div class='cab'>{e(a['fecha'])} · Rival: {e(a['rival'] or '-')} · Resultado: {e(a['resultado'] or '-')}"
              f" · Autor: {e(a['autor'])}</div>"]
    if a.get('informe_ia'):
        partes += ["<h2>Informe de análisis (IA)</h2>", markdown_a_html(a['informe_ia'])]
    partes.append("<h2>Respuestas del árbol táctico</h2>")
    if not bloques:
        partes.append("<p>Sin respuestas.</p>")
    for titulo, preguntas in bloques:
        partes.append(f"<h3>{e(titulo)}</h3>")
        for q, rs in preguntas:
            partes.append(f"<div class='q'>{e(q)}</div>")
            partes += [f"<div class='r'>- {e(r.replace('→', '>'))}</div>" for r in rs]
    if checklist:
        partes.append("<h2>Checklist de fallos por líneas</h2>")
        for titulo, rs in checklist:
            partes.append(f"<h4>{e(titulo)}</h4><ul>" + ''.join(f"<li>{e(r)}</li>" for r in rs) + "</ul>")
    if obs:
        partes += ["<h2>Observaciones</h2>", f"<p>{e(obs)}</p>"]

    import fitz
    story = fitz.Story(html='<body>' + '\n'.join(partes) + '</body>', user_css=_CSS_PDF)
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    pagina = fitz.paper_rect('a4')
    area = pagina + (40, 40, -40, -40)
    mas = 1
    while mas:
        dev = writer.begin_page(pagina)
        mas, _ = story.place(area)
        story.draw(dev)
        writer.end_page()
    writer.close()
    buf.seek(0)
    nombre = f"analisis_{_norm(a['equipo']).replace(' ', '_')}_{a['fecha']}.pdf"
    return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=nombre)
