"""Previa del rival por WhatsApp (lunes 22:00) a cada entrenador, por cada equipo con partido el finde siguiente.

Clasificación (nuestra y del rival, con goles por partido), resultado de la ida, 2 últimos partidos del rival,
hasta 2 rivales en común (sus resultados y los nuestros), máximo goleador del rival y lo destacable (más/menos
goleador o goleado del grupo, rachas de 3 o más). Sin Aficionados ni Debutantes; si descansa, no se envía.
Los datos salen de la caché de la Federación (rffm_cache.json), que se actualiza el lunes a las 21:30.
"""
import os
import re
import json
import unicodedata
from datetime import datetime, date, timedelta
from flask import Blueprint, request, jsonify

previa_bp = Blueprint('previa_rival', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(BASE_DIR, 'datos_privados', 'previa_rival.json')
HORA_ENVIO = (22, 0)
HORA_SYNC = (21, 30)
_DIAS = ['lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom']


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


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.replace('"', ' ').replace("'", ' ').upper().split())


def _bonito(eq):
    return (' '.join(eq.upper().split()).replace('PREBENJAMIN', 'PREBENJAMÍN')
            .replace('BENJAMIN', 'BENJAMÍN').replace('ALEVIN', 'ALEVÍN'))


def _num(x):
    try:
        return int(str(x).strip())
    except Exception:
        return 0


def _por_partido(goles, jugados):
    return f"{goles / jugados:.1f}".replace('.', ',') if jugados else '0'


def _nombre_jugador(txt):
    """'MUÑOZ GONZALEZ, JAVIER' → 'Javier Muñoz Gonzalez'."""
    if ',' in txt:
        ape, nom = [x.strip() for x in txt.split(',', 1)]
        txt = f'{nom} {ape}'
    return ' '.join(w.capitalize() for w in txt.split())


def _jornada_num(j):
    m = re.search(r'\d+', str(j or ''))
    return int(m.group()) if m else 0


def _resultados(calendario_grupo, equipo_norm, hasta_jornada):
    """[(jornada, rival, gf, gc, 'G'/'E'/'P')] del equipo antes de esa jornada, del más antiguo al más reciente."""
    out = []
    for p in calendario_grupo or []:
        num = _jornada_num(p.get('jornada'))
        if num >= hasta_jornada:
            continue
        loc, vis = _norm(p.get('local')), _norm(p.get('visitante'))
        if equipo_norm not in (loc, vis):
            continue
        m = re.search(r'(\d+)\s*-\s*(\d+)', p.get('resultado') or '')
        if not m:
            continue
        gl, gv = int(m.group(1)), int(m.group(2))
        es_local = loc == equipo_norm
        gf, gc = (gl, gv) if es_local else (gv, gl)
        out.append((num, p.get('visitante') if es_local else p.get('local'), gf, gc, 'G' if gf > gc else ('E' if gf == gc else 'P')))
    return sorted(out, key=lambda x: x[0])


def _nombre_en_grupo(calendario_grupo, nombre):
    """Nombre normalizado tal y como aparece en el calendario del grupo (comparación flexible)."""
    n = _norm(nombre)
    nombres = {_norm(p.get(k)) for p in calendario_grupo or [] for k in ('local', 'visitante')}
    if n in nombres:
        return n
    return next((x for x in nombres if x and (n in x or x in n)), n)


def _claves_equipo(p):
    """Nombres posibles en la configuración para un partido de OBJ FINES DE SEMANA ('ALEVIN F7' 'A' → 'ALEVIN A F7')."""
    cat = _norm(p.get('categoria'))
    letra = _norm(p.get('letra'))
    fem = 'FEM' in cat.split()
    fmt = _norm(p.get('formato')) or ('F7' if 'F7' in cat.split() else '')
    base = ' '.join(w for w in cat.split() if w not in ('F7', 'F11', 'FEM', 'FEMENINO'))
    if fem:
        return {f'{base} FEMENINO', f'{base} FEMENINO {letra}'.strip()}, fmt
    return {f'{base} {letra}'.strip(), f'{base} {letra} {fmt}'.strip()}, fmt


def _equipo_config(p, equipos):
    claves, fmt = _claves_equipo(p)
    fmt_de = lambda n: 'F11' if 'F11' in n.split() else ('F7' if 'F7' in n.split() else '')
    for e in equipos:
        if _norm(e) in claves:
            return e
    for e in equipos:
        n = _norm(e)
        if any(n.startswith(k) or k.startswith(n) for k in claves) and not (fmt and fmt_de(n) and fmt != fmt_de(n)):
            return e
    return None


def _destacable(clasif, rival_norm, resultados_rival):
    """Lo que de verdad destaca del rival: récords del grupo (por partido) y rachas de 3 o más."""
    out = []
    filas = [f for f in clasif.values() if f.get('jugados')]
    fila = next((f for f in filas if _norm(f['equipo']) == rival_norm), None)
    if fila and len(filas) >= 3:
        pp = lambda f, k: f[k] / f['jugados']
        def es(k, fn):
            v = pp(fila, k)
            return v == fn(pp(f, k) for f in filas) and sum(1 for f in filas if pp(f, k) == v) == 1
        if es('goles_favor', max):
            out.append(f"Equipo más goleador del grupo ({_por_partido(fila['goles_favor'], fila['jugados'])} goles por partido)")
        elif es('goles_favor', min):
            out.append(f"Equipo menos goleador del grupo ({_por_partido(fila['goles_favor'], fila['jugados'])} goles por partido)")
        if es('goles_contra', min):
            out.append(f"Equipo menos goleado del grupo ({_por_partido(fila['goles_contra'], fila['jugados'])} goles en contra por partido)")
        elif es('goles_contra', max):
            out.append(f"Equipo más goleado del grupo ({_por_partido(fila['goles_contra'], fila['jugados'])} goles en contra por partido)")
    estados = [r[4] for r in resultados_rival]
    def racha(cond):
        n = 0
        for e in reversed(estados):
            if not cond(e):
                break
            n += 1
        return n
    v, d, sp, sg = racha(lambda e: e == 'G'), racha(lambda e: e == 'P'), racha(lambda e: e != 'P'), racha(lambda e: e != 'G')
    if v >= 3:
        out.append(f'Lleva {v} victorias seguidas')
    elif sp >= 3:
        out.append(f'Lleva {sp} partidos sin perder')
    if d >= 3:
        out.append(f'Lleva {d} derrotas seguidas')
    elif sg >= 3:
        out.append(f'Lleva {sg} partidos sin ganar')
    return out


def _hora_partido(p, horas_fed):
    claves, _ = _claves_equipo(p)
    return next((h for (ks, f, h) in horas_fed if f == p['_fecha'] and ks & claves and h), '')


def calcular(vie=None):
    """Previas del finde (viernes `vie`; por defecto el siguiente). [{usuario, telefono, equipo, texto}] y avisos."""
    from competicion_scraper import construir_obj_semanales, load_cached_data, calcular_clasificacion_en_jornada
    from deportivo import _get_equipos_config_path
    from formulario_finde import _entrenadores
    hoy = date.today()
    vie = vie or hoy + timedelta(days=(4 - hoy.weekday()) % 7 or 7)
    dias = {vie + timedelta(days=i) for i in range(3)}
    try:
        with open(_get_equipos_config_path(), encoding='utf-8') as f:
            equipos = [e['nombre'] for e in json.load(f).get('equipos', []) if e.get('nombre')]
    except Exception:
        equipos = []
    equipos = [e for e in equipos if not _norm(e).startswith(('AFICIONADO', 'DEBUTANTE'))]
    grupos = {g.get('nombre'): g for g in (load_cached_data() or {}).get('equipos', [])}
    horas_fed = _horas_federacion(vie)

    previas, avisos, sin_goles, sin_goleador_rival, clasifs = {}, [], [], [], {}
    for p in construir_obj_semanales():
        try:
            dd, mm, yy = [int(x) for x in (p.get('fecha') or '').split('-')]
            p['_fecha'] = date(yy, mm, dd)
        except Exception:
            continue
        if p['_fecha'] not in dias or not p.get('rival') or 'DESCANSA' in _norm(p.get('rival')):
            continue
        eq = _equipo_config(p, equipos)
        if not eq or eq in previas:
            continue
        grupo = grupos.get(p.get('grupo')) or {}
        previas[eq] = texto_previa(p, eq, grupo, _hora_partido(p, horas_fed), calcular_clasificacion_en_jornada)
        clasifs[eq] = {'grupo': p.get('grupo'), 'equipo': eq, 'rival': p.get('rival', ''), 'letra': p.get('letra', '')}
        if not grupo.get('goleadores'):
            sin_goles.append(eq)
        elif '⚽' not in previas[eq]:
            sin_goleador_rival.append(f"{eq} ({p.get('rival')})")
    if sin_goles:
        avisos.append('Sin goleadores descargados de la Federación (sincroniza RFFM; en categorías pequeñas puede que no haya): '
                      + ', '.join(sin_goles))
    if sin_goleador_rival:
        avisos.append('El rival no tiene ningún goleador en la lista de la Federación: ' + ', '.join(sin_goleador_rival))

    mensajes = []
    for usuario, telefono, eq in _entrenadores(previas):
        nombre = usuario.split()[0].capitalize()
        if not telefono:
            avisos.append(f'{usuario} ({eq}): sin teléfono en su usuario')
            continue
        mensajes.append({'usuario': usuario, 'telefono': telefono, 'equipo': eq, 'clasificacion': clasifs.get(eq),
                         'texto': f'Hola {nombre}, previa de la jornada:\n' + previas[eq]})
    sin = sorted(set(previas) - {m['equipo'] for m in mensajes})
    if sin:
        avisos.append('Equipos con partido sin entrenador con teléfono: ' + ', '.join(sin))
    return {'viernes': vie.isoformat(), 'mensajes': mensajes, 'avisos': avisos}


def _horas_federacion(vie):
    """[(claves, fecha, hora)] del listado de la Federación (para poner la hora del partido)."""
    out = []
    try:
        import competicion_scraper as cs
        from horarios import _rffm_session
        from formulario_finde import _claves_federacion
        cod, _, _ = cs._temporada_actual()
        for m in cs.fetch_temporada_matches(_rffm_session(), cod_temporada=cod, fecha_desde=vie.strftime('%d-%m-%Y'),
                                            fecha_hasta=(vie + timedelta(days=2)).strftime('%d-%m-%Y')):
            for nuestro, rival in ((m['nombre_casa'], m['nombre_fuera']), (m['nombre_fuera'], m['nombre_casa'])):
                if 'FUENTELARREYNA' not in nuestro.upper():
                    continue
                claves, _ = _claves_federacion({'competicion': m.get('competicion'), 'deporte': m.get('deporte'), 'nuestro': nuestro})
                dd, mm, yy = [int(x) for x in m['fecha'].replace('/', '-').split('-')]
                hora = (m.get('hora') or '').strip()[:5]
                out.append(({_norm(k) for k in claves}, date(yy, mm, dd), hora if hora not in ('00:00', '0:00') else ''))
    except Exception as e:
        print(f"[PREVIA] horas de la Federación: {e}", flush=True)
    return out


def _nombres_grupo(cal, rival, letra):
    """(rival, nuestro equipo) tal y como se llaman en el calendario del grupo (normalizados)."""
    rival_n = _nombre_en_grupo(cal, rival)
    nombres = [_norm(x.get(k)) for x in cal for k in ('local', 'visitante')]
    nuestro_n = next((n for n in nombres if 'FUENTELARREYNA' in n and (not letra or n.endswith(' ' + _norm(letra)))), None) \
        or next((n for n in nombres if 'FUENTELARREYNA' in n), '')
    return rival_n, nuestro_n


# ---------- imagen de la clasificación (como la de la Federación) ----------

_RES_COL = {'G': '#16a34a', 'E': '#d97706', 'P': '#dc2626'}


def html_clasificacion(grupo, eq, rival, letra):
    """Resultados de la última jornada + clasificación del grupo. Nuestro equipo en verde y el próximo rival en amarillo."""
    from html import escape
    cal = grupo.get('calendario_grupo') or []
    rival_n, nuestro_n = _nombres_grupo(cal, rival, letra)
    fondo = lambda nombre: ('background-color:#dcfce7;' if _norm(nombre) == nuestro_n else
                            'background-color:#fef9c3;' if _norm(nombre) == rival_n else '')
    jugadas = [x for x in cal if re.search(r'\d+\s*-\s*\d+', x.get('resultado') or '')]
    h = [f'<table style="width:100%;font-family:sans-serif;"><tr><td style="font-size:14px;font-weight:bold;color:#E31E24;">'
         f'{escape(_bonito(eq))} — {escape(str(grupo.get("nombre", "")))}</td>'
         f'<td style="text-align:right;font-size:9px;color:#64748b;">Temporada {_temporada()}</td></tr></table>']
    if jugadas:
        jn = max(_jornada_num(x.get('jornada')) for x in jugadas)
        ult = [x for x in cal if _jornada_num(x.get('jornada')) == jn]
        fecha = next((str(x.get('fecha')) for x in ult if x.get('fecha')), '')
        h.append(f'<p style="font-family:sans-serif;font-size:10px;font-weight:bold;text-align:center;margin:8px 0 4px 0;">'
                 f'Jornada {jn}{" (" + escape(fecha) + ")" if fecha else ""}</p>')
        filas = ''.join(
            f'<tr style="{fondo(x.get("local")) or fondo(x.get("visitante"))}">'
            f'<td style="padding:3px 6px;font-size:8.5px;text-align:right;width:44%;">{escape(str(x.get("local", "")))}</td>'
            f'<td style="padding:3px 6px;font-size:10px;font-weight:bold;text-align:center;width:12%;">{escape(str(x.get("resultado") or "-"))}</td>'
            f'<td style="padding:3px 6px;font-size:8.5px;width:44%;">{escape(str(x.get("visitante", "")))}</td></tr>' for x in ult)
        h.append(f'<table style="width:100%;border-collapse:collapse;font-family:sans-serif;">{filas}</table>')
    th = 'padding:4px 5px;font-size:8.5px;font-weight:bold;color:#64748b;background-color:#f8fafc;border-bottom:2px solid #e2e8f0;'
    td = 'padding:4px 5px;font-size:9px;border-bottom:1px solid #e2e8f0;'
    filas = ''
    for f in grupo.get('clasificacion') or []:
        ult = ''.join(f'<b style="color:{_RES_COL.get(u, "#94a3b8")};">{escape(u)}</b> ' for u in (f.get('ultimos') or [])[:5])
        filas += (f'<tr style="{fondo(f.get("equipo"))}"><td style="{td}color:#64748b;">{escape(str(f.get("pos", "")))}</td>'
                  f'<td style="{td}font-weight:bold;">{escape(str(f.get("equipo", "")))}</td>'
                  f'<td style="{td}text-align:center;font-weight:bold;color:#E31E24;">{escape(str(f.get("puntos", "")))}</td>'
                  + ''.join(f'<td style="{td}text-align:center;">{escape(str(f.get(k, "")))}</td>'
                            for k in ('jugados', 'ganados', 'empatados', 'perdidos', 'goles_favor', 'goles_contra'))
                  + f'<td style="{td}text-align:center;">{ult}</td></tr>')
    h.append(f'<table style="width:100%;border-collapse:collapse;font-family:sans-serif;margin-top:10px;"><tr>'
             f'<th style="{th}width:20px;"></th><th style="{th}text-align:left;">EQUIPOS</th>'
             + ''.join(f'<th style="{th}text-align:center;width:28px;">{x}</th>' for x in ('PTS', 'J', 'G', 'E', 'P', 'F', 'C'))
             + f'<th style="{th}text-align:center;width:80px;">ULT.</th></tr>{filas}</table>')
    return ''.join(h)


def _temporada():
    hoy = date.today()
    ini = hoy.year if hoy.month >= 8 else hoy.year - 1
    return f'{ini}-{ini + 1}'


def imagen_clasificacion(info):
    """PNG de la clasificación para una previa (info = {'grupo', 'equipo', 'rival', 'letra'}), o None."""
    from competicion_scraper import load_cached_data
    from objetivos_finde import html_a_png
    grupo = next((g for g in (load_cached_data() or {}).get('equipos', []) if g.get('nombre') == info.get('grupo')), None)
    if not grupo or not grupo.get('clasificacion'):
        return None
    return html_a_png(html_clasificacion(grupo, info['equipo'], info['rival'], info.get('letra')),
                      filas=8 + len(grupo['clasificacion']) // 2, ancho=760)


def texto_previa(p, eq, grupo, hora, clasif_en_jornada):
    """Cuerpo del mensaje (sin el saludo) para un partido."""
    cal = grupo.get('calendario_grupo') or []
    jn = _jornada_num(p.get('jornada'))
    rival = p.get('rival', '')
    rival_n, nuestro_n = _nombres_grupo(cal, rival, p.get('letra'))
    d = p['_fecha']
    cab = f"*{_bonito(eq)}* · J.{jn} · {_DIAS[d.weekday()]} {d:%d/%m}{' ' + hora if hora else ''} · {'Casa' if p.get('es_local') else 'Fuera'}"
    t = [cab, f'🆚 *{rival}*']

    dn, dr = p.get('datos_nuestro'), p.get('datos_rival')
    if dn and dr:
        linea = lambda et, x: (f"{et}: {x.get('posicion')}º · {x.get('puntos')} pts · goles a favor {x.get('goles_favor')} "
                               f"({_por_partido(_num(x.get('goles_favor')), _num(x.get('jugados')))} por partido) · goles en contra "
                               f"{x.get('goles_contra')} ({_por_partido(_num(x.get('goles_contra')), _num(x.get('jugados')))} por partido)")
        t += ['', '📊 Clasificación', linea('FR', dn), linea('Rival', dr)]

    res_rival = _resultados(cal, rival_n, jn)
    res_nuestros = _resultados(cal, nuestro_n, jn) if nuestro_n else []
    ida = [r for r in res_nuestros if _norm(r[1]) == rival_n]
    if ida:  # resultado desde nuestro lado (goles nuestros primero)
        _, _, gf, gc, e = ida[-1]
        t += ['', f"🔙 Ida: {({'G': 'ganamos', 'E': 'empatamos', 'P': 'perdimos'})[e]} {gf}-{gc}"]
    if res_rival:
        t += ['', '📉 Últimos partidos del rival'] + [f'• {e} {gf}-{gc} vs {r}' for _, r, gf, gc, e in res_rival[-2:][::-1]]

    if nuestro_n:
        nuestros = {_norm(r): (gf, gc, e, j) for j, r, gf, gc, e in res_nuestros}
        comunes = []
        for j, r, gf, gc, e in reversed(res_rival):
            k = _norm(r)
            if k in nuestros and k not in (nuestro_n, rival_n) and k not in [c[0] for c in comunes]:
                comunes.append((k, r, (gf, gc, e), nuestros[k]))
        if comunes:
            t += ['', '🤝 Rivales en común'] + [f'• {r}: ellos {e1} {a1}-{b1} · nosotros {e2} {a2}-{b2}'
                                                for _, r, (a1, b1, e1), (a2, b2, e2, _) in comunes[:2]]

    goleadores = [g for g in grupo.get('goleadores') or [] if _norm(g.get('equipo')) == rival_n
                  or (_norm(g.get('equipo')) and (_norm(g.get('equipo')) in rival_n or rival_n in _norm(g.get('equipo'))))]
    if goleadores:
        maxg = max(g['goles'] for g in goleadores)
        tops = [g for g in goleadores if g['goles'] == maxg]
        t += ['', '⚽ Máximo goleador: ' + ' y '.join(
            f"{_nombre_jugador(g['jugador'])} ({g['goles']} goles{' en ' + str(g['partidos']) + ' partidos' if g.get('partidos') else ''})"
            for g in tops)]

    dest = _destacable(clasif_en_jornada(cal, jn - 1) if cal else {}, rival_n, res_rival)
    if dest:
        t += ['', '⭐ Destacable'] + [f'• {x}' for x in dest]
    return '\n'.join(t) + '\n\nDirección Deportiva'


def enviar_todas(vie=None, solo_telefono=None, destino=None):
    """Envía las previas. Con solo_telefono, solo las de ese entrenador; con destino, a ese teléfono (prueba)."""
    from app import enviar_whatsapp
    datos = calcular(vie)
    ok = 0
    mensajes = [m for m in datos['mensajes'] if not solo_telefono
                or ''.join(c for c in m['telefono'] if c.isdigit()).endswith(''.join(c for c in solo_telefono if c.isdigit())[-9:])]
    from objetivos_finde import enviar_imagen
    imagenes = {}
    for m in mensajes:
        tel = ''.join(c for c in (destino or m['telefono']) if c.isdigit())
        tel = '34' + tel if len(tel) == 9 else tel
        info = m.get('clasificacion') or {}
        clave = (info.get('grupo'), info.get('equipo'))
        if clave not in imagenes:
            try:
                imagenes[clave] = imagen_clasificacion(info) if info else None
            except Exception as e:
                print(f"[PREVIA] imagen clasificación {m['equipo']}: {e}", flush=True)
                imagenes[clave] = None
        if imagenes[clave]:
            enviar_imagen(f'{tel}@c.us', imagenes[clave], '')
        bien, detalle = enviar_whatsapp(tel, m['texto'])
        ok += bool(bien)
        if not bien:
            print(f"[PREVIA] No enviado a {m['usuario']}: {detalle}", flush=True)
    print(f"[PREVIA] Enviadas {ok} de {len(mensajes)}", flush=True)
    return ok, len(mensajes)


def tick(ahora):
    """Lunes: 21:30 actualiza los datos de la Federación; 22:00 envía las previas (una vez)."""
    if ahora.weekday() != 0:
        return
    cfg = cargar_cfg()
    if not cfg.get('activo'):
        return
    hoy = ahora.date().isoformat()
    sync = ahora.replace(hour=HORA_SYNC[0], minute=HORA_SYNC[1], second=0, microsecond=0)
    envio = ahora.replace(hour=HORA_ENVIO[0], minute=HORA_ENVIO[1], second=0, microsecond=0)
    if cfg.get('sync') != hoy and sync <= ahora < envio:
        cfg['sync'] = hoy
        guardar_cfg(cfg)
        try:
            import competicion_scraper as cs
            from sincronizar_rffm import credenciales
            usuario, password = credenciales()
            ok, msg = cs.sync_rffm(usuario, password)
            print(f"[PREVIA] Actualización de la Federación: {'OK' if ok else msg}", flush=True)
        except Exception as e:
            print(f"[PREVIA] Actualización de la Federación: {e}", flush=True)
    if cfg.get('ultimo') != hoy and envio <= ahora < envio + timedelta(minutes=30):
        cfg['ultimo'] = hoy
        guardar_cfg(cfg)
        enviar_todas()


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@previa_bp.route('/api/whatsapp/previa', methods=['GET', 'POST'])
def api_cfg():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    cfg = cargar_cfg()
    if request.method == 'POST':
        cfg['activo'] = bool((request.json or {}).get('activo'))
        guardar_cfg(cfg)
    return jsonify({'status': 'ok', 'activo': cfg['activo'], 'ultimo': cfg.get('ultimo', '')})


@previa_bp.route('/api/whatsapp/previa/vista')
def api_vista():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        return jsonify(dict(status='ok', **calcular()))
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]})


@previa_bp.route('/api/whatsapp/previa/clasificacion')
def api_clasificacion():
    """Imagen de la clasificación que irá con la previa de un equipo (no envía nada)."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    from flask import Response
    try:
        equipo = _norm(request.args.get('equipo') or '')
        m = next((m for m in calcular()['mensajes'] if _norm(m['equipo']) == equipo), None)
        png = imagen_clasificacion(m['clasificacion']) if m and m.get('clasificacion') else None
        if not png:
            return jsonify({'status': 'error', 'message': 'Ese equipo no tiene previa este finde (o no hay clasificación)'}), 404
        return Response(png, mimetype='image/png', headers={'Cache-Control': 'no-store'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error en el servidor: {e}'[:250]}), 500
