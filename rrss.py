import os, json
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, current_app

rrss_bp = Blueprint('rrss', __name__)

PLANNING_SHEET = 'RRSS_PLANNING'
VIDEOS_SHEET   = 'RRSS_VIDEOS'
DBX_RRSS_FOLDER = '/RRSS'
DBX_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dropbox_config.json')

REDES_DISPONIBLES = ['Instagram', 'TikTok', 'YouTube', 'Facebook', 'Twitter/X']

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _tiene_acceso():
    u = session.get('usuario', '')
    if u.lower() == 'admin':
        return True
    return session.get('permisos', {}).get('RRSS') == 'SI'

def _dbx():
    import dropbox
    with open(DBX_CONFIG_PATH, encoding='utf-8') as f:
        cfg = json.load(f)
    # Usar refresh_token permanente si está disponible
    if cfg.get('refresh_token') and cfg.get('app_key') and cfg.get('app_secret'):
        return dropbox.Dropbox(
            oauth2_refresh_token=cfg['refresh_token'],
            app_key=cfg['app_key'],
            app_secret=cfg['app_secret']
        )
    # Fallback: token de corta duración
    token = cfg.get('access_token', '')
    if not token:
        raise ValueError('No hay token de Dropbox configurado. Ejecuta dropbox_setup_oauth.py')
    return dropbox.Dropbox(token)

def _planning_sheet():
    client = current_app.gs_client
    wb = client.open(current_app.gs_name)
    try:
        return wb.worksheet(PLANNING_SHEET)
    except Exception:
        sh = wb.add_worksheet(title=PLANNING_SHEET, rows=1000, cols=3)
        sh.append_row(['FECHA', 'CONTENIDO', 'ACTUALIZADO'])
        return sh

def _videos_sheet():
    client = current_app.gs_client
    wb = client.open(current_app.gs_name)
    try:
        return wb.worksheet(VIDEOS_SHEET)
    except Exception:
        sh = wb.add_worksheet(title=VIDEOS_SHEET, rows=500, cols=6)
        sh.append_row(['NOMBRE', 'FECHA_SUBIDA', 'PUBLICADO', 'FECHA_PUBLICACION', 'REDES', 'DBX_PATH'])
        return sh

# ─── Rutas ────────────────────────────────────────────────────────────────────

@rrss_bp.route('/rrss')
def rrss_page():
    if not session.get('usuario'):
        return redirect(url_for('index'))
    if not _tiene_acceso():
        return redirect(url_for('index'))
    return render_template('rrss.html',
                           usuario=session.get('usuario'),
                           redes=REDES_DISPONIBLES)


@rrss_bp.route('/api/rrss/planning')
def api_rrss_planning_get():
    if not session.get('usuario') or not _tiene_acceso():
        return jsonify({'error': 'No auth'}), 401

    mes_str = request.args.get('mes', '')
    if mes_str:
        import calendar as cal_mod
        try:
            year, month = int(mes_str[:4]), int(mes_str[5:7])
            _, last_day = cal_mod.monthrange(year, month)
            first = date(year, month, 1)
            dias = {(first + timedelta(days=i)).isoformat(): '' for i in range(last_day)}
        except Exception:
            return jsonify({'error': 'Mes inválido'}), 400
        try:
            rows = _planning_sheet().get_all_records()
            for r in rows:
                f = str(r.get('FECHA', '')).strip()
                if f in dias:
                    dias[f] = str(r.get('CONTENIDO', ''))
        except Exception as e:
            print(f'[rrss] planning mes error: {e}')
        return jsonify({'dias': dias, 'mes': mes_str})

    fecha_str = request.args.get('lunes', date.today().isoformat())
    try:
        lunes = date.fromisoformat(fecha_str)
    except Exception:
        lunes = date.today()
    lunes = lunes - timedelta(days=lunes.weekday())
    semana = {(lunes + timedelta(days=i)).isoformat(): '' for i in range(7)}
    try:
        rows = _planning_sheet().get_all_records()
        for r in rows:
            f = str(r.get('FECHA', '')).strip()
            if f in semana:
                semana[f] = str(r.get('CONTENIDO', ''))
    except Exception as e:
        print(f'[rrss] planning get error: {e}')
    return jsonify({'semana': semana, 'lunes': lunes.isoformat()})


TIPO_KEYS = ['TIPO-LUN','TIPO-MAR','TIPO-MIE','TIPO-JUE','TIPO-VIE','TIPO-SAB','TIPO-DOM']
TIPO_ESTADOS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'rrss_tipo_estados.json')

def _leer_tipo_estados():
    try:
        with open(TIPO_ESTADOS_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def _guardar_tipo_estados(data):
    os.makedirs(os.path.dirname(TIPO_ESTADOS_PATH), exist_ok=True)
    with open(TIPO_ESTADOS_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@rrss_bp.route('/api/rrss/planning/tipo')
def api_rrss_planning_tipo_get():
    if not session.get('usuario') or not _tiene_acceso():
        return jsonify({'error': 'No auth'}), 401
    dias = {k: '' for k in TIPO_KEYS}
    try:
        rows = _planning_sheet().get_all_records()
        for r in rows:
            f = str(r.get('FECHA', '')).strip()
            if f in dias:
                dias[f] = str(r.get('CONTENIDO', ''))
    except Exception as e:
        print(f'[rrss] tipo get error: {e}')
    return jsonify({'tipo': dias})

@rrss_bp.route('/api/rrss/tipo/estados')
def api_rrss_tipo_estados_get():
    if not session.get('usuario'):
        return jsonify({'error': 'No auth'}), 401
    lunes_str = request.args.get('lunes', '')
    data = _leer_tipo_estados()
    if lunes_str:
        try:
            lunes = date.fromisoformat(lunes_str)
            dates = [(lunes + timedelta(days=i)).isoformat() for i in range(7)]
            filtered = {k: v for k, v in data.items() if any(k.startswith(d) for d in dates)}
            return jsonify({'estados': filtered})
        except Exception:
            pass
    return jsonify({'estados': data})

@rrss_bp.route('/api/rrss/tipo/estado', methods=['POST'])
def api_rrss_tipo_estado_post():
    if not session.get('usuario'):
        return jsonify({'error': 'No auth'}), 401
    d = request.json or {}
    key   = d.get('key', '').strip()   # "YYYY-MM-DD|TIPO-LUN"
    estado = d.get('estado', 'PENDIENTE')
    if not key:
        return jsonify({'error': 'Falta key'}), 400
    data = _leer_tipo_estados()
    data[key] = estado
    _guardar_tipo_estados(data)
    return jsonify({'status': 'ok'})


@rrss_bp.route('/api/rrss/planning/tipo', methods=['POST'])
def api_rrss_planning_tipo_post():
    if not session.get('usuario') or not _tiene_acceso():
        return jsonify({'error': 'No auth'}), 401
    d = request.json or {}
    tipo = d.get('tipo', {})
    if not tipo:
        return jsonify({'error': 'Faltan datos'}), 400
    try:
        sh   = _planning_sheet()
        rows = sh.get_all_records()
        idx_map = {}
        for i, r in enumerate(rows):
            f = str(r.get('FECHA', '')).strip()
            if f in TIPO_KEYS:
                idx_map[f] = i + 2
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        for key, contenido in tipo.items():
            if key not in TIPO_KEYS:
                continue
            if key in idx_map:
                sh.update(f'A{idx_map[key]}:C{idx_map[key]}', [[key, contenido, now]])
            else:
                sh.append_row([key, contenido, now])
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@rrss_bp.route('/api/rrss/planning', methods=['POST'])
def api_rrss_planning_post():
    if not session.get('usuario') or not _tiene_acceso():
        return jsonify({'error': 'No auth'}), 401
    d = request.json or {}
    fecha     = d.get('fecha', '')
    contenido = d.get('contenido', '')
    if not fecha:
        return jsonify({'error': 'Falta fecha'}), 400
    try:
        sh   = _planning_sheet()
        rows = sh.get_all_records()
        idx  = None
        for i, r in enumerate(rows):
            if str(r.get('FECHA', '')).strip() == fecha:
                idx = i + 2; break
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        if idx:
            sh.update(f'A{idx}:C{idx}', [[fecha, contenido, now]])
        else:
            sh.append_row([fecha, contenido, now])
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@rrss_bp.route('/api/rrss/videos')
def api_rrss_videos():
    if not session.get('usuario') or not _tiene_acceso():
        return jsonify({'error': 'No auth'}), 401
    try:
        import dropbox
        dbx = _dbx()
        # Listar archivos en la carpeta /RRSS
        try:
            res = dbx.files_list_folder(DBX_RRSS_FOLDER)
            archivos_dbx = {e.name: e for e in res.entries if isinstance(e, dropbox.files.FileMetadata)}
        except dropbox.exceptions.ApiError:
            archivos_dbx = {}

        # Estado desde Sheets
        try:
            rows = _videos_sheet().get_all_records()
            estado_map = {str(r.get('NOMBRE','')): r for r in rows if r.get('NOMBRE')}
        except Exception:
            estado_map = {}

        videos = []
        for nombre, meta in sorted(archivos_dbx.items(), key=lambda x: x[1].server_modified, reverse=True):
            e = estado_map.get(nombre, {})
            videos.append({
                'nombre':           nombre,
                'fecha_subida':     meta.server_modified.strftime('%Y-%m-%d %H:%M'),
                'publicado':        str(e.get('PUBLICADO', 'NO')).upper() == 'SI',
                'fecha_publicacion':str(e.get('FECHA_PUBLICACION', '')),
                'redes':            str(e.get('REDES', '')),
                'dbx_path':         meta.path_lower,
            })
        return jsonify({'videos': videos})
    except Exception as e:
        return jsonify({'error': str(e), 'videos': []})


@rrss_bp.route('/api/rrss/video/link')
def api_rrss_video_link():
    if not session.get('usuario') or not _tiene_acceso():
        return jsonify({'error': 'No auth'}), 401
    dbx_path = request.args.get('path', '')
    if not dbx_path:
        return jsonify({'error': 'Falta path'}), 400
    try:
        dbx  = _dbx()
        link = dbx.files_get_temporary_link(dbx_path)
        return jsonify({'url': link.link})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@rrss_bp.route('/api/rrss/video/subir', methods=['POST'])
def api_rrss_video_subir():
    if not session.get('usuario') or not _tiene_acceso():
        return jsonify({'error': 'No auth'}), 401
    archivo = request.files.get('archivo')
    if not archivo:
        return jsonify({'error': 'No se recibió archivo'}), 400
    try:
        import dropbox
        dbx  = _dbx()
        nombre   = archivo.filename
        dbx_path = f'{DBX_RRSS_FOLDER}/{nombre}'
        data     = archivo.read()
        dbx.files_upload(data, dbx_path, mode=dropbox.files.WriteMode.overwrite, mute=True)

        # Registrar en Sheets
        sh   = _videos_sheet()
        rows = sh.get_all_records()
        idx  = None
        for i, r in enumerate(rows):
            if str(r.get('NOMBRE', '')) == nombre:
                idx = i + 2; break
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        if idx:
            sh.update(f'A{idx}:F{idx}', [[nombre, now, 'NO', '', '', dbx_path]])
        else:
            sh.append_row([nombre, now, 'NO', '', '', dbx_path])
        return jsonify({'status': 'ok', 'nombre': nombre})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@rrss_bp.route('/api/rrss/video/marcar', methods=['POST'])
def api_rrss_video_marcar():
    if not session.get('usuario') or not _tiene_acceso():
        return jsonify({'error': 'No auth'}), 401
    d = request.json or {}
    nombre   = d.get('nombre', '')
    publicado= d.get('publicado', True)
    fecha_p  = d.get('fecha_publicacion', '')
    redes    = d.get('redes', '')
    if not nombre:
        return jsonify({'error': 'Falta nombre'}), 400
    try:
        sh   = _videos_sheet()
        rows = sh.get_all_records()
        idx  = None
        for i, r in enumerate(rows):
            if str(r.get('NOMBRE', '')) == nombre:
                idx = i + 2; break
        pub_str = 'SI' if publicado else 'NO'
        if idx:
            sh.update(f'C{idx}:E{idx}', [[pub_str, fecha_p, redes]])
        else:
            now = datetime.now().strftime('%Y-%m-%d %H:%M')
            sh.append_row([nombre, now, pub_str, fecha_p, redes, ''])
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@rrss_bp.route('/api/rrss/video/renombrar', methods=['POST'])
def api_rrss_video_renombrar():
    if not session.get('usuario') or not _tiene_acceso():
        return jsonify({'error': 'No auth'}), 401
    d = request.json or {}
    nombre_old = d.get('nombre_old', '').strip()
    nombre_new = d.get('nombre_new', '').strip()
    dbx_path   = d.get('dbx_path', '').strip()
    if not nombre_old or not nombre_new or not dbx_path:
        return jsonify({'error': 'Faltan datos'}), 400
    if nombre_old == nombre_new:
        return jsonify({'status': 'ok', 'dbx_path_new': dbx_path})
    try:
        import dropbox
        dbx = _dbx()
        parent = dbx_path.rsplit('/', 1)[0]
        dbx_path_new = f'{parent}/{nombre_new}'
        meta = dbx.files_move_v2(dbx_path, dbx_path_new, autorename=False)
        new_path_lower = meta.metadata.path_lower

        # Actualizar nombre en Sheets
        sh   = _videos_sheet()
        rows = sh.get_all_records()
        for i, r in enumerate(rows):
            if str(r.get('NOMBRE', '')) == nombre_old:
                sh.update(f'A{i+2}', [[nombre_new]])
                sh.update(f'F{i+2}', [[new_path_lower]])
                break
        return jsonify({'status': 'ok', 'dbx_path_new': new_path_lower, 'nombre_new': nombre_new})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@rrss_bp.route('/api/rrss/video/eliminar', methods=['POST'])
def api_rrss_video_eliminar():
    if not session.get('usuario') or not _tiene_acceso():
        return jsonify({'error': 'No auth'}), 401
    d = request.json or {}
    nombre   = d.get('nombre', '')
    dbx_path = d.get('dbx_path', '')
    if not nombre:
        return jsonify({'error': 'Falta nombre'}), 400
    try:
        import dropbox
        dbx = _dbx()
        if dbx_path:
            try:
                dbx.files_delete_v2(dbx_path)
            except Exception:
                pass
        # Eliminar de Sheets
        sh   = _videos_sheet()
        rows = sh.get_all_records()
        for i, r in enumerate(rows):
            if str(r.get('NOMBRE', '')) == nombre:
                sh.delete_rows(i + 2)
                break
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
