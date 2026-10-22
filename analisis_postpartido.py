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
    api_key = cfg.get('gemini_api_key') or cfg.get('GEMINI_API_KEY') or os.environ.get('GEMINI_API_KEY')
    if not api_key:
        raise RuntimeError("No hay clave de Gemini configurada")
    genai.configure(api_key=api_key)
    modelos = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    # Google retira modelos antiguos para claves nuevas: preferimos el alias "flash-lite-latest" (≈7 s; el Flash normal
    # tarda 60-100 s y el servidor corta antes) y si no existe, el Flash-Lite estable más nuevo
    if 'models/gemini-flash-lite-latest' in modelos:
        nombre = 'models/gemini-flash-lite-latest'
    else:
        estables = []
        for m in modelos:
            v = re.fullmatch(r'models/gemini-(\d+(?:\.\d+)?)-flash-lite', m)
            if v:
                estables.append((float(v.group(1)), m))
        nombre = max(estables)[1] if estables else next((m for m in modelos if 'flash' in m), 'models/gemini-flash-lite-latest')
    return genai, genai.GenerativeModel(model_name=nombre)


def _generar_ia(prompt):
    """Genera el texto; si Google responde que hay que usar otro modelo ("use models/xxx"), reintenta con ese."""
    genai, modelo = _modelo_gemini()
    try:
        return (modelo.generate_content(prompt, request_options={"timeout": 120}).text or '').strip()
    except Exception as e:
        sugerido = re.search(r'models/(gemini-[\w.\-]+)', str(e))
        if not sugerido or sugerido.group(0) == modelo.model_name:
            raise
        print(f"IA analisis_pp: {modelo.model_name} rechazado, reintentando con {sugerido.group(0)}")
        return (genai.GenerativeModel(sugerido.group(0)).generate_content(prompt, request_options={"timeout": 120}).text or '').strip()


_PROMPT_IA = """Eres un analista táctico de fútbol con Licencia UEFA Pro.
Un entrenador de cantera del club ha rellenado, tras el partido, un árbol de análisis táctico y un checklist de fallos por líneas.
Analiza SOLO lo que se ha marcado (no inventes datos que no aparezcan) y adapta el nivel a la categoría del equipo (fútbol formativo).

El entrenador lo va a leer en el MÓVIL en 1 minuto: escribe PUNTOS CLAVE, nada de párrafos.
Reglas estrictas:
- Cada punto: una sola frase de 15 palabras como máximo, con la idea clave en **negrita** al principio.
- Lenguaje técnico pero directo y accionable. Sin introducciones ni conclusiones.
- Usa EXACTAMENTE este formato Markdown (mismos títulos, mismos emojis), sin añadir nada más:

## 🔎 Diagnóstico colectivo
- **Causa raíz:** …
- **Consecuencia:** …
- **Clave del partido:** …
(entre 3 y 4 puntos)

## 📊 Línea por línea
- 🧤 **Portero** 🟢|🟡|🔴 — …
- 🛡️ **Defensa** 🟢|🟡|🔴 — …
- ⚙️ **Medios** 🟢|🟡|🔴 — …
- ⚡ **Atacantes** 🟢|🟡|🔴 — …
(pon UN solo semáforo por línea: 🟢 bien, 🟡 a mejorar, 🔴 problema; si no hay datos de una línea pon 🟡 y "Sin datos suficientes")

## 🎯 3 focos para esta semana
1. **Título corto** — por qué importa.
2. **Título corto** — por qué importa.
3. **Título corto** — por qué importa.

## 🏋️ Tareas de entrenamiento
### Tarea 1 · Nombre corto (día del microciclo)
- **Objetivo:** …
- **Organización:** espacio, jugadores y duración en una línea.
- **Consignas:** 2 o 3 consignas separadas por " · ".
(2 o 3 tareas en total, con el mismo formato)

Si al final aparecen "Datos objetivos de Veo" (estadísticas de la cámara del partido):
- Úsalos para respaldar cada punto con CIFRAS concretas (p. ej. "posesión 67% vs 33%", "13 remates dentro del área").
- Contrástalos con lo que marcó el entrenador (si coinciden o se contradicen, dilo).
- Añade justo después de "Diagnóstico colectivo" esta sección:
## 📈 Datos Veo clave
- **Dato:** cifra nuestra vs rival — lectura táctica breve.
(4 puntos, los más reveladores, mirando también las diferencias entre 1ª y 2ª parte)
- En "Línea por línea" puedes citar dorsales concretos si sus datos son relevantes.

Datos del partido y respuestas del entrenador:
"""


def _veo_id(datos):
    v = ((datos or {}).get('veo_id') or '').strip()
    return v if re.fullmatch(r'[0-9a-f-]{36}', v) else None


def _veo_resumen(datos):
    """Resumen del partido de Veo vinculado al análisis (None si no hay o falla la conexión)."""
    vid = _veo_id(datos)
    if not vid:
        return None
    try:
        import veo
        return veo.resumen_partido(vid) if veo.configurado() else None
    except Exception as e:
        print(f"Veo en analisis_pp: {e}")
        return None


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
    veo_r = _veo_resumen(datos)
    if veo_r:
        import veo
        texto += '\n\n' + veo.texto_para_ia(veo_r)
    try:
        informe = _generar_ia(_PROMPT_IA + texto)
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


def _html_para_pdf(h):
    """La fuente del PDF no tiene emojis: los semáforos pasan a texto de color y el resto se quitan."""
    h = (h.replace('🟢', '<b style="color:#16a34a;">[BIEN]</b>')
          .replace('🟡', '<b style="color:#d97706;">[A MEJORAR]</b>')
          .replace('🔴', '<b style="color:#dc2626;">[PROBLEMA]</b>')
          .replace('—', '-'))
    return re.sub(r'[\U0001F000-\U0001FAFF☀-➿️‍]', '', h)


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
table { border-collapse: collapse; width: 100%; margin: 2pt 0 6pt 0; font-size: 8.5pt; }
th { background-color: #1e293b; color: white; padding: 2pt 3pt; text-align: left; }
td { border-bottom: 1px solid #e2e8f0; padding: 2pt 3pt; }
td.n { text-align: center; font-weight: bold; }
td.p { color: #64748b; }
table.j { font-size: 7pt; }
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
        partes += ["<h2>Informe de análisis (IA)</h2>", _html_para_pdf(markdown_a_html(a['informe_ia']))]
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
    veo_r = _veo_resumen(a['datos'])
    if veo_r:
        partes.append(f"<h2>Estadísticas Veo · {e(veo_r['nuestro'])} {veo_r['marcador'][0]} - {veo_r['marcador'][1]} {e(veo_r['rival'])}</h2>")
        for s in veo_r['secciones']:
            filas = ''.join(
                f"<tr><td>{e(f['etiqueta'])}</td><td class='n'>{f['nuestro']}</td><td class='n'>{f['rival']}</td>"
                f"<td class='p'>{' / '.join(f'{x}-{y}' for x, y in f['partes'])}</td></tr>" for f in s['filas'])
            partes.append(f"<h4>{e(s['titulo'])}</h4><table><tr><th>Dato</th><th>Nosotros</th><th>Rival</th><th>1ª / 2ª parte</th></tr>{filas}</table>")
        if veo_r['jugadores']:
            # En tres tablas para que quepan en el ancho de la página
            grupos = [('Por jugador · Físico', ('seconds_played_total', 'distance_total_meters', 'top_speed_kmh',
                                                'average_speed_kmh', 'sprints_total', 'high_intensity_runs_total')),
                      ('Por jugador · Con balón', ('football_goal_total', 'football_attempts_total', 'football_passes_total',
                                                   'football_passes_completed_total', 'football_dribble_total', 'football_loose_total',
                                                   'football_corner_total', 'football_free_kick_total', 'football_throw_in_total')),
                      ('Por jugador · Sin balón', ('football_duel_total', 'football_tackle_total', 'football_interception_total',
                                                   'football_foul_total', 'football_goal_kick_total', 'football_save_total'))]
            etiquetas = dict(veo_r['columnas_jugador'])
            for titulo_g, tipos in grupos:
                cols = [t for t in tipos if any(j.get(t) for j in veo_r['jugadores'])]
                if not cols:
                    continue
                cab_j = '<th>Dorsal</th>' + ''.join(f"<th>{e(etiquetas.get(t, t))}</th>" for t in cols)
                filas_j = ''.join('<tr><td>' + e(str(j['dorsal'])) + '</td>' +
                                  ''.join(f"<td class='n'>{j.get(t) if j.get(t) is not None else ''}</td>" for t in cols) + '</tr>'
                                  for j in veo_r['jugadores'])
                partes.append(f"<h4>{titulo_g}</h4><table class='j'><tr>{cab_j}</tr>{filas_j}</table>")

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
