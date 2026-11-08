"""Aviso diario asistencias/entrenamientos (WhatsApp privado a cada entrenador, 21:00 por defecto).

Solo los días que entrena alguno de sus equipos, y solo de esos equipos. Para cada uno, los días de
entrenamiento del mes en curso (hasta hoy incluido) sin asistencia pasada o sin foto subida, con un
enlace al primer día pendiente de cada cosa. Si no le falta nada, no se le envía. Aficionados fuera.
Configuración y último envío en datos_privados/aviso_diario.json; lo lanza el bucle de push_scheduler.
"""
import os
import json
import unicodedata
from datetime import datetime, date, timedelta
from urllib.parse import urlencode
from flask import Blueprint, request, jsonify

aviso_bp = Blueprint('aviso_diario', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(BASE_DIR, 'datos_privados', 'aviso_diario.json')
URL_BASE = 'https://intranet.clubfuentelarreyna.com'
_DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
_DIA_NUM = {'lunes': 0, 'martes': 1, 'miercoles': 2, 'jueves': 3, 'viernes': 4, 'sabado': 5, 'domingo': 6}


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.upper().split())


def _bonito(equipo):
    """'ALEVIN A F7' → 'ALEVÍN A F7' (solo para el texto del mensaje)."""
    return (' '.join(equipo.upper().split()).replace('PREBENJAMIN', 'PREBENJAMÍN')
            .replace('BENJAMIN', 'BENJAMÍN').replace('ALEVIN', 'ALEVÍN'))


def cargar_cfg():
    try:
        with open(CFG_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    cfg.setdefault('activo', True)
    cfg.setdefault('hora', '21:00')
    return cfg


def guardar_cfg(cfg):
    os.makedirs(os.path.dirname(CFG_FILE), exist_ok=True)
    tmp = CFG_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CFG_FILE)


def _dias_entreno():
    """{EQUIPO_NORMALIZADO: {0..6}} según la configuración de equipos de la temporada."""
    from deportivo import _get_equipos_config_path
    out = {}
    try:
        with open(_get_equipos_config_path(), encoding='utf-8') as f:
            for e in json.load(f).get('equipos', []):
                nums = {_DIA_NUM[k] for k in (_norm(d).lower() for d in e.get('dias', [])) if k in _DIA_NUM}
                if e.get('nombre') and nums:
                    out[_norm(e['nombre'])] = nums
    except Exception as ex:
        print(f"[AVISO] config equipos: {ex}", flush=True)
    return out


def calcular(dia=None):
    """Mensajes del aviso para el día dado (por defecto hoy).
    Devuelve {'mensajes': [{usuario, telefono, texto}], 'avisos': [texto de incidencias]}."""
    from app import client, NOMBRE_EXCEL, UPLOAD_FOLDER, sanitizar_equipo_archivo
    dia = dia or date.today()
    dow = dia.weekday()
    dias_cfg = _dias_entreno()
    sh = client.open(NOMBRE_EXCEL)

    hechos = set()  # (dd/mm/aaaa, EQUIPO) con alguna asistencia guardada
    for r in sh.worksheet('ASISTENCIAS').get_all_values()[1:]:
        if len(r) >= 2 and r[0].strip():
            hechos.add((r[0].strip(), _norm(r[1])))
    try:
        fotos = set(os.listdir(UPLOAD_FOLDER))
    except Exception:
        fotos = set()

    # Entrenadores (PERFILES): por teléfono, juntando equipos si aparece varias veces
    p = sh.worksheet('PERFILES').get_all_values()
    h = [x.strip().upper() for x in (p[0] if p else [])]
    i_u, i_t, i_e = h.index('USUARIO'), h.index('TELEFONO'), h.index('EQUIPO')
    entrenadores = {}
    for r in p[1:]:
        g = lambda i: r[i].strip() if i < len(r) else ''
        eqs = [e.strip() for e in g(i_e).split(',') if e.strip() and not _norm(e).startswith('AFICIONADO')]
        if not g(i_u) or not eqs:
            continue
        k = g(i_t) or ('sin-telefono:' + g(i_u))
        ent = entrenadores.setdefault(k, {'usuario': g(i_u), 'telefono': g(i_t), 'equipos': []})
        ent['equipos'] += [e for e in eqs if e not in ent['equipos']]

    avisos = []
    entrenan_hoy = {eq for eq, ds in dias_cfg.items() if dow in ds and not eq.startswith('AFICIONADO')}
    cubiertos = {_norm(e) for ent in entrenadores.values() if ent['telefono'] for e in ent['equipos']}
    for eq in sorted(entrenan_hoy - cubiertos):
        avisos.append(f'{eq}: entrena hoy pero no tiene ningún entrenador con teléfono en los usuarios.')
    sin_cfg = sorted({_norm(e) for ent in entrenadores.values() for e in ent['equipos']} - set(dias_cfg))
    if sin_cfg:
        avisos.append('Sin días de entrenamiento configurados (no se avisa): ' + ', '.join(sin_cfg))

    def fdia(d):
        return f'hoy {d:%d/%m}' if d == dia else f'{_DIAS[d.weekday()]} {d:%d/%m}'

    def enlace(eq, accion, d):
        return f"{URL_BASE}/aviso?{urlencode({'equipo': eq, 'accion': accion, 'fecha': d.isoformat()})}"

    mensajes = []
    for ent in entrenadores.values():
        bloques = []
        for eq in ent['equipos']:
            dias_eq = dias_cfg.get(_norm(eq))
            if not dias_eq or dow not in dias_eq:
                continue  # este equipo no entrena hoy: no se le avisa de él hoy
            entrenos = [d for d in (dia.replace(day=n) for n in range(1, dia.day + 1)) if d.weekday() in dias_eq]
            sin_asis = [d for d in entrenos if (f'{d:%d/%m/%Y}', _norm(eq)) not in hechos]
            sin_foto = [d for d in entrenos if f'entreno_{sanitizar_equipo_archivo(eq)}_{d:%d-%m-%Y}.jpg' not in fotos]
            if not sin_asis and not sin_foto:
                continue
            orden = lambda ds: sorted(ds, key=lambda d: (d != dia, -d.toordinal()))  # hoy primero, luego del más reciente
            # Formato fijado por Dirección Deportiva (negritas de WhatsApp con *...*)
            b = [f'*{_bonito(eq)}*']
            if sin_asis:
                b.append('    • *Asistencia*: ' + ' · '.join(fdia(d) for d in orden(sin_asis)))
                b.append(f'  ➜ Enlace: {enlace(eq, "asis", min(sin_asis))}')
            if sin_foto:
                b.append('• *Foto del entrenamiento*: ' + ' · '.join(fdia(d) for d in orden(sin_foto)))
                b.append(f'  ➜ Enlace: {enlace(eq, "foto", min(sin_foto))}')
            bloques.append('\n'.join(b))
        if not bloques:
            continue
        nombre = ent['usuario'].split()[0].capitalize()
        texto = (f'Hola {nombre}, quedan pendientes estos registros:\n'
                 + '\n\n'.join(bloques) +
                 '\n\nDirección Deportiva')
        if not ent['telefono']:
            avisos.append(f"{ent['usuario']}: tiene pendientes pero no tiene teléfono en su usuario.")
            continue
        mensajes.append({'usuario': ent['usuario'], 'telefono': ent['telefono'], 'texto': texto})
    mensajes.sort(key=lambda m: m['usuario'].lower())
    return {'mensajes': mensajes, 'avisos': avisos}


def enviar(dia=None):
    from app import enviar_whatsapp
    res = calcular(dia)
    ok = 0
    for m in res['mensajes']:
        bien, detalle = enviar_whatsapp(m['telefono'], m['texto'])
        ok += bool(bien)
        if not bien:
            print(f"[AVISO] No enviado a {m['usuario']}: {detalle}", flush=True)
    print(f"[AVISO] Aviso diario: enviado a {ok} de {len(res['mensajes'])}", flush=True)
    return ok, len(res['mensajes'])


def tick(ahora):
    """Lo llama push_scheduler cada minuto. Envía una vez al día, en la media hora siguiente a la hora fijada."""
    cfg = cargar_cfg()
    if not cfg.get('activo'):
        return
    hh, mm = [int(x) for x in cfg['hora'].split(':')]
    inicio = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
    hoy = ahora.date().isoformat()
    if cfg.get('ultimo') == hoy or not (inicio <= ahora < inicio + timedelta(minutes=30)):
        return
    cfg['ultimo'] = hoy  # marcar antes de enviar: nunca dos veces el mismo día
    guardar_cfg(cfg)
    enviar(ahora.date())


def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@aviso_bp.route('/api/whatsapp/aviso_diario', methods=['GET', 'POST'])
def api_aviso_cfg():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    cfg = cargar_cfg()
    if request.method == 'POST':
        data = request.json or {}
        if 'activo' in data:
            cfg['activo'] = bool(data['activo'])
        hora = (data.get('hora') or '').strip()
        if hora:
            try:
                hh, mm = [int(x) for x in hora.split(':')]
                assert 0 <= hh < 24 and 0 <= mm < 60
                cfg['hora'] = f'{hh:02d}:{mm:02d}'
            except Exception:
                return jsonify({'status': 'error', 'message': 'Hora no válida'}), 400
        guardar_cfg(cfg)
    return jsonify({'status': 'ok', 'activo': cfg['activo'], 'hora': cfg['hora'], 'ultimo': cfg.get('ultimo', '')})


@aviso_bp.route('/api/whatsapp/aviso_diario/vista')
def api_aviso_vista():
    """Qué se enviaría (sin enviar nada). ?fecha=YYYY-MM-DD para ver otro día del mes."""
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    try:
        f = request.args.get('fecha')
        dia = datetime.strptime(f, '%Y-%m-%d').date() if f else None
        return jsonify(dict(status='ok', **calcular(dia)))
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)[:200]}), 500
