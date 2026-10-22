"""
CONSULTOR IA (preguntas libres sobre un partido grabado con Veo)
- La IA (Gemini) NO se inventa datos: responde usando "herramientas" que consultan todas las acciones
  del partido registradas por Veo (pases con pasador, receptor, origen y destino; saques; duelos...).
- Las herramientas preparan los DIBUJOS con coordenadas reales; la IA solo decide cuáles mostrar
  escribiendo [[DIBUJO:Dn]] en su respuesta.
- Plantilla de preguntas: sugeridas (élite) + las propias de cada usuario (datos_privados/).
Sistema de coordenadas de los dibujos: campo 0..1, NOSOTROS atacando hacia la derecha (el rival hacia la izquierda).
"""
import os
import re
import json
import threading
from collections import Counter, defaultdict

from flask import Blueprint, jsonify, request, session

consultor_bp = Blueprint('consultor_bp', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLANTILLAS_FILE = os.path.join(BASE_DIR, 'datos_privados', 'consultor_preguntas.json')
_lock_plantillas = threading.Lock()

PREGUNTAS_ELITE = [
    {"cat": "Salida de balón", "texto": "Dibújame la secuencia de pases que más ha repetido el rival en su salida de balón"},
    {"cat": "Salida de balón", "texto": "¿Qué jugador del rival toca más veces el balón en su salida desde campo propio y hacia quién la orienta?"},
    {"cat": "Salida de balón", "texto": "¿Por qué carril inicia más el rival su salida de balón y con qué porcentaje de éxito progresa al medio campo?"},
    {"cat": "Salida de balón", "texto": "Analiza nuestra salida de balón: cadenas más repetidas, jugadores clave y dónde perdemos el balón"},
    {"cat": "Saques de puerta", "texto": "Analízame los patrones de los saques de puerta del rival: corto o largo, receptores y secuencia más repetida (dibújala)"},
    {"cat": "Saques de puerta", "texto": "¿Qué ocurre después de nuestros saques de puerta? ¿Conseguimos progresar o perdemos el balón y dónde?"},
    {"cat": "Presión", "texto": "¿En qué zonas recuperamos el balón y qué jugadores participan más en las recuperaciones?"},
    {"cat": "Presión", "texto": "¿Dónde y quién pierde más balones de nuestro equipo en campo propio? Dibuja las pérdidas"},
    {"cat": "Ataque", "texto": "¿Cuál es nuestra secuencia de pases más repetida que termina en el último tercio? Dibújala"},
    {"cat": "Ataque", "texto": "¿Desde qué zonas y después de qué acciones generamos nuestros tiros? Dibuja el mapa de tiros con los minutos"},
    {"cat": "Ataque", "texto": "¿Qué conexiones entre jugadores fueron más productivas para progresar? Dame las 5 más repetidas"},
    {"cat": "Rival", "texto": "¿Qué jugadores del rival son los más influyentes en la circulación y por qué zona juegan?"},
    {"cat": "Rival", "texto": "¿Cómo atacó el rival: por bandas o por dentro? ¿Desde dónde nos tiraron?"},
    {"cat": "ABP", "texto": "Analiza nuestros córners y saques de banda: qué secuencias hacemos después y cómo terminan"},
    {"cat": "ABP", "texto": "¿Qué hace el rival después de sus saques de banda en campo propio? Secuencia más repetida y dibujo"},
    {"cat": "Comparativa", "texto": "Compara la 1ª y la 2ª parte: posesión por zonas, pases, pérdidas y recuperaciones de los dos equipos"},
    {"cat": "Jugadores", "texto": "¿Qué jugadores de nuestro equipo participaron más en la circulación y cuál fue su acierto en el pase?"},
]

_REANUDACIONES = {'saque_puerta': 'FootballGoalKick', 'saque_banda': 'FootballThrowIn', 'corner': 'FootballCornerKick',
                  'falta': 'FootballFreeKick', 'saque_centro': 'FootballKickOff'}
_CORTE = {'pass', 'possession', 'interception', 'tackle', 'dribble', 'FootballShot'}  # acciones con balón de un equipo


# ───────────────────────── preparación de los datos ─────────────────────────

class Partido:
    """Acciones del partido ya giradas (nosotros atacando a la derecha) y con minutos de juego."""

    def __init__(self, veo_id):
        import veo
        self.veo_id = veo_id
        rep = veo.informe(veo_id)
        self.resumen = veo.resumen_partido(veo_id)
        self.periodos = rep.get('periods') or []
        self.lado = {i + 1: (p.get('own_side') or 'left') for i, p in enumerate(self.periodos)}
        self.nombres = {'own': self.resumen.get('nuestro') or 'Nosotros', 'opp': self.resumen.get('rival') or 'Rival'}
        self.dibujos = {}
        self.clips = {}
        videos = (rep.get('match') or {}).get('videos') or []
        self.video_url = next((v['url'] for v in videos if v.get('render_type') == 'standard'), videos[0]['url'] if videos else None)
        crudos = sorted(veo.eventos(veo_id), key=lambda e: e.get('videoTimeMs') or 0)
        self.ev = []
        for e in crudos:
            eq = 'own' if e.get('team') == 'Own' else ('opp' if e.get('team') == 'Opponent' else None)
            if not eq:
                continue
            at = {a.get('label'): a.get('value') for a in (e.get('attributes') or [])}
            x, y = self._pos(e.get('x'), e.get('z'), e.get('periodId'))
            rx, ry = self._pos(at.get('receiver_x'), at.get('receiver_z'), e.get('periodId'))
            self.ev.append({
                'eq': eq, 'tipo': e.get('eventType'), 'dorsal': str(e.get('playerJersey') or '?'),
                'receptor': str(at['receiver_jersey']) if at.get('receiver_jersey') not in (None, -1) else None,
                'x': x, 'y': y, 'rx': rx, 'ry': ry, 'ok': e.get('outcome') == 1 if e.get('outcome') is not None else None,
                'dist': round(at['distance'], 1) if isinstance(at.get('distance'), (int, float)) else None,
                'parte': e.get('periodId'), 'ms': e.get('videoTimeMs') or 0,
                'min': veo._minuto(e.get('videoTimeMs'), self.periodos),
            })

    def _pos(self, x, z, periodo):
        if x is None or z is None:
            return None, None
        if self.lado.get(periodo) == 'right':  # nuestra portería a la derecha: se gira el campo 180º
            x, z = 1 - x, 1 - z
        return round(min(1, max(0, x)), 4), round(min(1, max(0, 1 - z)), 4)

    # Zonas vistas desde el equipo que hace la acción
    @staticmethod
    def rel(eq, x, y):
        if x is None:
            return None, None
        return (x, y) if eq == 'own' else (1 - x, 1 - y)

    def zona(self, eq, x, y):
        xr, yr = self.rel(eq, x, y)
        if xr is None:
            return 'sin ubicar'
        t = ['campo propio', 'zona media', 'campo rival'][min(2, int(xr * 3))]
        c = ['carril izquierdo', 'carril central', 'carril derecho'][min(2, int(yr * 3))]
        return f"{t}, {c}"

    def filtra(self, equipo='', parte='todo'):
        eqs = {'nosotros': 'own', 'propio': 'own', 'own': 'own', 'rival': 'opp', 'opp': 'opp'}
        eq = eqs.get((equipo or '').strip().lower())
        p = str(parte or 'todo').lower()
        num = 1 if p.startswith('1') or 'primera' in p else (2 if p.startswith('2') or 'segunda' in p else None)
        return [e for e in self.ev if (not eq or e['eq'] == eq) and (not num or e['parte'] == num)]

    def nuevo_clip(self, titulo, ms_ini, ms_fin, minuto):
        """Trozo de vídeo de una jugada: 3 s antes de empezar hasta 3 s después de terminar."""
        if not self.video_url:
            return None
        k = f"C{len(self.clips) + 1}"
        ini = max(0, ms_ini / 1000 - 3)
        fin = max(ini + 6, ms_fin / 1000 + 3)
        self.clips[k] = {'id': k, 'titulo': titulo, 'inicio': round(ini, 1), 'fin': round(min(fin, ini + 120), 1), 'min': minuto}
        return k

    def nuevo_dibujo(self, titulo, flechas=None, puntos=None, nota=''):
        k = f"D{len(self.dibujos) + 1}"
        self.dibujos[k] = {'id': k, 'titulo': titulo, 'flechas': flechas or [], 'puntos': puntos or [], 'nota': nota}
        return k


def _flecha(p, n=None):
    return {'x1': p['x'], 'y1': p['y'], 'x2': p['rx'] if p['rx'] is not None else p['x'], 'y2': p['ry'] if p['ry'] is not None else p['y'],
            'de': p['dorsal'], 'a': p['receptor'] or '?', 'ok': p['ok'], 'n': n, 'min': p['min'], 'eq': p['eq']}


def _secuencia_desde(P, idx, eq, max_s=40):
    """Pases seguidos del equipo eq desde el índice idx hasta que el rival toca el balón, sale fuera o hay falta."""
    ini = P.ev[idx]['ms']
    pases, fin = [], 'se corta la jugada'
    P._fin_ms = ini + max_s * 1000
    for e in P.ev[idx + 1:]:
        P._fin_ms = e['ms']
        if e['ms'] - ini > max_s * 1000:
            fin = 'sigue la posesión'; break
        if e['tipo'] in ('FootballOutOfPlay',):
            fin = 'balón fuera'; break
        if e['tipo'] in ('FootballFoul',):
            fin = 'falta'; break
        if e['tipo'] in ('FootballGoal',) and e['eq'] == eq:
            fin = 'GOL'; break
        if e['eq'] != eq and e['tipo'] in _CORTE:
            fin = f"pérdida ({P.zona(eq, e['x'], e['y'])})" if e['x'] is not None else 'pérdida'; break
        if e['eq'] == eq and e['tipo'] == 'FootballShot':
            fin = 'tiro'; break
        if e['eq'] == eq and e['tipo'] == 'pass':
            pases.append(e)
    return pases, fin


def _cadenas(pases_equipo):
    """Agrupa pases completados encadenados (el receptor es el siguiente pasador)."""
    cadenas, actual = [], []
    for p in pases_equipo:
        if actual and (actual[-1]['ok'] and actual[-1]['receptor'] == p['dorsal'] and p['ms'] - actual[-1]['ms'] < 15000):
            actual.append(p)
        else:
            if actual:
                cadenas.append(actual)
            actual = [p]
        if not p['ok']:
            cadenas.append(actual); actual = []
    if actual:
        cadenas.append(actual)
    return cadenas


def _dibujo_media(P, titulo, ocurrencias, nota=''):
    """Dibujo de una secuencia repetida: posición media de cada pase en todas sus repeticiones."""
    n = len(ocurrencias[0])
    flechas = []
    for i in range(n):
        ps = [o[i] for o in ocurrencias]
        med = lambda k: round(sum(p[k] for p in ps if p[k] is not None) / max(1, sum(1 for p in ps if p[k] is not None)), 4)
        f = _flecha(ps[0], i + 1)
        f.update({'x1': med('x'), 'y1': med('y'), 'x2': med('rx') if any(p['rx'] is not None for p in ps) else med('x'),
                  'y2': med('ry') if any(p['ry'] is not None for p in ps) else med('y'), 'min': None})
        flechas.append(f)
    return P.nuevo_dibujo(titulo, flechas=flechas, nota=nota)


# ───────────────────────── herramientas para la IA ─────────────────────────

def _herramientas(P):
    def estadisticas_partido() -> dict:
        """Marcador, goles y todas las estadísticas de equipo del Informe de Veo (nosotros vs rival, por partes)."""
        r = P.resumen
        return {'nosotros': P.nombres['own'], 'rival': P.nombres['opp'], 'marcador': r.get('marcador'),
                'goles': r.get('goleadores'), 'secciones': [{'titulo': s['titulo'], 'datos': [
                    {'dato': f['etiqueta'], 'nosotros': f['nuestro'], 'rival': f['rival'], 'por_partes': f['partes']} for f in s['filas']]}
                    for s in r.get('secciones', [])]}

    def secuencias_tras_reanudacion(equipo: str, tipo: str, parte: str = "todo") -> dict:
        """Analiza lo que hace un equipo justo después de una reanudación: primer pase (corto/largo, receptor, carril),
        secuencias de pases, cómo terminan y la secuencia más repetida (con dibujos).
        equipo: 'nosotros' o 'rival'. tipo: 'saque_puerta', 'saque_banda', 'corner', 'falta' o 'saque_centro'.
        parte: 'todo', '1' o '2'."""
        tipo_veo = _REANUDACIONES.get((tipo or '').strip().lower())
        if not tipo_veo:
            return {'error': f"tipo no válido; usa uno de {list(_REANUDACIONES)}"}
        eq = 'own' if (equipo or '').lower().startswith(('nos', 'pro', 'own')) else 'opp'
        validos = {id(e) for e in P.filtra(equipo, parte)}
        lista, primeros, receptores, carriles, finales, toques, caminos = [], Counter(), Counter(), Counter(), Counter(), Counter(), defaultdict(list)
        for i, e in enumerate(P.ev):
            if e['tipo'] != tipo_veo or e['eq'] != eq or id(e) not in validos:
                continue
            pases, fin = _secuencia_desde(P, i, eq)
            fin_ms = P._fin_ms
            finales[fin.split(' (')[0]] += 1
            clip = P.nuevo_clip(f"{'Nosotros' if eq == 'own' else 'Rival'} · {tipo.replace('_', ' ')} min {e['min']} → {fin}", e['ms'], fin_ms, e['min'])
            if not pases:
                lista.append({'minuto': e['min'], 'pases': 0, 'final': fin, 'clip': clip}); continue
            p0 = pases[0]
            primeros['largo (>=25 m)' if (p0['dist'] or 0) >= 25 else 'corto (<25 m)'] += 1
            receptores[p0['receptor'] or '?'] += 1
            carriles[P.zona(eq, p0['rx'] if p0['rx'] is not None else p0['x'], p0['ry'] if p0['ry'] is not None else p0['y']).split(', ')[-1]] += 1
            for p in pases:
                toques[p['dorsal']] += 1
            camino = tuple([p['dorsal'] for p in pases] + ([pases[-1]['receptor']] if pases[-1]['ok'] and pases[-1]['receptor'] else []))
            caminos[camino[:4]].append(pases[:3])
            d = P.nuevo_dibujo(f"{'Nosotros' if eq == 'own' else 'Rival'} · {tipo.replace('_', ' ')} min {e['min']}",
                               flechas=[_flecha(p, n + 1) for n, p in enumerate(pases[:8])], nota=f"Termina: {fin}")
            lista.append({'minuto': e['min'], 'pases': len(pases), 'camino_dorsales': ' → '.join(camino), 'final': fin, 'dibujo': d, 'clip': clip})
        mas = sorted(caminos.items(), key=lambda kv: (-len(kv[1]), -len(kv[0])))
        repetida = None
        if mas and len(mas[0][1]) >= 2:
            camino, ocs = mas[0]
            ocs = [o for o in ocs if len(o) == len(ocs[0])]
            repetida = {'camino_dorsales': ' → '.join(camino), 'veces': len(mas[0][1]),
                        'dibujo': _dibujo_media(P, f"Secuencia más repetida tras {tipo.replace('_', ' ')} ({'nosotros' if eq == 'own' else 'rival'})", ocs,
                                                nota=f"Repetida {len(mas[0][1])} veces (posición media)")}
        return {'equipo': P.nombres[eq], 'reanudaciones': len(lista), 'primer_pase': dict(primeros), 'receptor_primer_pase': receptores.most_common(6),
                'carril_primer_pase': dict(carriles), 'como_terminan': dict(finales), 'quien_mas_toca': toques.most_common(6),
                'secuencia_mas_repetida': repetida, 'detalle': lista[:30],
                'aviso': "'?' = dorsal no detectado por Veo"}

    def cadenas_pases_repetidas(equipo: str, longitud_minima: int = 3, zona_inicio: str = "cualquiera", zona_final: str = "cualquiera",
                                parte: str = "todo", top: int = 5) -> dict:
        """Busca las secuencias de pases (mismo orden de dorsales) que más repite un equipo y las dibuja.
        equipo: 'nosotros' o 'rival'. longitud_minima: nº de pases de la secuencia (2 a 6).
        zona_inicio / zona_final: 'cualquiera', 'campo propio' (salida de balón), 'zona media' o 'campo rival' (último tercio),
        vistas desde ese equipo. parte: 'todo', '1' o '2'. top: cuántas devolver (máx. 8)."""
        eq = 'own' if (equipo or '').lower().startswith(('nos', 'pro', 'own')) else 'opp'
        L = max(2, min(6, int(longitud_minima or 3)))
        pases = [e for e in P.filtra(equipo, parte) if e['tipo'] == 'pass']
        cont = defaultdict(list)
        for cad in _cadenas(pases):
            for i in range(0, len(cad) - L + 1):
                trozo = cad[i:i + L]
                if any(p['dorsal'] == '?' for p in trozo):
                    continue
                if zona_inicio and zona_inicio != 'cualquiera' and not P.zona(eq, trozo[0]['x'], trozo[0]['y']).startswith(zona_inicio):
                    continue
                ult = trozo[-1]
                if zona_final and zona_final != 'cualquiera' and not P.zona(eq, ult['rx'] if ult['rx'] is not None else ult['x'],
                                                                               ult['ry'] if ult['ry'] is not None else ult['y']).startswith(zona_final):
                    continue
                cont[tuple(p['dorsal'] for p in trozo) + ((trozo[-1]['receptor'] or '?'),)].append(trozo)
        ranking = sorted(cont.items(), key=lambda kv: -len(kv[1]))[:max(1, min(8, int(top or 5)))]
        out = []
        for camino, ocs in ranking:
            out.append({'camino_dorsales': ' → '.join(camino), 'veces': len(ocs), 'minutos': [o[0]['min'] for o in ocs],
                        'clips': [P.nuevo_clip(f"{' → '.join(camino)} · min {o[0]['min']}", o[0]['ms'], o[-1]['ms'], o[0]['min']) for o in ocs[:10]],
                        'zona_inicio': P.zona(eq, ocs[0][0]['x'], ocs[0][0]['y']),
                        'dibujo': _dibujo_media(P, f"{'Nosotros' if eq == 'own' else 'Rival'} · {' → '.join(camino)} ({len(ocs)} {'vez' if len(ocs) == 1 else 'veces'})", ocs,
                                                nota=f"Minutos: {', '.join(str(o[0]['min']) for o in ocs)}")})
        return {'equipo': P.nombres[eq], 'pases_analizados': len(pases), 'secuencias': out,
                'nota': 'Solo secuencias con todos los dorsales detectados; dibujo = posición media de las repeticiones'}

    def buscar_pases(equipo: str, jugador: str = "", receptor: str = "", zona_origen: str = "cualquiera", zona_destino: str = "cualquiera",
                     parte: str = "todo", solo_completados: bool = False, dibujar: bool = True) -> dict:
        """Filtra pases y devuelve totales, acierto, a quién se pasa, zonas y un dibujo (hasta 30 pases).
        equipo: 'nosotros' o 'rival'. jugador/receptor: dorsal ('' = todos). zona_origen/zona_destino: 'cualquiera', 'campo propio',
        'zona media', 'campo rival' o con carril, p. ej. 'campo propio, carril izquierdo' (vistas desde ese equipo). parte: 'todo','1','2'."""
        eq = 'own' if (equipo or '').lower().startswith(('nos', 'pro', 'own')) else 'opp'
        sel = []
        for p in P.filtra(equipo, parte):
            if p['tipo'] != 'pass' or (jugador and p['dorsal'] != str(jugador)) or (receptor and p['receptor'] != str(receptor)):
                continue
            if solo_completados and not p['ok']:
                continue
            if zona_origen and zona_origen != 'cualquiera' and not P.zona(eq, p['x'], p['y']).startswith(zona_origen):
                continue
            dz = P.zona(eq, p['rx'], p['ry']) if p['rx'] is not None else 'sin ubicar'
            if zona_destino and zona_destino != 'cualquiera' and not dz.startswith(zona_destino):
                continue
            sel.append(p)
        ok = sum(1 for p in sel if p['ok'])
        res = {'equipo': P.nombres[eq], 'pases': len(sel), 'completados': ok, 'acierto_pct': round(100 * ok / len(sel), 1) if sel else None,
               'por_pasador': Counter(p['dorsal'] for p in sel).most_common(8), 'por_receptor': Counter(p['receptor'] or '?' for p in sel).most_common(8),
               'zonas_origen': Counter(P.zona(eq, p['x'], p['y']) for p in sel).most_common(6),
               'distancia_media_m': round(sum(p['dist'] for p in sel if p['dist']) / max(1, sum(1 for p in sel if p['dist'])), 1) if sel else None}
        if dibujar and sel:
            res['dibujo'] = P.nuevo_dibujo(f"Pases {('nosotros' if eq == 'own' else 'rival')}" + (f" de #{jugador}" if jugador else '') + (f" a #{receptor}" if receptor else ''),
                                           flechas=[_flecha(p) for p in sel[:30]], nota=f"{len(sel)} pases ({ok} completados)" + (' · se dibujan 30' if len(sel) > 30 else ''))
        return res

    def buscar_acciones(equipo: str, tipo: str, parte: str = "todo", zona: str = "cualquiera", jugador: str = "") -> dict:
        """Lista y dibuja acciones de un equipo. tipo: 'tiro', 'gol', 'perdida' (pases fallados + balones perdidos), 'recuperacion'
        (intercepciones + entradas), 'duelo', 'falta', 'regate', 'corner', 'saque_banda', 'saque_puerta'.
        equipo: 'nosotros' o 'rival'. zona: 'cualquiera', 'campo propio', 'zona media', 'campo rival' (vista desde ese equipo)."""
        eq = 'own' if (equipo or '').lower().startswith(('nos', 'pro', 'own')) else 'opp'
        mapa = {'tiro': {'FootballShot'}, 'gol': {'FootballGoal'}, 'perdida': {'loose'}, 'recuperacion': {'interception', 'tackle'},
                'duelo': {'duel'}, 'falta': {'FootballFoul'}, 'regate': {'dribble'}, 'corner': {'FootballCornerKick'},
                'saque_banda': {'FootballThrowIn'}, 'saque_puerta': {'FootballGoalKick'}}
        t = (tipo or '').strip().lower()
        if t not in mapa:
            return {'error': f"tipo no válido; usa uno de {list(mapa)}"}
        sel = [e for e in P.filtra(equipo, parte) if (e['tipo'] in mapa[t] or (t == 'perdida' and e['tipo'] == 'pass' and e['ok'] is False))
               and (not jugador or e['dorsal'] == str(jugador))]
        if zona and zona != 'cualquiera':
            sel = [e for e in sel if P.zona(eq, e['x'], e['y']).startswith(zona)]
        colores = {'tiro': '#E31E24', 'gol': '#16a34a', 'perdida': '#f59e0b', 'recuperacion': '#22c55e'}
        acciones = [{'minuto': e['min'], 'dorsal': e['dorsal'], 'zona': P.zona(eq, e['x'], e['y']),
                     'clip': P.nuevo_clip(f"{t.replace('_', ' ').capitalize()} #{e['dorsal']} · min {e['min']}", e['ms'] - 3000, e['ms'] + 2000, e['min'])}
                    for e in sel[:30]]
        return {'equipo': P.nombres[eq], 'total': len(sel), 'acciones': acciones, 'por_jugador': Counter(e['dorsal'] for e in sel).most_common(8),
                'por_zona': Counter(P.zona(eq, e['x'], e['y']) for e in sel).most_common(9),
                'por_parte': Counter(f"{e['parte']}ª parte" for e in sel), 'minutos': [e['min'] for e in sel][:40],
                'dibujo': P.nuevo_dibujo(f"{t.replace('_', ' ').capitalize()} · {'nosotros' if eq == 'own' else 'rival'}",
                                         puntos=[{'x': e['x'], 'y': e['y'], 'etiqueta': e['dorsal'], 'min': e['min'], 'color': colores.get(t, '#3b82f6'),
                                                  'relleno': bool(e['ok']) if t == 'tiro' else True} for e in sel if e['x'] is not None][:60],
                                         nota=f"{len(sel)} acciones") if sel else None}

    def participacion_jugadores(equipo: str, fase: str = "todo", parte: str = "todo") -> dict:
        """Participación de cada jugador con balón: pases dados/recibidos, acierto, pérdidas, recuperaciones y zona media.
        fase: 'todo', 'salida_balon' (acciones en campo propio), 'creacion' (zona media) o 'ultimo_tercio' (campo rival).
        equipo: 'nosotros' o 'rival'. parte: 'todo', '1' o '2'."""
        eq = 'own' if (equipo or '').lower().startswith(('nos', 'pro', 'own')) else 'opp'
        tercio = {'salida_balon': 'campo propio', 'creacion': 'zona media', 'ultimo_tercio': 'campo rival'}.get((fase or '').lower())
        st = defaultdict(lambda: {'pases': 0, 'completados': 0, 'recibidos': 0, 'perdidas': 0, 'recuperaciones': 0, 'duelos': 0, 'tiros': 0, 'xs': []})
        for e in P.filtra(equipo, parte):
            if tercio and not P.zona(eq, e['x'], e['y']).startswith(tercio):
                continue
            s = st[e['dorsal']]
            if e['tipo'] == 'pass':
                s['pases'] += 1; s['completados'] += 1 if e['ok'] else 0
                if e['ok'] and e['receptor']:
                    st[e['receptor']]['recibidos'] += 1
                if e['ok'] is False:
                    s['perdidas'] += 1
            elif e['tipo'] == 'loose':
                s['perdidas'] += 1
            elif e['tipo'] in ('interception', 'tackle'):
                s['recuperaciones'] += 1
            elif e['tipo'] == 'duel':
                s['duelos'] += 1
            elif e['tipo'] == 'FootballShot':
                s['tiros'] += 1
            if e['x'] is not None:
                s['xs'].append((e['x'], e['y']))
        filas = []
        for d, s in st.items():
            if d == '?':
                continue
            zona = P.zona(eq, sum(x for x, _ in s['xs']) / len(s['xs']), sum(y for _, y in s['xs']) / len(s['xs'])) if s['xs'] else '-'
            filas.append({'dorsal': d, 'pases': s['pases'], 'acierto_pct': round(100 * s['completados'] / s['pases']) if s['pases'] else None,
                          'recibidos': s['recibidos'], 'perdidas': s['perdidas'], 'recuperaciones': s['recuperaciones'],
                          'duelos': s['duelos'], 'tiros': s['tiros'], 'zona_habitual': zona})
        filas.sort(key=lambda f: -(f['pases'] + f['recibidos']))
        return {'equipo': P.nombres[eq], 'fase': fase, 'jugadores': filas[:18]}

    import functools

    def simple(fn):
        # Devuelve solo tipos JSON (listas en vez de tuplas, etc.) y nunca rompe la conversación
        @functools.wraps(fn)
        def envuelta(*a, **k):
            try:
                return json.loads(json.dumps(fn(*a, **k), ensure_ascii=False, default=str))
            except Exception as e:
                print(f"Consultor herramienta {fn.__name__}: {e}")
                return {'error': str(e)}
        return envuelta

    return [simple(f) for f in (estadisticas_partido, secuencias_tras_reanudacion, cadenas_pases_repetidas,
                                buscar_pases, buscar_acciones, participacion_jugadores)]


_SISTEMA = """Eres el analista táctico jefe de un club de fútbol (nivel Licencia UEFA Pro, analista de datos de élite).
Respondes preguntas sobre UN partido usando EXCLUSIVAMENTE las herramientas disponibles, que consultan todas las acciones
registradas por la cámara Veo (pases con pasador, receptor, origen y destino, saques, duelos, tiros...).
Reglas:
- Usa siempre las herramientas antes de responder, TAMBIÉN en las preguntas de seguimiento (los datos y dibujos de respuestas
  anteriores no se pueden reutilizar: vuelve a consultar). Puedes llamar a varias. NUNCA inventes cifras, dorsales ni minutos.
- "Salida de balón" = acciones que empiezan en campo propio del equipo. "Último tercio" = campo rival.
- Cuando una herramienta devuelva un dibujo relevante (campo "dibujo": "Dn"), muéstralo escribiendo [[DIBUJO:Dn]] en su propia línea,
  JUSTO DESPUÉS del texto que habla de ESA jugada. Usa exactamente el id "Dn" que viene en el mismo elemento del que hablas
  (no mezcles ids de llamadas distintas). Si te piden dibujar algo, incluye siempre al menos un dibujo.
- Las herramientas devuelven también "clip": "Cn" (trozo de vídeo de esa jugada). Cuando listes jugadas concretas, o te pidan
  verlas, el vídeo o clips, escribe [[CLIP:Cn]] al final de la línea de CADA jugada (con su id exacto) para que el entrenador
  pueda darle al play. Si piden "todos", lista todas las que devuelva la herramienta, una por línea, cada una con su clip.
- Responde en español, en formato Markdown, de forma profesional, concreta y accionable: puntos clave en **negrita**,
  cifras exactas, minutos para revisar en el vídeo y, al final, una recomendación táctica breve para el cuerpo técnico.
- Si los datos son pocos o faltan dorsales ('?'), dilo con naturalidad (es una limitación de la detección de Veo).
- Nuestro equipo es "{nosotros}"; el rival es "{rival}". Marcador: {marcador}."""


def responder(veo_id, pregunta, historial):
    from analisis_postpartido import _modelo_gemini, markdown_a_html
    P = Partido(veo_id)
    genai, modelo_base = _modelo_gemini()
    modelo = genai.GenerativeModel(modelo_base.model_name, tools=_herramientas(P),
                                   system_instruction=_SISTEMA.format(nosotros=P.nombres['own'], rival=P.nombres['opp'],
                                                                      marcador='-'.join(str(x) for x in P.resumen.get('marcador') or [])))
    hist = []
    for m in (historial or [])[-6:]:
        if m.get('rol') in ('user', 'model') and (m.get('texto') or '').strip():
            # sin las marcas de dibujo de respuestas anteriores (si no, la IA las copia y saldría un dibujo viejo)
            texto = re.sub(r'\[\[(DIBUJO|CLIP):[DC]\d+\]\]', '', m['texto'])
            hist.append({'role': m['rol'], 'parts': [texto[:4000]]})
    chat = modelo.start_chat(history=hist, enable_automatic_function_calling=True)
    resp = chat.send_message(pregunta, request_options={"timeout": 150})
    texto = (resp.text or '').strip()
    usados = [k for k in re.findall(r'\[\[DIBUJO:(D\d+)\]\]', texto) if k in P.dibujos]
    html = markdown_a_html(texto)
    html = re.sub(r'(<p>)?\s*\[\[DIBUJO:(D\d+)\]\]\s*(</p>)?',
                  lambda m: f'<div class="cons-dibujo" data-d="{m.group(2)}"></div>' if m.group(2) in P.dibujos else '', html)
    clips = {k: P.clips[k] for k in re.findall(r'\[\[CLIP:(C\d+)\]\]', texto) if k in P.clips}
    html = re.sub(r'\[\[CLIP:(C\d+)\]\]', lambda m: (f'<button class="cons-clip" data-c="{m.group(1)}">▶ Ver jugada · min {P.clips[m.group(1)]["min"]}</button>'
                                                    if m.group(1) in P.clips else ''), html)
    return {'texto': texto, 'html': html, 'dibujos': {k: P.dibujos[k] for k in usados}, 'clips': clips, 'video_url': P.video_url}


# ───────────────────────── plantilla de preguntas ─────────────────────────

def _leer_plantillas():
    if os.path.exists(PLANTILLAS_FILE):
        try:
            with open(PLANTILLAS_FILE, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _guardar_plantillas(d):
    os.makedirs(os.path.dirname(PLANTILLAS_FILE), exist_ok=True)
    with open(PLANTILLAS_FILE, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


# ───────────────────────────── rutas ─────────────────────────────

@consultor_bp.route('/api/consultor/preguntar', methods=['POST'])
def api_preguntar():
    if not session.get('usuario'):
        return jsonify({"status": "error"}), 401
    d = request.json or {}
    veo_id = (d.get('veo_id') or '').strip()
    pregunta = (d.get('pregunta') or '').strip()
    if not re.fullmatch(r'[0-9a-f-]{36}', veo_id):
        return jsonify({"status": "error", "message": "Este análisis no tiene partido de Veo vinculado"}), 400
    if not pregunta:
        return jsonify({"status": "error", "message": "Escribe una pregunta"}), 400
    try:
        return jsonify({"status": "ok", **responder(veo_id, pregunta[:1500], d.get('historial') or [])})
    except Exception as e:
        print(f"Error consultor IA: {e}")
        return jsonify({"status": "error", "message": f"No se ha podido responder: {e}"}), 500


@consultor_bp.route('/api/consultor/plantilla', methods=['GET', 'POST', 'DELETE'])
def api_plantilla():
    """GET: preguntas sugeridas + las mías. POST {texto, cat}: añadir. DELETE {id}: borrar una mía."""
    usuario = (session.get('usuario') or '').strip().lower()
    if not usuario:
        return jsonify({"status": "error"}), 401
    with _lock_plantillas:
        todas = _leer_plantillas()
        mias = todas.get(usuario, [])
        if request.method == 'POST':
            d = request.json or {}
            texto = (d.get('texto') or '').strip()[:500]
            if not texto:
                return jsonify({"status": "error", "message": "Pregunta vacía"}), 400
            if not any(p['texto'].lower() == texto.lower() for p in mias):
                mias.append({'id': f"p{len(mias) + 1}_{abs(hash(texto)) % 100000}", 'texto': texto, 'cat': (d.get('cat') or 'Mis preguntas')[:40]})
            todas[usuario] = mias
            _guardar_plantillas(todas)
        elif request.method == 'DELETE':
            pid = (request.json or {}).get('id')
            todas[usuario] = mias = [p for p in mias if p['id'] != pid]
            _guardar_plantillas(todas)
    return jsonify({"status": "ok", "sugeridas": PREGUNTAS_ELITE, "mias": mias})
