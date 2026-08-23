import os
import json
import calendar
import requests
import re
import io
import threading
import pdfplumber
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from urllib.parse import urljoin

# Archivo de cache
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'rffm_cache.json')
SHIELDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'shields')
os.makedirs(SHIELDS_DIR, exist_ok=True)

KIT_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'kit_colors_cache.json')
KIT_REPORT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'kit_clash_report.json')

def _conv_data_folder():
    try:
        from app import get_data_folder
        return get_data_folder()
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data')

def _conv_cache_file():   return os.path.join(_conv_data_folder(), 'convocatorias_cache.json')
def _conv_report_file():  return os.path.join(_conv_data_folder(), 'convocatorias_report.json')
def _conv_anot_file():    return os.path.join(_conv_data_folder(), 'convocatorias_anotaciones.json')

# Aliases para compatibilidad con módulos externos que importan las constantes
CONVOCATORIAS_CACHE_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'convocatorias_cache.json')
CONVOCATORIAS_REPORT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'convocatorias_report.json')

# --- Auto-sincronización en segundo plano ---
# Credenciales guardadas fuera de /static (no servidas públicamente) y fuera de git.
RFFM_CREDENTIALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'rffm_credentials.json')
AUTO_SYNC_MIN_INTERVAL_MINUTES = 60

_auto_sync_state = {'running': False, 'error': None, 'started_at': None, 'finished_at': None}
_auto_sync_lock = threading.Lock()


def guardar_credenciales_rffm(usuario, password):
    """Guarda las credenciales RFFM en disco (fuera de /static y fuera de git) para poder
    re-sincronizar automáticamente en segundo plano sin pedirlas cada vez."""
    os.makedirs(os.path.dirname(RFFM_CREDENTIALS_FILE), exist_ok=True)
    with open(RFFM_CREDENTIALS_FILE, 'w', encoding='utf-8') as f:
        json.dump({'usuario': usuario, 'password': password}, f)


def cargar_credenciales_rffm():
    if not os.path.exists(RFFM_CREDENTIALS_FILE):
        return None, None
    try:
        with open(RFFM_CREDENTIALS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('usuario'), data.get('password')
    except Exception:
        return None, None


def auto_sync_status():
    estado = dict(_auto_sync_state)
    cache = load_cached_data()
    estado['last_updated'] = cache.get('last_updated', '')
    estado['tiene_credenciales'] = bool(cargar_credenciales_rffm()[0])
    return estado


def _datos_desactualizados():
    cache = load_cached_data()
    last_updated = (cache.get('last_updated') or '')[:19]  # quita sufijos tipo "(Mapeo Completo...)"
    if not last_updated:
        return True
    try:
        dt = datetime.strptime(last_updated, '%d/%m/%Y %H:%M:%S')
    except Exception:
        return True
    return (datetime.now() - dt) >= timedelta(minutes=AUTO_SYNC_MIN_INTERVAL_MINUTES)


AUTO_SYNC_RETRY_AFTER_FAILURE_MINUTES = 10


def intentar_autosync_background():
    """Lanza sync_rffm() en un hilo aparte si los datos llevan más de AUTO_SYNC_MIN_INTERVAL_MINUTES
    sin actualizarse y no hay ya una sincronización en curso. No bloquea la petición HTTP que lo llama.
    Si el último intento falló, espera AUTO_SYNC_RETRY_AFTER_FAILURE_MINUTES antes de reintentar, para
    no entrar en bucle de reintentos (p.ej. credenciales incorrectas)."""
    with _auto_sync_lock:
        if _auto_sync_state['running']:
            return False
        if _auto_sync_state['error'] and _auto_sync_state['finished_at']:
            try:
                last_try = datetime.strptime(_auto_sync_state['finished_at'], '%d/%m/%Y %H:%M:%S')
                if (datetime.now() - last_try) < timedelta(minutes=AUTO_SYNC_RETRY_AFTER_FAILURE_MINUTES):
                    return False
            except Exception:
                pass
        if not _datos_desactualizados():
            return False
        usuario, password = cargar_credenciales_rffm()
        if not usuario or not password:
            return False
        _auto_sync_state['running'] = True
        _auto_sync_state['error'] = None
        _auto_sync_state['started_at'] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    def _job():
        try:
            success, message = sync_rffm(usuario, password)
            if not success:
                _auto_sync_state['error'] = message
        except Exception as e:
            _auto_sync_state['error'] = str(e)
        finally:
            _auto_sync_state['running'] = False
            _auto_sync_state['finished_at'] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    threading.Thread(target=_job, daemon=True).start()
    return True

def get_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest"
    })
    return session

def download_shield(url, club_id, auth_session=None):
    """Descarga un escudo y lo guarda localmente en static/shields/.
    Devuelve la URL local o vacío si falla.
    Usa auth_session si se proporciona (para intranet autenticada)."""
    if not url or not club_id:
        return ''
        
    if url.startswith('/static/') or url.startswith('static/'):
        return url
    
    local_filename = f"club_{club_id}.png"
    local_path = os.path.join(SHIELDS_DIR, local_filename)
    local_url = f"/static/shields/{local_filename}"
    
    # Si ya existe localmente, devolver directamente
    if os.path.exists(local_path) and os.path.getsize(local_path) > 500:
        return local_url
    
    try:
        sess = auth_session if auth_session else get_session()
        r = sess.get(url, timeout=8)
        content_type = r.headers.get("Content-Type", "")
        # Solo guardar si es imagen real
        if r.status_code == 200 and ("image" in content_type or r.content[:4] in [b'\x89PNG', b'\xff\xd8\xff\xe0', b'GIF8', b'RIFF']):
            with open(local_path, 'wb') as f:
                f.write(r.content)
            return local_url
        else:
            return ''
    except Exception as e:
        print(f"  [Escudo] No se pudo descargar {url}: {e}")
        return ''

def get_local_shield_url(club_id):
    """Devuelve la URL local del escudo si existe, o vacío."""
    if not club_id:
        return ''
    local_path = os.path.join(SHIELDS_DIR, f"club_{club_id}.png")
    if os.path.exists(local_path) and os.path.getsize(local_path) > 500:
        return f"/static/shields/club_{club_id}.png"
    return ''

def extract_shield_id(url):
    if not url:
        return ""
    m = re.search(r'club_(\d+)', url, re.I)
    if m:
        return m.group(1)
    # Extraer el nombre del archivo sin extensión y limpiarlo
    m_fn = re.search(r'/([^/]+)\.[a-zA-Z0-9]{3,4}$', url)
    if m_fn:
        return re.sub(r'[^a-zA-Z0-9_-]', '', m_fn.group(1))
    return ""

def test_login(username, password):
    """Prueba si las credenciales son válidas"""
    url_login = "https://intranet.ffmadrid.es/nfg/NLogin"
    session = get_session()
    payload = {
        "NUser": username,
        "NPass": password,
        "LoginAjax": "1"
    }
    try:
        res = session.post(url_login, data=payload)
        match_est = re.search(r'var estado="(\d+)"', res.text)
        estado = match_est.group(1) if match_est else "2"
        return estado == "1", res.text
    except Exception as e:
        return False, str(e)

def detect_categoria_y_letra(comp_name):
    name = comp_name.upper().strip()
    
    # Excluir Copa completamente
    if "COPA" in name:
        return None, None
        
    if "AFICIONADO" in name:
        cat = "AFICIONADO"
    elif "JUVENIL" in name:
        cat = "JUVENIL"
    elif "CADETE" in name:
        cat = "CADETE"
    elif "INFANTIL" in name:
        cat = "INFANTIL"
    elif "ALEVIN F-7" in name or "ALEVIN F7" in name or "ALEVÍN F-7" in name or "ALEVÍN F7" in name or ("ALEVIN" in name and "F-7" in name) or ("ALEVÍN" in name and "F-7" in name):
        cat = "ALEVIN F7"
    elif "ALEVIN" in name or "ALEVÍN" in name:
        cat = "ALEVIN"
    elif "PREBENJAMIN" in name or "PREBENJAMÍN" in name:
        cat = "PREBENJAMIN"
    elif "BENJAMIN" in name or "BENJAMÍN" in name:
        cat = "BENJAMIN"
    else:
        cat = "OTROS"
        
    letra = "A" # Default
    
    if cat == "AFICIONADO":
        if "SEGUNDA" in name or "2ª" in name or "GRUPO 14" in name:
            letra = "B"
        else:
            letra = "A"
    elif cat == "JUVENIL":
        if "PRIMERA" in name and "SEGUNDA" not in name:
            letra = "A"
        elif "SEGUNDA" in name:
            letra = "C" if "GRUPO 22" in name else "B"
        elif "GRUPO 28" in name:
            letra = "B"
        elif "GRUPO 22" in name:
            letra = "C"
        else:
            letra = "A"
    elif cat == "CADETE":
        if "PREFERENTE" in name or "GRUPO 2" in name:
            letra = "A"
        elif "PRIMERA" in name and "SEGUNDA" not in name:
            letra = "B"
        elif "GRUPO 11" in name:
            letra = "B"
        elif "GRUPO 8" in name:
            letra = "C"
        elif "GRUPO 31" in name:
            letra = "D"
        elif "GRUPO 18" in name:
            letra = "E"
    elif cat == "INFANTIL":
        if "PRIMERA" in name and "SEGUNDA" not in name:
            letra = "A"
        elif "GRUPO 9" in name:
            letra = "A"
        elif "GRUPO 21" in name:
            letra = "B"
        elif "GRUPO 32" in name:
            letra = "C"
        elif "GRUPO 28" in name:
            letra = "D"
    elif cat == "ALEVIN":
        if "PREFERENTE" in name or "GRUPO 1" in name:
            letra = "A"
        else:
            letra = "B"
    elif cat == "ALEVIN F7":
        if "FEMENINO" in name:
            if "SUBGRUPO 4 B" in name or "SEGUNDA FASE" in name:
                letra = "FEM B"
            else:
                letra = "FEM A"
        else:
            if "GRUPO 16" in name:
                letra = "B"
            else:
                letra = "A"
    elif cat == "BENJAMIN":
        if "AUTONOMICA" in name or "AUTONÓMICA" in name:
            if "CUADRANGULAR 1" in name or "T. CAMPEONES" in name or "TRIANGULAR" in name:
                letra = "T2"
            else:
                letra = "A"
        elif "GRUPO 22 (GRUPO A)" in name or "GRUPO 22(A)" in name or "GRUPO A" in name:
            letra = "B"
        elif "GRUPO 22 (GRUPO B)" in name or "GRUPO 22(B)" in name or "GRUPO B" in name:
            letra = "C"
        elif "GRUPO 22 (GRUPO C)" in name or "GRUPO 22(C)" in name or "GRUPO C" in name:
            letra = "D"
        elif "TRIANGULAR 10" in name or "TRIANGULAR" in name:
            letra = "T1"
        else:
            letra = "A"
    elif cat == "PREBENJAMIN":
        if "PREFERENTE" in name:
            if "SUBGRUPO 5 A" in name or "SEGUNDA FASE" in name:
                letra = "A F2"
            else:
                letra = "A"
        elif "GRUPO 18" in name:
            if "SUBGRUPO 18 B" in name or "SEGUNDA FASE" in name:
                if "B F2" in name or "SUBGRUPO 18 B" in name:
                    letra = "B F2"
                else:
                    letra = "C F2"
            elif "GRUPO A" in name or "(GRUPO A)" in name:
                letra = "B"
            elif "GRUPO B" in name or "(GRUPO B)" in name:
                letra = "C"
            else:
                letra = "A"
        elif "SUBGRUPO 18 B" in name:
            letra = "B F2"
        elif "SUBGRUPO 18 A" in name:
            letra = "C F2"
            
    return cat, letra

def generate_opponents_for_category(categoria, letra):
    base_names = [
        "COLMENAR VIEJO", "REAL DE MANZANARES", "MASRIVER", "UNION EUROPA SANSE",
        "ATLETICO DEL PILAR", "UNION ZONA NORTE", "SIETE PICOS", "EL PARDO",
        "GUADALIX DE LA SIERRA", "RUPE SAHAGUN", "JUVENTUD SANSE", "MECO",
        "SPORTING SEIS DE DICIEMBRE", "FOMENTO ALUMNI", "SAN AGUSTIN DE GUADALIX",
        "ESPANYOL DE MADRID", "NUEVO BOADILLA", "PEÑAGRANDE", "MIRAMONTE MADRID",
        "VIRGEN DE MIRASIERRA", "BOCA", "FUNDACION ADF", "INTER DEL PILAR",
        "UNION ADARVE", "RAYO DEL PILAR", "ARROYOFRESNO", "LACOMA"
    ]
    
    suffix = ""
    if categoria == "AFICIONADO":
        suffix = f" \"{letra}\""
    else:
        suffix = f" {categoria} \"{letra}\""
        
    opponents = []
    for name in base_names:
        opponents.append(f"C.D. {name}{suffix}")
            
    return opponents


def enrich_calendar_with_shields(calendar_entries, clasificacion):
    """Añade el campo rival_shield a cada partido del calendario,
    buscando la URL del escudo del rival en los datos de clasificación."""
    if not clasificacion:
        return calendar_entries

    # Construir un mapa de nombre limpio -> shield URL con varias estrategias de matching
    shield_lookup = {}
    for item in clasificacion:
        raw = item.get('equipo', '')
        shield_url = item.get('shield', '')
        if not shield_url:
            continue

        # 1. Nombre completo sin comillas, en mayúsculas
        clean_full = raw.replace('"', '').replace("'", '').strip().upper()
        shield_lookup[clean_full] = shield_url

        # 2. Sin letra final (ej: "C.D. RUPE SAHAGUN A" -> "C.D. RUPE SAHAGUN")
        without_letter = re.sub(r'\s+[A-E]$', '', clean_full).strip()
        if without_letter not in shield_lookup:
            shield_lookup[without_letter] = shield_url

        # 3. Sin prefijo clúsico (sin C.D., A.D., etc.)
        keywords = re.sub(r'^(C\.D\.|A\.D\.|C\.F\.|S\.A\.D\.|R\.C\.|R\.C\.D\.|U\.D\.|ESC\.FUT\.|A\.D\.C\.)\s*', '', clean_full).strip()
        keywords_no_letter = re.sub(r'\s+[A-E]$', '', keywords).strip()
        if keywords not in shield_lookup:
            shield_lookup[keywords] = shield_url
        if keywords_no_letter not in shield_lookup:
            shield_lookup[keywords_no_letter] = shield_url

    def find_shield(rival_raw):
        rival_clean = rival_raw.replace('"', '').replace("'", '').strip().upper()
        # Búsqueda directa
        if rival_clean in shield_lookup:
            return shield_lookup[rival_clean]
        # Sin letra final
        without_letter = re.sub(r'\s+[A-E]$', '', rival_clean).strip()
        if without_letter in shield_lookup:
            return shield_lookup[without_letter]
        # Sin prefijo
        keywords = re.sub(r'^(C\.D\.|A\.D\.|C\.F\.|S\.A\.D\.|R\.C\.|R\.C\.D\.|U\.D\.|ESC\.FUT\.|A\.D\.C\.)\s*', '', rival_clean).strip()
        keywords_no_letter = re.sub(r'\s+[A-E]$', '', keywords).strip()
        if keywords in shield_lookup:
            return shield_lookup[keywords]
        if keywords_no_letter in shield_lookup:
            return shield_lookup[keywords_no_letter]
        # Búsqueda parcial (contención)
        for key, url in shield_lookup.items():
            if len(key) > 5 and (rival_clean.startswith(key) or key.startswith(rival_clean.split()[0] if rival_clean.split() else rival_clean)):
                return url
        return ''

    for entry in calendar_entries:
        if not entry.get('rival_shield'):
            entry['rival_shield'] = find_shield(entry.get('rival', ''))

    return calendar_entries

def sync_rffm(username, password):
    """Inicia la sincronización completa desde la RFFM"""
    url_login = "https://intranet.ffmadrid.es/nfg/NLogin"
    url_portada = "https://intranet.ffmadrid.es/nfg/NPortada"
    
    session = get_session()
    payload = {
        "NUser": username,
        "NPass": password,
        "LoginAjax": "1"
    }
    
    try:
        # 1. Iniciar sesión
        res_login = session.post(url_login, data=payload)
        match_est = re.search(r'var estado="(\d+)"', res_login.text)
        estado = match_est.group(1) if match_est else "2"
        
        if estado != "1":
            return False, "Credenciales incorrectas o error de acceso."
            
        # 2. Cargar Portada
        res_portada = session.get(url_portada)
        soup = BeautifulSoup(res_portada.text, 'html.parser')
        
        # Buscar la tabla de equipos en competición
        tablas = soup.find_all('table')
        tabla_comp = None
        for t in tablas:
            headers = [th.text.upper() for th in t.find_all('th')]
            if any("POSICION" in h or "POSICIÓN" in h or "ULT.JORNADA" in h for h in headers):
                tabla_comp = t
                break
                
        if not tabla_comp:
            return False, "No se encontró la tabla de equipos en la portada de la RFFM."
            
        # 3. Procesar las filas de la tabla
        equipos_data = []
        rows = tabla_comp.find_all('tr')[1:] # Omitir cabecera
        for r in rows:
            cols = r.find_all('td')
            if len(cols) < 4:
                continue
                
            # Nombre de competición / grupo
            comp_name = cols[0].text.strip()
            
            # Detectar categoría y letra, e ignorar Copa
            cat, letra = detect_categoria_y_letra(comp_name)
            if not cat:
                continue
                
            puntos = cols[1].text.strip()
            posicion = cols[2].text.strip()
            
            # Buscar el enlace de "Ult.Jornada" y extraer parámetros de todos los enlaces de la fila
            link_jornada = None
            codgrupo = None
            codequipo = None
            cod_primaria = '1000131'  # Valor por defecto FFMADRID
            links = r.find_all('a')
            for a in links:
                href = a.get('href', '')
                m_cg = re.search(r'cod_?grupo=(\d+)', href, re.I)
                m_ce = re.search(r'(?:codequipo|codigo_equipo)=(\d+)', href, re.I)
                m_cp = re.search(r'cod_primaria=(\d+)', href, re.I)
                if m_cg and not codgrupo:
                    codgrupo = m_cg.group(1)
                if m_ce and not codequipo:
                    codequipo = m_ce.group(1)
                if m_cp and cod_primaria == '1000131':
                    cod_primaria = m_cp.group(1)
            
            for a in links:
                href = a.get('href', '')
                if 'LstJornada' in href or 'Jornada' in href or 'cod_grupo' in href or 'codgrupo' in href or 'CmpJornada' in href:
                    link_jornada = href
                    break
                    
            if not link_jornada:
                equipos_data.append({
                    "nombre": comp_name,
                    "categoria": cat,
                    "letra": letra,
                    "puntos": puntos,
                    "posicion": posicion,
                    "ultimo_partido": None,
                    "clasificacion": [],
                    "calendario": [],
                    "calendario_grupo": []
                })
                continue
                
            # Resolver URL absoluta del enlace
            link_jornada = urljoin(url_portada, link_jornada)
                
            # 4. Navegar al detalle de la jornada
            res_jornada = session.get(link_jornada)
            soup_jornada = BeautifulSoup(res_jornada.text, 'html.parser')
            
            # Intentar extraer codgrupo/codequipo desde la página si no los tenemos
            if not codgrupo:
                for a in soup_jornada.find_all('a'):
                    href = a.get('href', '')
                    m_cg = re.search(r'codgrupo=(\d+)', href)
                    m_ce = re.search(r'codequipo=(\d+)', href)
                    m_cp = re.search(r'cod_primaria=(\d+)', href)
                    if m_cg:
                        codgrupo = m_cg.group(1)
                        if m_ce: codequipo = m_ce.group(1)
                        if m_cp: cod_primaria = m_cp.group(1)
                        break
            
            # Buscar partido de FUENTELARREYNA en los resultados
            ultimo_partido = None
            partidos_tablas = soup_jornada.find_all('table')
            tabla_partidos = None
            for pt in partidos_tablas:
                if "RESULTADOS" in pt.text.upper() or any("CLUB FUENTELARREYNA" in cell.text.upper() for cell in pt.find_all('td')):
                    tabla_partidos = pt
                    break
                    
            jornada_title = "Última Jornada"
            if tabla_partidos:
                th_el = tabla_partidos.find('th')
                if th_el:
                    jornada_title = th_el.text.strip()
            else:
                title_el = soup_jornada.find(class_=re.compile("titulo|header|jornada", re.I))
                if title_el:
                    jornada_title = title_el.text.strip()
                    if len(jornada_title) > 50:
                        jornada_title = "Última Jornada"
                
            rows_partidos = []
            if tabla_partidos:
                rows_partidos = tabla_partidos.find_all('tr')
            else:
                rows_partidos = soup_jornada.find_all('tr')

            # Recopilar todos los escudos de los equipos del grupo desde la jornada
            group_shields = {}
            for pr in rows_partidos:
                cells = pr.find_all('td')
                if len(cells) >= 3:
                    local_name = cells[0].text.strip().replace('"', '').replace("'", '').strip().upper()
                    visit_name = cells[2].text.strip().replace('"', '').replace("'", '').strip().upper()
                    img_loc = cells[0].find('img')
                    img_vis = cells[2].find('img')
                    if img_loc and img_loc.get('src'):
                        group_shields[local_name] = urljoin(link_jornada, img_loc.get('src'))
                    if img_vis and img_vis.get('src'):
                        group_shields[visit_name] = urljoin(link_jornada, img_vis.get('src'))
                
            for pr in rows_partidos:
                cells = pr.find_all('td')
                if len(cells) >= 3:
                    text_row = pr.text.upper()
                    if "FUENTELARREYNA" in text_row:
                        eq_local = cells[0].text.strip()
                        resultado_raw = cells[1].text.strip()
                        eq_visitante = cells[2].text.strip()
                        
                        # Extraer escudos del partido y descargarlos localmente
                        img_loc = cells[0].find('img')
                        img_vis = cells[2].find('img')
                        shield_loc = ""
                        shield_vis = ""
                        if img_loc:
                            shield_loc = img_loc.get('src', '')
                            if shield_loc:
                                shield_loc = urljoin(link_jornada, shield_loc)
                                cid = extract_shield_id(shield_loc)
                                if cid:
                                    local_sh = download_shield(shield_loc, cid, auth_session=session)
                                    if local_sh:
                                        shield_loc = local_sh
                        if img_vis:
                            shield_vis = img_vis.get('src', '')
                            if shield_vis:
                                shield_vis = urljoin(link_jornada, shield_vis)
                                cid = extract_shield_id(shield_vis)
                                if cid:
                                    local_sh = download_shield(shield_vis, cid, auth_session=session)
                                    if local_sh:
                                        shield_vis = local_sh
                        
                        goles_fav, goles_contra = 0, 0
                        es_local = "FUENTELARREYNA" in eq_local.upper()
                        contrario = eq_visitante if es_local else eq_local
                        
                        # Parsear resultado
                        clean_resultado = "-"
                        match_res = re.search(r'(\d+)\s*[-–]\s*(\d+)', resultado_raw)
                        if match_res:
                            g_loc = int(match_res.group(1))
                            g_vis = int(match_res.group(2))
                            goles_fav = g_loc if es_local else g_vis
                            goles_contra = g_vis if es_local else g_loc
                            clean_resultado = f"{g_loc} - {g_vis}"
                            
                        # Buscar fecha si está en la celda
                        fecha_match = "Finalizado"
                        m_date = re.search(r'\d{2}[-/]\d{2}[-/]\d{4}', resultado_raw)
                        if m_date:
                            fecha_match = m_date.group(0)
                            m_time = re.search(r'\d{2}:\d{2}', resultado_raw)
                            if m_time:
                                fecha_match += " " + m_time.group(0)
                        else:
                            for cell in cells:
                                date_match = re.search(r'\d{2}[-/]\d{2}[-/]\d{4}', cell.text)
                                if date_match:
                                    fecha_match = date_match.group(0)
                                    time_match = re.search(r'\d{2}:\d{2}', cell.text)
                                    if time_match:
                                        fecha_match += " " + time_match.group(0)
                                    break
                                
                        ultimo_partido = {
                            "jornada": jornada_title,
                            "fecha": fecha_match,
                            "local": eq_local,
                            "visitante": eq_visitante,
                            "local_shield": shield_loc,
                            "visitante_shield": shield_vis,
                            "resultado": clean_resultado,
                            "contrario": contrario,
                            "goles_favor": goles_fav,
                            "goles_contra": goles_contra,
                            "es_local": es_local
                        }
                        break
                        
            # 5. Navegar a la clasificación del grupo
            link_clasif = None
            for a in soup_jornada.find_all('a'):
                href = a.get('href', '')
                if 'LstClasificacion' in href or 'Clasificacion' in href:
                    link_clasif = href
                    break
                    
            clasif_data = []
            if link_clasif:
                link_clasif = urljoin(link_jornada, link_clasif)
                    
                res_clasif = session.get(link_clasif)
                soup_clasif = BeautifulSoup(res_clasif.text, 'html.parser')
                
                # Buscar tabla de clasificación
                tabla_cl = None
                for t in soup_clasif.find_all('table'):
                    headers = [th.text.upper() for th in t.find_all('th')]
                    # Evitar la tabla resumida (casa/fuera) buscando palabras como CASA o FUERA
                    if any("CASA" in h or "FUERA" in h for h in headers):
                        continue
                    if any("PTS" in h or "PUNTOS" in h or "JUG" in h or "PARTIDOS" in h or "PTS/PJ" in h for h in headers):
                        tabla_cl = t
                        break
                        
                if tabla_cl:
                    # Detectar si hay columna de Pts/PJ analizando los encabezados
                    headers_cl_texts = [th.text.upper() for th in tabla_cl.find_all('th')]
                    has_pts_pj = any("PTS/PJ" in h or "PTS./PJ" in h or "PTS / PJ" in h for h in headers_cl_texts)
                    
                    # Ajustar índices según presencia de Pts/PJ
                    if has_pts_pj:
                        pts_idx = 4
                        j_idx = 5
                        g_idx = 6
                        e_idx = 7
                        p_idx = 8
                        gf_idx = 9
                        gc_idx = 10
                        ultimos_idx = 11
                    else:
                        pts_idx = 3
                        j_idx = 4
                        g_idx = 5
                        e_idx = 6
                        p_idx = 7
                        gf_idx = 8
                        gc_idx = 9
                        ultimos_idx = 10
                        
                    rows_cl = tabla_cl.find_all('tr')
                    for r_cl in rows_cl:
                        cells_cl = r_cl.find_all('td')
                        required_len = 12 if has_pts_pj else 11
                        if len(cells_cl) >= required_len:
                            pos = cells_cl[1].text.strip()
                            team = cells_cl[2].text.strip()
                            pts = cells_cl[pts_idx].text.strip()
                            jugados = cells_cl[j_idx].text.strip()
                            
                            ganados = cells_cl[g_idx].text.strip()
                            empatados = cells_cl[e_idx].text.strip()
                            perdidos = cells_cl[p_idx].text.strip()
                            gf = cells_cl[gf_idx].text.strip()
                            gc = cells_cl[gc_idx].text.strip()
                            
                            # Extraer columna Últimos
                            ultimos = [s.text.strip().upper() for s in cells_cl[ultimos_idx].find_all('span') if s.text.strip()]
                            if not ultimos:
                                ultimos_raw = cells_cl[ultimos_idx].text.strip()
                                ultimos = [part.upper() for part in ultimos_raw.split() if part.upper() in ['G', 'E', 'P']]
                            
                            # Extraer escudo de la clasificación y descargarlo localmente
                            img_cl = cells_cl[2].find('img')
                            raw_shield = ""
                            if img_cl and img_cl.get('src'):
                                raw_shield = urljoin(link_clasif, img_cl.get('src'))
                            
                            # Fallback al mapa de escudos del grupo de la jornada
                            if not raw_shield:
                                clean_team_key = team.replace('"', '').replace("'", '').strip().upper()
                                raw_shield = group_shields.get(clean_team_key, '')
                                
                            shield_url = ""
                            if raw_shield:
                                cid = extract_shield_id(raw_shield)
                                if cid:
                                    local_sh = download_shield(raw_shield, cid, auth_session=session)
                                    shield_url = local_sh if local_sh else raw_shield
                                else:
                                    shield_url = raw_shield
                            
                            clasif_data.append({
                                "pos": pos,
                                "equipo": team,
                                "puntos": pts,
                                "jugados": jugados,
                                "ganados": ganados,
                                "empatados": empatados,
                                "perdidos": perdidos,
                                "goles_favor": gf,
                                "goles_contra": gc,
                                "shield": shield_url,
                                "ultimos": ultimos
                            })

            # 6. Scraper del CALENDARIO COMPLETO
            calendario_completo = []
            calendario_grupo = []  # TODOS los partidos del grupo (no solo los de Fuentelarreyna), para poder
                                    # reconstruir clasificaciones históricas y el historial de cualquier rival
            link_cal = None
            for a in soup_jornada.find_all('a'):
                href = a.get('href', '')
                if 'VisCalendario_Vis' in href or a.text.strip().upper() == 'CALENDARIO':
                    link_cal = href
                    break
            
            url_cal = None
            if link_cal:
                url_cal = urljoin(link_jornada, link_cal)
            elif codgrupo and codcompeticion:
                url_cal = f"https://intranet.ffmadrid.es/nfg/NPcd/NFG_VisCalendario_Vis?cod_primaria={cod_primaria}&codtemporada=21&codcompeticion={codcompeticion}&codgrupo={codgrupo}"
                
            if url_cal:
                # Asegurar CDetalle=1 en la URL del calendario para expandir todas las jornadas
                if "CDetalle=" not in url_cal:
                    if "?" in url_cal:
                        url_cal += "&CDetalle=1"
                    else:
                        url_cal += "?CDetalle=1"
                else:
                    url_cal = re.sub(r'CDetalle=[^&]*', 'CDetalle=1', url_cal)

                try:
                    res_cal = session.get(url_cal)
                    soup_cal = BeautifulSoup(res_cal.text, 'html.parser')

                    # Construir mapa de escudos desde la clasificación ya extraída
                    escudo_map = {}
                    for item in clasif_data:
                        clean_name = item['equipo'].replace('"', '').replace("'", '').strip().upper()
                        if item.get('shield'):
                            escudo_map[clean_name] = item['shield']

                    # Buscar todos los bloques de jornada y sus partidos
                    seen_matches = set()
                    seen_matches_grupo = set()
                    for t in soup_cal.find_all('table'):
                        th_el = t.find('th')
                        if th_el:
                            jornada_header = th_el.text.strip()
                            if "JORNADA" in jornada_header.upper():
                                # Limpiar espacios en blanco
                                jornada_actual = " ".join(jornada_header.split())
                                # Extraer la fecha del título de la jornada
                                fecha = ""
                                m_date = re.search(r'\d{2}[-/]\d{2}[-/]\d{4}', jornada_actual)
                                if m_date:
                                    fecha = m_date.group(0)
                                    
                                # Limpiar cabecera para quedarnos solo con "Jornada X"
                                m_jn = re.search(r'Jornada\s+\d+', jornada_actual, re.I)
                                if m_jn:
                                    jornada_actual = m_jn.group(0)
                                    
                                # Procesar los partidos de esta jornada
                                for r_cal in t.find_all('tr'):
                                    cells = r_cal.find_all('td')
                                    if len(cells) >= 5:
                                        local_raw = cells[0].text.strip()
                                        visit_raw = cells[4].text.strip()

                                        # Guardamos TODOS los partidos del grupo (no solo los nuestros), para poder
                                        # reconstruir la clasificación de cualquier jornada y el historial de cualquier rival
                                        local_g = " ".join(local_raw.split())
                                        visit_g = " ".join(visit_raw.split())
                                        if local_g and visit_g:
                                            score_l_g = cells[1].text.strip()
                                            score_v_g = cells[3].text.strip()
                                            resultado_g = ""
                                            if score_l_g.isdigit() and score_v_g.isdigit():
                                                resultado_g = f"{int(score_l_g)} - {int(score_v_g)}"
                                            clave_grupo = (jornada_actual, local_g, visit_g)
                                            if clave_grupo not in seen_matches_grupo:
                                                seen_matches_grupo.add(clave_grupo)
                                                calendario_grupo.append({
                                                    "jornada": jornada_actual,
                                                    "fecha": fecha,
                                                    "local": local_g,
                                                    "visitante": visit_g,
                                                    "resultado": resultado_g
                                                })

                                        if "FUENTELARREYNA" in local_raw.upper() or "FUENTELARREYNA" in visit_raw.upper():
                                            es_local = "FUENTELARREYNA" in local_raw.upper()
                                            rival = visit_raw if es_local else local_raw
                                            rival = " ".join(rival.split())
                                            
                                            match_key = (jornada_actual, rival)
                                            if match_key in seen_matches:
                                                continue
                                            seen_matches.add(match_key)
                                            
                                            score_l = cells[1].text.strip()
                                            score_v = cells[3].text.strip()
                                            
                                            resultado = ""
                                            estado = ""
                                            if score_l.isdigit() and score_v.isdigit():
                                                g1 = int(score_l)
                                                g2 = int(score_v)
                                                resultado = f"{g1} - {g2}"
                                                if es_local:
                                                    estado = 'G' if g1 > g2 else ('E' if g1 == g2 else 'P')
                                                else:
                                                    estado = 'G' if g2 > g1 else ('E' if g1 == g2 else 'P')
                                                    
                                            # Escudo del rival
                                            rival_clean = rival.replace('"', '').replace("'", '').strip().upper()
                                            rival_shield = escudo_map.get(rival_clean, '')
                                            
                                            # Búsqueda difusa de escudo
                                            if not rival_shield:
                                                rival_keywords = re.sub(r'^(C\.D\.|A\.D\.|C\.F\.|A\.D\.C\.|S\.A\.D\.|R\.C\.|R\.C\.D\.|U\.D\.|ESC\.FUT\.)\s*', '', rival_clean, flags=re.I).strip()
                                                rival_keywords_base = rival_keywords.split()[0] if rival_keywords.split() else rival_keywords
                                                for k_name, sh_url in escudo_map.items():
                                                    if rival_keywords in k_name or k_name in rival_keywords or k_name.startswith(rival_keywords_base):
                                                        rival_shield = sh_url
                                                        break
                                            
                                            # Intentar obtener el img de la celda del rival y descargar escudo
                                            rival_cell = cells[4] if es_local else cells[0]
                                            img_rival = rival_cell.find('img')
                                            if img_rival and not rival_shield:
                                                rival_shield = img_rival.get('src', '')
                                                if rival_shield:
                                                    rival_shield = urljoin(url_cal, rival_shield)
                                                    
                                            if rival_shield:
                                                cid = extract_shield_id(rival_shield)
                                                if cid:
                                                    local_sh = download_shield(rival_shield, cid, auth_session=session)
                                                    if local_sh:
                                                        rival_shield = local_sh
                                                    
                                            calendario_completo.append({
                                                "jornada": jornada_actual,
                                                "fecha": fecha,
                                                "rival": rival,
                                                "es_local": es_local,
                                                "resultado": resultado,
                                                "estado": estado,
                                                "rival_shield": rival_shield
                                            })
                except Exception as e_cal:
                    print(f"Aviso: no se pudo scraper el calendario completo: {e_cal}")

            # Si no obtuvimos calendario completo, usar demo enriquecido con escudos
            if not calendario_completo:
                calendario_completo = generate_calendario_demo(comp_name, clasif_data)
                calendario_completo = enrich_calendar_with_shields(calendario_completo, clasif_data)

            equipos_data.append({
                "nombre": comp_name,
                "categoria": cat,
                "letra": letra,
                "puntos": puntos,
                "posicion": posicion,
                "ultimo_partido": ultimo_partido,
                "clasificacion": clasif_data,
                "calendario": calendario_completo,
                "calendario_grupo": calendario_grupo
            })
            
        # 6. Deduplicar filas exactamente repetidas (la portada de la RFFM a veces
        # lista el mismo equipo/grupo más de una vez) antes de guardar en caché.
        equipos_unicos = []
        vistos = set()
        for eq in equipos_data:
            clave = (eq.get("categoria"), eq.get("letra"), eq.get("nombre"))
            if clave in vistos:
                continue
            vistos.add(clave)
            equipos_unicos.append(eq)
        equipos_data = equipos_unicos

        # 7. Guardar en caché local
        cache_data = {
            "status": "success",
            "last_updated": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "equipos": equipos_data
        }
        
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=4)
            
        return True, "Sincronización completada con éxito."
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f"Error durante la sincronización: {str(e)}"

def generate_full_clasificacion(nombre_comp, pos_fuentelarreyna, pts_fuentelarreyna):
    comp_upper = nombre_comp.upper().strip()
    
    # 1. Caso especial: Primera Aficionado A (de la Imagen 2)
    if "PRIMERA AFICIONADO" in comp_upper and "GRUPO 2" in comp_upper:
        return [
            {"pos": "1", "equipo": "A.D. COLMENAR VIEJO \"B\"", "puntos": "86", "jugados": "34", "ganados": "27", "empatados": "5", "perdidos": "2", "goles_favor": "123", "goles_contra": "36", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1009.png"},
            {"pos": "2", "equipo": "CLUB FUENTELARREYNA \"A\"", "puntos": "74", "jugados": "34", "ganados": "23", "empatados": "5", "perdidos": "6", "goles_favor": "89", "goles_contra": "50", "shield": "/static/ESCUDO SIN FONDO.png"},
            {"pos": "3", "equipo": "A.D. EL REAL DE MANZANARES", "puntos": "69", "jugados": "34", "ganados": "21", "empatados": "6", "perdidos": "7", "goles_favor": "76", "goles_contra": "49", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1120.png"},
            {"pos": "4", "equipo": "C.D. MASRIVER \"A\"", "puntos": "64", "jugados": "34", "ganados": "19", "empatados": "7", "perdidos": "8", "goles_favor": "77", "goles_contra": "54", "shield": "https://www.ffmadrid.es/rffm/escudos/club_30302.png"},
            {"pos": "5", "equipo": "C.D. UNION EUROPA SANSE \"A\"", "puntos": "56", "jugados": "34", "ganados": "16", "empatados": "8", "perdidos": "10", "goles_favor": "73", "goles_contra": "61", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1537.png"},
            {"pos": "6", "equipo": "ATLETICO DEL PILAR C.F. \"A\"", "puntos": "50", "jugados": "34", "ganados": "15", "empatados": "5", "perdidos": "14", "goles_favor": "84", "goles_contra": "73", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1538.png"},
            {"pos": "7", "equipo": "UNION ZONA NORTE \"A\"", "puntos": "50", "jugados": "34", "ganados": "15", "empatados": "5", "perdidos": "14", "goles_favor": "73", "goles_contra": "78", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1215.png"},
            {"pos": "8", "equipo": "ESC.FUT. SIETE PICOS COLMENAR", "puntos": "50", "jugados": "34", "ganados": "15", "empatados": "5", "perdidos": "14", "goles_favor": "70", "goles_contra": "70", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1427.png"},
            {"pos": "9", "equipo": "A.D. EL PARDO \"A\"", "puntos": "49", "jugados": "34", "ganados": "14", "empatados": "7", "perdidos": "13", "goles_favor": "68", "goles_contra": "57", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1042.png"},
            {"pos": "10", "equipo": "C.D. GUADALIX DE LA SIERRA", "puntos": "49", "jugados": "34", "ganados": "14", "empatados": "7", "perdidos": "13", "goles_favor": "63", "goles_contra": "64", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1052.png"},
            {"pos": "11", "equipo": "C.D. RUPE SAHAGUN \"A\"", "puntos": "47", "jugados": "34", "ganados": "13", "empatados": "8", "perdidos": "13", "goles_favor": "77", "goles_contra": "79", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1125.png"},
            {"pos": "12", "equipo": "JUVENTUD SANSE \"B\"", "puntos": "43", "jugados": "34", "ganados": "12", "empatados": "7", "perdidos": "15", "goles_favor": "67", "goles_contra": "82", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1394.png"},
            {"pos": "13", "equipo": "C.D. MECO", "puntos": "42", "jugados": "34", "ganados": "12", "empatados": "6", "perdidos": "16", "goles_favor": "61", "goles_contra": "61", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1123.png"},
            {"pos": "14", "equipo": "SPORTING SEIS DE DICIEMBRE", "puntos": "36", "jugados": "34", "ganados": "10", "empatados": "6", "perdidos": "18", "goles_favor": "47", "goles_contra": "67", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1422.png"},
            {"pos": "15", "equipo": "S.A.D. FOMENTO ALUMNI \"A\"", "puntos": "35", "jugados": "34", "ganados": "10", "empatados": "5", "perdidos": "19", "goles_favor": "65", "goles_contra": "80", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1423.png"},
            {"pos": "16", "equipo": "C.F. SAN AGUSTIN DE GUADALIX \"B\"", "puntos": "30", "jugados": "34", "ganados": "9", "empatados": "3", "perdidos": "22", "goles_favor": "58", "goles_contra": "79", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1086.png"},
            {"pos": "17", "equipo": "R.C.D. ESPANYOL DE MADRID", "puntos": "26", "jugados": "34", "ganados": "7", "empatados": "5", "perdidos": "22", "goles_favor": "40", "goles_contra": "82", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1054.png"},
            {"pos": "18", "equipo": "C.D. NUEVO BOADILLA \"C\"", "puntos": "6", "jugados": "34", "ganados": "1", "empatados": "3", "perdidos": "30", "goles_favor": "33", "goles_contra": "141", "shield": "https://www.ffmadrid.es/rffm/escudos/club_1311.png"}
        ]

    # 2. Caso especial: Segunda Aficionado (de la Ficha Aficionado B)
    if "SEGUNDA AFICIONADO" in comp_upper and "14" in comp_upper:
        opponents = [
            "C.D. PEÑAGRANDE", "C.D.E. MIRAMONTE MADRID \"B\"", "IE UNIVERSITY ATHLETICS",
            "C.D.E. RACING DE MOSTOLES", "C.D. MASRIVER \"B\"", "A.D. EL PARDO \"B\"",
            "S.A.D. CODEC DE MADRID", "C.D. VIRGEN DE MIRASIERRA", "C.D. BOCA",
            "FUNDACION ADF \"B\"", "R.C. INTER DEL PILAR", "A.D. UNION ADARVE \"D\"",
            "RAYO DEL PILAR C.F.", "U.D. ARROYOFRESNO \"A\"", "A.D.C. LACOMA"
        ]
        fuentelarreyna_name = "CLUB FUENTELARREYNA \"B\""
    else:
        # Generar oponentes de la categoría para no cruzar información
        cat, letra = detect_categoria_y_letra(nombre_comp)
        opponents = generate_opponents_for_category(cat, letra)
        
        suffix = f" \"{letra}\"" if letra in ["A", "B", "C", "D", "E"] else ""
        fuentelarreyna_name = f"CLUB FUENTELARREYNA{suffix}"

    opponents = [op for op in opponents if "FUENTELARREYNA" not in op.upper()]
    opp_teams = opponents[:15]
    
    pos_idx = int(pos_fuentelarreyna) - 1
    if pos_idx < 0: pos_idx = 0
    if pos_idx >= 16: pos_idx = 15
        
    full_teams = []
    for i in range(16):
        if i == pos_idx:
            full_teams.append(fuentelarreyna_name)
        else:
            if opp_teams:
                full_teams.append(opp_teams.pop(0))
            else:
                full_teams.append(f"C.D. RIVAL GENERICO {i+1}")
                
    pj = 34
    clasif_list = []
    for rank_idx, team_name in enumerate(full_teams):
        pos_str = str(rank_idx + 1)
        if rank_idx == pos_idx:
            pts = int(pts_fuentelarreyna)
        else:
            if rank_idx < pos_idx:
                pts = int(pts_fuentelarreyna) + (pos_idx - rank_idx) * 2 + 1
            else:
                pts = max(0, int(pts_fuentelarreyna) - (rank_idx - pos_idx) * 2 - 1)
        
        pg = pts // 3
        pe = pts % 3
        if pg + pe > pj:
            pj_actual = pg + pe
        else:
            pj_actual = pj
        pp = pj_actual - pg - pe
        
        gf = pg * 2 + pe + rank_idx
        gc = pp * 2 + pe + (16 - rank_idx)
        
        actual_pos = pos_fuentelarreyna if rank_idx == pos_idx else pos_str
        
        # En demo mode, se puede generar una URL de escudo de RFFM simulada
        if "FUENTELARREYNA" in team_name.upper():
            shield_url = "/static/ESCUDO SIN FONDO.png"
        else:
            simulated_id = 1000 + rank_idx * 73
            shield_url = f"https://www.ffmadrid.es/rffm/escudos/club_{simulated_id}.png"

        clasif_list.append({
            "pos": actual_pos,
            "equipo": team_name,
            "puntos": str(pts),
            "jugados": str(pj_actual),
            "ganados": str(pg),
            "empatados": str(pe),
            "perdidos": str(pp),
            "goles_favor": str(gf),
            "goles_contra": str(gc),
            "shield": shield_url,
            "ultimos": ["G", "G", "E", "P", "G"]
        })
        
    try:
        clasif_list.sort(key=lambda x: int(x['pos']))
    except:
        pass
    return clasif_list

def generate_calendario_demo(nombre_comp, clasificacion):
    if "PRIMERA AFICIONADO" in nombre_comp.upper():
        return [
            {"jornada": "Jornada 1", "fecha": "14/09/2025", "rival": "JUVENTUD SANSE \"B\"", "es_local": True, "resultado": "5 - 2", "estado": "G"},
            {"jornada": "Jornada 2", "fecha": "21/09/2025", "rival": "A.D. COLMENAR VIEJO \"B\"", "es_local": False, "resultado": "1 - 1", "estado": "E"},
            {"jornada": "Jornada 3", "fecha": "28/09/2025", "rival": "ATLETICO DEL PILAR C.F. \"A\"", "es_local": True, "resultado": "2 - 5", "estado": "P"},
            {"jornada": "Jornada 4", "fecha": "05/10/2025", "rival": "S.A.D. FOMENTO ALUMNI \"A\"", "es_local": False, "resultado": "0 - 2", "estado": "G"},
            {"jornada": "Jornada 5", "fecha": "12/10/2025", "rival": "UNION ZONA NORTE \"A\"", "es_local": False, "resultado": "2 - 3", "estado": "G"},
            {"jornada": "Jornada 6", "fecha": "19/10/2025", "rival": "A.D. EL PARDO \"A\"", "es_local": True, "resultado": "3 - 1", "estado": "G"},
            {"jornada": "Jornada 7", "fecha": "26/10/2025", "rival": "C.D. MECO", "es_local": False, "resultado": "1 - 1", "estado": "E"},
            {"jornada": "Jornada 8", "fecha": "02/11/2025", "rival": "C.D. MASRIVER \"A\"", "es_local": True, "resultado": "2 - 0", "estado": "G"},
            {"jornada": "Jornada 9", "fecha": "09/11/2025", "rival": "ESC.FUT. SIETE PICOS COLMENAR", "es_local": False, "resultado": "0 - 0", "estado": "E"},
            {"jornada": "Jornada 10", "fecha": "16/11/2025", "rival": "SPORTING SEIS DE DICIEMBRE", "es_local": True, "resultado": "2 - 0", "estado": "G"},
            {"jornada": "Jornada 11", "fecha": "23/11/2025", "rival": "C.D. NUEVO BOADILLA \"C\"", "es_local": False, "resultado": "0 - 3", "estado": "G"},
            {"jornada": "Jornada 12", "fecha": "30/11/2025", "rival": "C.D. UNION EUROPA SANSE \"A\"", "es_local": True, "resultado": "1 - 0", "estado": "G"},
            {"jornada": "Jornada 13", "fecha": "14/12/2025", "rival": "C.D. GUADALIX DE LA SIERRA", "es_local": False, "resultado": "2 - 0", "estado": "P"},
            {"jornada": "Jornada 14", "fecha": "21/12/2025", "rival": "R.C.D. ESPANYOL DE MADRID", "es_local": True, "resultado": "4 - 1", "estado": "G"},
            {"jornada": "Jornada 15", "fecha": "11/01/2026", "rival": "A.D. EL REAL DE MANZANARES", "es_local": False, "resultado": "0 - 2", "estado": "G"},
            {"jornada": "Jornada 16", "fecha": "18/01/2026", "rival": "C.F. SAN AGUSTIN DE GUADALIX \"B\"", "es_local": True, "resultado": "4 - 1", "estado": "G"},
            {"jornada": "Jornada 17", "fecha": "25/01/2026", "rival": "C.D. RUPE SAHAGUN \"A\"", "es_local": False, "resultado": "1 - 4", "estado": "G"},
            {"jornada": "Jornada 18", "fecha": "01/02/2026", "rival": "JUVENTUD SANSE \"B\"", "es_local": False, "resultado": "0 - 4", "estado": "G"},
            {"jornada": "Jornada 19", "fecha": "08/02/2026", "rival": "A.D. COLMENAR VIEJO \"B\"", "es_local": True, "resultado": "3 - 3", "estado": "E"},
            {"jornada": "Jornada 20", "fecha": "15/02/2026", "rival": "ATLETICO DEL PILAR C.F. \"A\"", "es_local": False, "resultado": "2 - 2", "estado": "E"},
            {"jornada": "Jornada 21", "fecha": "22/02/2026", "rival": "S.A.D. FOMENTO ALUMNI \"A\"", "es_local": True, "resultado": "2 - 2", "estado": "E"},
            {"jornada": "Jornada 22", "fecha": "01/03/2026", "rival": "UNION ZONA NORTE \"A\"", "es_local": True, "resultado": "2 - 3", "estado": "P"},
            {"jornada": "Jornada 23", "fecha": "08/03/2026", "rival": "A.D. EL PARDO \"A\"", "es_local": False, "resultado": "3 - 5", "estado": "G"},
            {"jornada": "Jornada 24", "fecha": "15/03/2026", "rival": "C.D. MECO", "es_local": True, "resultado": "3 - 0", "estado": "G"},
            {"jornada": "Jornada 25", "fecha": "22/03/2026", "rival": "C.D. MASRIVER \"A\"", "es_local": False, "resultado": "2 - 0", "estado": "P"},
            {"jornada": "Jornada 26", "fecha": "29/03/2026", "rival": "ESC.FUT. SIETE PICOS COLMENAR", "es_local": True, "resultado": "2 - 0", "estado": "G"},
            {"jornada": "Jornada 27", "fecha": "12/04/2026", "rival": "SPORTING SEIS DE DICIEMBRE", "es_local": False, "resultado": "0 - 4", "estado": "G"},
            {"jornada": "Jornada 28", "fecha": "19/04/2026", "rival": "C.D. NUEVO BOADILLA \"C\"", "es_local": True, "resultado": "0 - 0", "estado": "E"},
            {"jornada": "Jornada 29", "fecha": "26/04/2026", "rival": "C.D. UNION EUROPA SANSE \"A\"", "es_local": False, "resultado": "0 - 1", "estado": "G"},
            {"jornada": "Jornada 30", "fecha": "10/05/2026", "rival": "C.D. GUADALIX DE LA SIERRA", "es_local": True, "resultado": "1 - 0", "estado": "G"},
            {"jornada": "Jornada 31", "fecha": "17/05/2026", "rival": "R.C.D. ESPANYOL DE MADRID", "es_local": False, "resultado": "1 - 3", "estado": "G"},
            {"jornada": "Jornada 32", "fecha": "24/05/2026", "rival": "A.D. EL REAL DE MANZANARES", "es_local": True, "resultado": "0 - 1", "estado": "P"},
            {"jornada": "Jornada 33", "fecha": "31/05/2026", "rival": "C.F. SAN AGUSTIN DE GUADALIX \"B\"", "es_local": False, "resultado": "1 - 4", "estado": "G"},
            {"jornada": "Jornada 34", "fecha": "07/06/2026", "rival": "C.D. RUPE SAHAGUN \"A\"", "es_local": True, "resultado": "1 - 2", "estado": "P"}
        ]

    if "SEGUNDA AFICIONADO" in nombre_comp.upper() and "14" in nombre_comp:
        return [
            {"jornada": "Jornada 1", "fecha": "28/09/2025", "rival": "C.D. PEÑAGRANDE", "es_local": False, "resultado": "1 - 2", "estado": "G"},
            {"jornada": "Jornada 2", "fecha": "05/10/2025", "rival": "C.D.E. MIRAMONTE MADRID \"B\"", "es_local": True, "resultado": "0 - 3", "estado": "P"},
            {"jornada": "Jornada 3", "fecha": "12/10/2025", "rival": "IE UNIVERSITY ATHLETICS", "es_local": False, "resultado": "3 - 2", "estado": "P"},
            {"jornada": "Jornada 4", "fecha": "19/10/2025", "rival": "C.D.E. RACING DE MOSTOLES", "es_local": True, "resultado": "2 - 1", "estado": "G"},
            {"jornada": "Jornada 5", "fecha": "26/10/2025", "rival": "C.D. MASRIVER \"B\"", "es_local": True, "resultado": "2 - 4", "estado": "P"},
            {"jornada": "Jornada 6", "fecha": "02/11/2025", "rival": "A.D. EL PARDO \"B\"", "es_local": False, "resultado": "1 - 2", "estado": "G"},
            {"jornada": "Jornada 7", "fecha": "09/11/2025", "rival": "S.A.D. CODEC DE MADRID", "es_local": False, "resultado": "4 - 3", "estado": "P"},
            {"jornada": "Jornada 8", "fecha": "16/11/2025", "rival": "C.D. VIRGEN DE MIRASIERRA", "es_local": False, "resultado": "5 - 0", "estado": "P"},
            {"jornada": "Jornada 9", "fecha": "23/11/2025", "rival": "C.D. BOCA", "es_local": True, "resultado": "6 - 3", "estado": "G"},
            {"jornada": "Jornada 10", "fecha": "30/11/2025", "rival": "FUNDACION ADF \"B\"", "es_local": False, "resultado": "3 - 2", "estado": "P"},
            {"jornada": "Jornada 11", "fecha": "14/12/2025", "rival": "R.C. INTER DEL PILAR", "es_local": False, "resultado": "2 - 0", "estado": "P"},
            {"jornada": "Jornada 12", "fecha": "21/12/2025", "rival": "A.D. UNION ADARVE \"D\"", "es_local": True, "resultado": "3 - 6", "estado": "P"},
            {"jornada": "Jornada 13", "fecha": "11/01/2026", "rival": "RAYO DEL PILAR C.F.", "es_local": True, "resultado": "4 - 0", "estado": "G"},
            {"jornada": "Jornada 14", "fecha": "18/01/2026", "rival": "U.D. ARROYOFRESNO \"A\"", "es_local": False, "resultado": "2 - 1", "estado": "P"},
            {"jornada": "Jornada 15", "fecha": "25/01/2026", "rival": "A.D.C. LACOMA", "es_local": True, "resultado": "0 - 0", "estado": "E"},
            {"jornada": "Jornada 16", "fecha": "01/02/2026", "rival": "C.D. PEÑAGRANDE", "es_local": True, "resultado": "2 - 1", "estado": "G"},
            {"jornada": "Jornada 17", "fecha": "08/02/2026", "rival": "C.D.E. MIRAMONTE MADRID \"B\"", "es_local": False, "resultado": "1 - 3", "estado": "G"},
            {"jornada": "Jornada 18", "fecha": "15/02/2026", "rival": "IE UNIVERSITY ATHLETICS", "es_local": True, "resultado": "1 - 0", "estado": "G"},
            {"jornada": "Jornada 19", "fecha": "22/02/2026", "rival": "C.D.E. RACING DE MOSTOLES", "es_local": False, "resultado": "1 - 2", "estado": "G"},
            {"jornada": "Jornada 20", "fecha": "01/03/2026", "rival": "C.D. MASRIVER \"B\"", "es_local": False, "resultado": "2 - 0", "estado": "P"},
            {"jornada": "Jornada 21", "fecha": "08/03/2026", "rival": "A.D. EL PARDO \"B\"", "es_local": True, "resultado": "2 - 1", "estado": "G"},
            {"jornada": "Jornada 22", "fecha": "15/03/2026", "rival": "S.A.D. CODEC DE MADRID", "es_local": True, "resultado": "1 - 3", "estado": "P"},
            {"jornada": "Jornada 23", "fecha": "22/03/2026", "rival": "C.D. VIRGEN DE MIRASIERRA", "es_local": True, "resultado": "2 - 3", "estado": "P"},
            {"jornada": "Jornada 24", "fecha": "29/03/2026", "rival": "C.D. BOCA", "es_local": False, "resultado": "1 - 2", "estado": "G"},
            {"jornada": "Jornada 25", "fecha": "12/04/2026", "rival": "FUNDACION ADF \"B\"", "es_local": True, "resultado": "0 - 2", "estado": "P"},
            {"jornada": "Jornada 26", "fecha": "19/04/2026", "rival": "R.C. INTER DEL PILAR", "es_local": True, "resultado": "1 - 2", "estado": "P"},
            {"jornada": "Jornada 27", "fecha": "26/04/2026", "rival": "A.D. UNION ADARVE \"D\"", "es_local": False, "resultado": "4 - 2", "estado": "G"},
            {"jornada": "Jornada 28", "fecha": "10/05/2026", "rival": "RAYO DEL PILAR C.F.", "es_local": False, "resultado": "2 - 2", "estado": "E"},
            {"jornada": "Jornada 29", "fecha": "17/05/2026", "rival": "U.D. ARROYOFRESNO \"A\"", "es_local": True, "resultado": "2 - 2", "estado": "E"},
            {"jornada": "Jornada 30", "fecha": "24/05/2026", "rival": "A.D.C. LACOMA", "es_local": False, "resultado": "5 - 2", "estado": "P"}
        ]
        
    opponents = [x['equipo'] for x in clasificacion if "FUENTELARREYNA" not in x['equipo'].upper()]
    calendar = []
    dates = [
        ("08/10/2025"), ("15/10/2025"), ("22/10/2025"),
        ("29/10/2025"), ("05/11/2025"), ("12/11/2025"),
        ("19/11/2025"), ("26/11/2025"), ("03/12/2025"),
        ("10/12/2025")
    ]
    for idx, fecha in enumerate(dates):
        rival = opponents[idx % len(opponents)] if opponents else f"C.D. RIVAL {idx+1}"
        es_local = (idx % 2 == 0)
        res_type = idx % 3
        if res_type == 0:
            resultado = "2 - 1" if es_local else "1 - 2"
            estado = "G"
        elif res_type == 1:
            resultado = "1 - 1"
            estado = "E"
        else:
            resultado = "0 - 3" if es_local else "3 - 0"
            estado = "P"
            
        calendar.append({
            "jornada": f"Jornada {idx + 1}",
            "fecha": fecha,
            "rival": rival,
            "es_local": es_local,
            "resultado": resultado,
            "estado": estado
        })
    return calendar

def load_cached_data():
    """Carga los datos cacheados o devuelve los de prueba si no existen"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # Inyectar dinámicamente categoría, letra y calendario si faltan
                if data and "equipos" in data:
                    for eq in data["equipos"]:
                        if "categoria" not in eq or "letra" not in eq:
                            cat, letra = detect_categoria_y_letra(eq["nombre"])
                            eq["categoria"] = cat
                            eq["letra"] = letra
                        if "calendario" not in eq or not eq["calendario"]:
                            eq["calendario"] = generate_calendario_demo(eq["nombre"], eq["clasificacion"])
                            eq["calendario"] = enrich_calendar_with_shields(eq["calendario"], eq["clasificacion"])
                        else:
                            # Enriquecer con escudos si los entradas no los tienen
                            eq["calendario"] = enrich_calendar_with_shields(eq["calendario"], eq.get("clasificacion", []))
                return data
        except Exception as e:
            print(f"Error cargando cache RFFM: {e}")
            
    equipos_base = [
        {"nombre": "PRIMERA AFICIONADO - Grupo 2", "puntos": "74", "posicion": "2", "categoria": "AFICIONADO", "letra": "A"},
        {"nombre": "SEGUNDA AFICIONADO - Grupo 14", "puntos": "48", "posicion": "9", "categoria": "AFICIONADO", "letra": "B"},
        {"nombre": "PRIMERA JUVENIL - Grupo 8", "puntos": "78", "posicion": "1", "categoria": "JUVENIL", "letra": "A"},
        {"nombre": "SEGUNDA JUVENIL - Grupo 28", "puntos": "63", "posicion": "3", "categoria": "JUVENIL", "letra": "B"},
        {"nombre": "SEGUNDA JUVENIL - Grupo 22", "puntos": "48", "posicion": "8", "categoria": "JUVENIL", "letra": "C"},
        {"nombre": "PREFERENTE CADETE - Grupo 2", "puntos": "30", "posicion": "13", "categoria": "CADETE", "letra": "A"},
        {"nombre": "PRIMERA CADETE - Grupo 11", "puntos": "28", "posicion": "11", "categoria": "CADETE", "letra": "B"},
        {"nombre": "SEGUNDA CADETE - Grupo 8", "puntos": "24", "posicion": "8", "categoria": "CADETE", "letra": "C"},
        {"nombre": "SEGUNDA CADETE - Grupo 31", "puntos": "38", "posicion": "7", "categoria": "CADETE", "letra": "D"},
        {"nombre": "SEGUNDA CADETE - Grupo 18", "puntos": "48", "posicion": "5", "categoria": "CADETE", "letra": "E"},
        {"nombre": "PRIMERA INFANTIL - Grupo 9", "puntos": "58", "posicion": "4", "categoria": "INFANTIL", "letra": "A"},
        {"nombre": "SEGUNDA INFANTIL - Grupo 21", "puntos": "58", "posicion": "2", "categoria": "INFANTIL", "letra": "B"},
        {"nombre": "SEGUNDA INFANTIL - Grupo 32", "puntos": "8", "posicion": "14", "categoria": "INFANTIL", "letra": "C"},
        {"nombre": "SEGUNDA INFANTIL - Grupo 28", "puntos": "52", "posicion": "3", "categoria": "INFANTIL", "letra": "D"},
        {"nombre": "PREFERENTE ALEVIN - Grupo 1", "puntos": "47", "posicion": "3", "categoria": "ALEVIN", "letra": "A"},
        {"nombre": "PRIMERA ALEVIN - Grupo 3", "puntos": "21", "posicion": "10", "categoria": "ALEVIN", "letra": "B"},
        {"nombre": "PRIMERA FEMENINO ALEVIN F-7 - Grupo 4", "puntos": "11", "posicion": "9", "categoria": "ALEVIN F7", "letra": "FEM A"},
        {"nombre": "SEGUNDA FASE PRIMERA FEMENINO ALEVIN F-7 SUBGRUPO 4 B", "puntos": "34", "posicion": "2", "categoria": "ALEVIN F7", "letra": "FEM B"},
        {"nombre": "PRIMERA ALEVIN F-7 - GRUPO 3", "puntos": "30", "posicion": "8", "categoria": "ALEVIN F7", "letra": "A"},
        {"nombre": "SEGUNDA ALEVIN F-7 - GRUPO 16", "puntos": "3", "posicion": "13", "categoria": "ALEVIN F7", "letra": "B"},
        {"nombre": "PRIMERA DIVISION AUTONOMICA BENJAMIN F-7 - Grupo 4", "puntos": "63", "posicion": "1", "categoria": "BENJAMIN", "letra": "A"},
        {"nombre": "PRIMERA BENJAMIN F-7 - Grupo 22 (Grupo A)", "puntos": "8", "posicion": "12", "categoria": "BENJAMIN", "letra": "B"},
        {"nombre": "PRIMERA BENJAMIN F-7 - Grupo 22 (Grupo B)", "puntos": "28", "posicion": "8", "categoria": "BENJAMIN", "letra": "C"},
        {"nombre": "PRIMERA BENJAMIN F-7 - Grupo 22 (Grupo C)", "puntos": "60", "posicion": "1", "categoria": "BENJAMIN", "letra": "D"},
        {"nombre": "T. CAMPEONES 1º BENJAMIN F7 1ª FASE - TRIANGULAR 10", "puntos": "3", "posicion": "3", "categoria": "BENJAMIN", "letra": "T1"},
        {"nombre": "T. CAMPEONES AUTONOMICA BENJAMIN F7 1ª FASE - CUADRANGULAR 1", "puntos": "1", "posicion": "4", "categoria": "BENJAMIN", "letra": "T2"},
        {"nombre": "PREFERENTE PREBENJAMIN F-7 GRUPO 5", "puntos": "17", "posicion": "8", "categoria": "PREBENJAMIN", "letra": "A"},
        {"nombre": "SEGUNDA FASE PREFERENTE PREBENJAMIN F-7 SUBGRUPO 5 A", "puntos": "30", "posicion": "4", "categoria": "PREBENJAMIN", "letra": "A F2"},
        {"nombre": "PRIMERA PREBENJAMIN F-7 - Grupo 18 (Grupo A)", "puntos": "8", "posicion": "12", "categoria": "PREBENJAMIN", "letra": "B"},
        {"nombre": "PRIMERA PREBENJAMIN F-7 - Grupo 18 (Grupo B)", "puntos": "18", "posicion": "8", "categoria": "PREBENJAMIN", "letra": "C"},
        {"nombre": "SEGUNDA FASE PRIMERA PREBENJAMIN F-7 SUBGRUPO 18 B", "puntos": "0", "posicion": "6", "categoria": "PREBENJAMIN", "letra": "B F2"},
        {"nombre": "SEGUNDA FASE PRIMERA PREBENJAMIN F-7 SUBGRUPO 18 A", "puntos": "27", "posicion": "3", "categoria": "PREBENJAMIN", "letra": "C F2"}
    ]
    
    equipos_data = []
    for eq in equipos_base:
        clasif = generate_full_clasificacion(eq["nombre"], eq["posicion"], eq["puntos"])
        cal = generate_calendario_demo(eq["nombre"], clasif)
        cal = enrich_calendar_with_shields(cal, clasif)
        
        ultimo = None
        if cal:
            p = cal[-1]
            suffix = f" \"{eq['letra']}\"" if eq['letra'] in ["A", "B", "C", "D", "E"] else ""
            local = f"CLUB FUENTELARREYNA{suffix}" if p["es_local"] else p["rival"]
            visitante = p["rival"] if p["es_local"] else f"CLUB FUENTELARREYNA{suffix}"
            
            ultimo = {
                "jornada": p["jornada"],
                "fecha": f"{p['fecha']}",
                "local": local,
                "visitante": visitante,
                "local_shield": "/static/ESCUDO SIN FONDO.png" if p["es_local"] else f"https://www.ffmadrid.es/rffm/escudos/club_{1000 + eq['posicion'].isdigit() and int(eq['posicion'])*73 or 100}.png",
                "visitante_shield": f"https://www.ffmadrid.es/rffm/escudos/club_{1000 + eq['posicion'].isdigit() and int(eq['posicion'])*73 or 100}.png" if p["es_local"] else "/static/ESCUDO SIN FONDO.png",
                "resultado": p["resultado"],
                "contrario": p["rival"],
                "goles_favor": int(p["resultado"].split(" - ")[0] if p["es_local"] else p["resultado"].split(" - ")[1]),
                "goles_contra": int(p["resultado"].split(" - ")[1] if p["es_local"] else p["resultado"].split(" - ")[0]),
                "es_local": p["es_local"]
            }
            
        equipos_data.append({
            "nombre": eq["nombre"],
            "categoria": eq["categoria"],
            "letra": eq["letra"],
            "puntos": eq["puntos"],
            "posicion": eq["posicion"],
            "ultimo_partido": ultimo,
            "clasificacion": clasif,
            "calendario": cal
        })
        
    demo_data = {
        "status": "demo",
        "last_updated": datetime.now().strftime("%d/%m/%Y %H:%M:%S") + " (Mapeo Completo 32 Equipos)",
        "equipos": equipos_data
    }
    return demo_data


# --- INFORME DE COLORES DE EQUIPACIÓN (Listado de Partidos -> "Ver equipaciones") ---

CLUB_FUENTELARREYNA_ID = '4239'

def _load_kit_cache():
    if os.path.exists(KIT_CACHE_FILE):
        try:
            with open(KIT_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_kit_cache(cache):
    os.makedirs(os.path.dirname(KIT_CACHE_FILE), exist_ok=True)
    with open(KIT_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def _es_color_rojo(color):
    return 'ROJ' in (color or '').upper()

def _temporada_actual():
    """Calcula el código y fechas de la temporada actual (01/08 - 31/07)."""
    hoy = datetime.now()
    if hoy.month >= 9:
        anio_ini, anio_fin = hoy.year, hoy.year + 1
    else:
        anio_ini, anio_fin = hoy.year - 1, hoy.year
    # Códigos de temporada de la RFFM: 21 = 2025-2026, 20 = 2024-2025, ...
    cod_temporada = str(21 - (2025 - anio_ini))
    fecha_desde = f"01-09-{anio_ini}"
    fecha_hasta = f"31-08-{anio_fin}"
    return cod_temporada, fecha_desde, fecha_hasta

def fetch_temporada_matches(session, club_id=CLUB_FUENTELARREYNA_ID, cod_temporada=None, fecha_desde=None, fecha_hasta=None):
    """Descarga el Listado de Partidos completo del club para toda la temporada
    (ruta Competición > Campos y Horas > Listado de Partidos), usando la exportación
    "Descargar Excel Agrupado por Día": la vista HTML interactiva tiene un límite oculto
    de ~300 resultados en pantalla, mientras que la exportación devuelve todos (verificado
    contra el total real: 661 partidos en la temporada 2025-2026)."""
    if not cod_temporada:
        cod_temporada, fecha_desde, fecha_hasta = _temporada_actual()

    params = {
        'cod_primaria': '1000139',
        'Consulta': '1',
        'Sch_Cod_Temporada': cod_temporada,
        'Sch_Fecha_Desde': fecha_desde,
        'Sch_Fecha_Hasta': fecha_hasta,
        'Club': club_id,
        'Sch_Clave_Acceso_Club': club_id,
        'NPcd_PageLines': '5000',
        'NPcd_File': '1',
        'excel': '1',
    }
    r = session.get('https://intranet.ffmadrid.es/nfg/NPcd/NFG_LstPartidos', params=params, timeout=60)
    r.encoding = 'iso-8859-1'
    soup = BeautifulSoup(r.text, 'html.parser')

    equipo_re = re.compile(r'^(\d+)\s*-\s*(.+)$')
    matches = []

    for tr in soup.find_all('tr'):
        tds = tr.find_all('td')
        if len(tds) < 10:
            continue

        m_local = equipo_re.match(tds[5].get_text(strip=True))
        m_visit = equipo_re.match(tds[6].get_text(strip=True))
        if not m_local or not m_visit:
            continue

        cod_equipo_casa, nombre_casa = m_local.groups()
        cod_equipo_fuera, nombre_visit = m_visit.groups()

        competicion = f"{tds[1].get_text(strip=True)}, {tds[2].get_text(strip=True)}"

        matches.append({
            'fecha': tds[8].get_text(strip=True),
            'competicion': competicion,
            'campo': tds[7].get_text(strip=True),
            'hora': tds[9].get_text(strip=True),
            'jornada': tds[4].get_text(strip=True),
            'resultado': '',
            'deporte': tds[0].get_text(strip=True),
            'nombre_casa': nombre_casa.strip(),
            'cod_equipo_casa': cod_equipo_casa,
            'nombre_fuera': nombre_visit.strip(),
            'cod_equipo_fuera': cod_equipo_fuera,
        })

    return matches


def fetch_codacta_map(session, club_id=CLUB_FUENTELARREYNA_ID, cod_temporada=None, fecha_desde=None, fecha_hasta=None):
    """Devuelve un dict {match_key: codacta} recorriendo el Listado de Partidos (vista
    interactiva, no la exportación Excel, porque solo la vista interactiva incluye el
    'Cod. Partido' que abre el PDF de alineaciones). La vista interactiva tiene un límite
    oculto de ~300 resultados, así que se trocea mes a mes para no superarlo nunca."""
    if not cod_temporada:
        cod_temporada, fecha_desde, fecha_hasta = _temporada_actual()

    d_ini, m_ini, y_ini = fecha_desde.split('-')
    d_fin, m_fin, y_fin = fecha_hasta.split('-')
    y_ini, m_ini, y_fin, m_fin = int(y_ini), int(m_ini), int(y_fin), int(m_fin)

    meses = []
    y, m = y_ini, m_ini
    while (y, m) <= (y_fin, m_fin):
        meses.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1

    day_pattern = re.compile(r'<b>(\d{2}-\d{2}-\d{4}),')
    row_pattern = re.compile(
        r'descarga_alineaciones\((\d+)\);.*?camisetas\((\d+),(\d+),(\d+),(\d+)\);.*?(\d{2}:\d{2})&nbsp;',
        re.DOTALL
    )

    resultado = {}
    for (anio, mes) in meses:
        desde = f"01-{mes:02d}-{anio}"
        ultimo_dia = calendar.monthrange(anio, mes)[1]
        hasta = f"{ultimo_dia:02d}-{mes:02d}-{anio}"
        params = {
            'cod_primaria': '1000139',
            'Consulta': '1',
            'Sch_Cod_Temporada': cod_temporada,
            'Sch_Fecha_Desde': desde,
            'Sch_Fecha_Hasta': hasta,
            'Club': club_id,
            'Sch_Clave_Acceso_Club': club_id,
            'NPcd_PageLines': '5000',
        }
        try:
            r = session.get('https://intranet.ffmadrid.es/nfg/NPcd/NFG_LstPartidos', params=params, timeout=60)
            r.encoding = 'iso-8859-1'
        except Exception:
            continue

        partes = day_pattern.split(r.text)
        for i in range(1, len(partes), 2):
            fecha = partes[i]
            contenido = partes[i + 1] if i + 1 < len(partes) else ''
            for m_row in row_pattern.finditer(contenido):
                codacta, _cod_club_casa, cod_equipo_casa, _cod_club_fuera, cod_equipo_fuera, hora = m_row.groups()
                key = f"{fecha}_{hora}_{cod_equipo_casa}_{cod_equipo_fuera}"
                resultado[key] = codacta

    return resultado


def _load_convocatorias_cache():
    f_path = _conv_cache_file()
    if os.path.exists(f_path):
        try:
            with open(f_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_convocatorias_cache(cache):
    f_path = _conv_cache_file()
    os.makedirs(os.path.dirname(f_path), exist_ok=True)
    with open(f_path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def fetch_alineacion_convocados(session, codacta, es_futbol_11):
    """Descarga y parsea el PDF de alineaciones de un partido (NFG_CMP_Alineacion),
    devolviendo {'casa': [...], 'fuera': [...]} con los convocados de cada equipo.
    El PDF no incluye la etiqueta TITULARES/SUPLENTES como texto; se asume (igual que
    en la propia acta) que los primeros 11 (fútbol-11) o 7 (fútbol-7) jugadores listados
    de cada equipo son titulares y el resto suplentes."""
    cutoff = 11 if es_futbol_11 else 7
    r = session.get(
        'https://intranet.ffmadrid.es/nfg/NPcd/NFG_CMP_Alineacion',
        params={'cod_primaria': '1000139', 'codacta': codacta, 'NPcd_Pdf': '1'},
        timeout=60
    )
    if r.status_code != 200 or not r.content:
        return None

    jugador_re = re.compile(r'^(\d+)\s+(.+)$')

    try:
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            page = pdf.pages[0]
            words = page.extract_words()
            mitad = page.width / 2

            def extraer_lado(min_x, max_x, top_min, top_max):
                filas = {}
                for w in words:
                    if min_x <= w['x0'] < max_x and top_min <= w['top'] <= top_max:
                        t = round(w['top'] / 4) * 4  # agrupar palabras de la misma fila
                        filas.setdefault(t, []).append((w['x0'], w['text']))
                jugadores = []
                for t in sorted(filas.keys()):
                    palabras = [txt for _, txt in sorted(filas[t])]
                    texto = ' '.join(palabras)
                    m = jugador_re.match(texto)
                    if not m:
                        continue
                    numero, resto = m.groups()
                    portero = '(P)' in resto
                    capitan = '(C)' in resto
                    nombre = resto.replace('(P)', '').replace('(C)', '').strip()
                    jugadores.append({'numero': numero, 'nombre': nombre, 'portero': portero, 'capitan': capitan})
                for idx, j in enumerate(jugadores):
                    j['titular'] = idx < cutoff
                return jugadores

            # La franja de jugadores empieza tras la cabecera "Nº JUGADOR/A..." y termina antes de "ENTRENADOR/A"
            top_jugadores_ini = None
            top_jugadores_fin = None
            for w in words:
                if w['text'] == 'JUGADOR/A' and top_jugadores_ini is None:
                    top_jugadores_ini = w['bottom']
                if 'ENTRENADOR' in w['text'] and (top_jugadores_fin is None or w['top'] < top_jugadores_fin):
                    top_jugadores_fin = w['top']
            if top_jugadores_ini is None:
                top_jugadores_ini = 0
            if top_jugadores_fin is None:
                top_jugadores_fin = page.height

            casa = extraer_lado(0, mitad, top_jugadores_ini, top_jugadores_fin)
            fuera = extraer_lado(mitad, page.width, top_jugadores_ini, top_jugadores_fin)
            return {'casa': casa, 'fuera': fuera}
    except Exception as e:
        print(f"Error parseando acta {codacta}: {e}")
        return None

def get_match_kit_colors(session, cod_equipo_casa, cod_equipo_fuera):
    """Llama al mismo endpoint que el icono "Ver equipaciones" de la web para un partido concreto.
    Devuelve los colores (1ª equipación) de ambos equipos exactamente como en ese partido.
    El código de club no influye en el resultado (verificado), solo el código de equipo."""
    url = "https://intranet.ffmadrid.es/nfg/NPcd/NFG_VisEquipacion"
    params = {
        'cod_primaria': '1000139',
        'cod_club_casa': '0',
        'cod_equipo_casa': cod_equipo_casa,
        'cod_club_fuera': '0',
        'cod_equipo_fuera': cod_equipo_fuera,
    }
    resultado = {'casa': None, 'fuera': None}
    try:
        r = session.get(url, params=params, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        divs = soup.find_all('div', class_='equipo')
        for i, key in enumerate(['casa', 'fuera']):
            if i >= len(divs):
                continue
            d = divs[i]
            h3 = d.find('h3')
            nombre = h3.get_text(strip=True) if h3 else ''
            colores = {'camiseta': '', 'pantalon': '', 'medias': ''}
            for p in d.find_all('p'):
                txt = p.get_text(' ', strip=True)
                if ':' not in txt:
                    continue
                label, _, valor = txt.partition(':')
                label = label.strip()
                valor = re.sub(r'\s+', ' ', valor).strip()
                if 'Camiseta' in label:
                    colores['camiseta'] = valor
                elif 'Pantal' in label:
                    colores['pantalon'] = valor
                elif 'Medias' in label:
                    colores['medias'] = valor
            resultado[key] = {'nombre': nombre, **colores}
    except Exception as e:
        print(f"Aviso: no se pudo obtener equipación del partido ({cod_equipo_casa} vs {cod_equipo_fuera}): {e}")
    return resultado

def build_kit_clash_report(username, password):
    """Genera el informe de colores de equipación usando el Listado de Partidos oficial del club
    (más preciso que inferir por nombre: usa directamente los códigos de equipo de cada partido)."""
    url_login = "https://intranet.ffmadrid.es/nfg/NLogin"
    session = get_session()
    payload = {"NUser": username, "NPass": password, "LoginAjax": "1"}

    try:
        res_login = session.post(url_login, data=payload)
        match_est = re.search(r'var estado="(\d+)"', res_login.text)
        estado = match_est.group(1) if match_est else "2"
        if estado != "1":
            return False, "Credenciales incorrectas o error de acceso.", None

        cod_temporada, fecha_desde, fecha_hasta = _temporada_actual()
        matches = fetch_temporada_matches(session, CLUB_FUENTELARREYNA_ID, cod_temporada, fecha_desde, fecha_hasta)
        if not matches:
            return False, "No se encontraron partidos en el Listado de Partidos de la RFFM.", None

        kit_cache = _load_kit_cache()
        report_entries = []

        for match in matches:
            somos_casa = 'FUENTELARREYNA' in match['nombre_casa'].upper()
            somos_fuera = 'FUENTELARREYNA' in match['nombre_fuera'].upper()
            es_visitante = somos_fuera and not somos_casa
            if not somos_casa and not somos_fuera:
                continue  # Defensivo: no debería ocurrir, el listado ya está filtrado por nuestro club
            if somos_casa and somos_fuera:
                continue  # Partido entre dos de nuestros propios equipos: no aplica el aviso de coincidencia

            cache_key = f"{match['cod_equipo_casa']}_{match['cod_equipo_fuera']}"
            if cache_key in kit_cache:
                colores = kit_cache[cache_key]
            else:
                colores = get_match_kit_colors(session, match['cod_equipo_casa'], match['cod_equipo_fuera'])
                kit_cache[cache_key] = colores

            casa = colores.get('casa')
            fuera = colores.get('fuera')
            # El aviso de coincidencia de rojo solo aplica cuando jugamos de visitante
            # (el rival, que juega en casa, es quien debe adaptar su color si coincide con el nuestro)
            warning = es_visitante and bool(casa) and (_es_color_rojo(casa.get('camiseta')) or _es_color_rojo(casa.get('pantalon')))

            cat, letra_heur = detect_categoria_y_letra(match['competicion'])
            nuestro_lado = fuera if es_visitante else casa
            rival_lado = casa if es_visitante else fuera
            nuestro_nombre = (nuestro_lado.get('nombre') if nuestro_lado else '') or match['nombre_fuera' if es_visitante else 'nombre_casa']
            rival_nombre = (rival_lado.get('nombre') if rival_lado else '') or match['nombre_casa' if es_visitante else 'nombre_fuera']
            _m = re.search(r'"([^"]+)"', nuestro_nombre or '')
            if _m:
                letra = _m.group(1)
            else:
                _w = (nuestro_nombre or '').strip().upper().split()
                _l = _w[-1] if _w else ''
                letra = _l if (_l and len(_l) <= 2 and all(c in 'ABCDE' for c in _l)) else letra_heur
            nuestro_equipo = nuestro_nombre or f"{cat or ''} {letra or ''}".strip()

            match_key = f"{match['fecha']}_{match['hora']}_{match['cod_equipo_casa']}_{match['cod_equipo_fuera']}"

            report_entries.append({
                "match_key": match_key,
                "fecha": match['fecha'],
                "jornada": match['jornada'],
                "hora": match['hora'],
                "campo": match['campo'],
                "resultado": match['resultado'],
                "competicion": match['competicion'],
                "categoria": cat or '',
                "letra": letra or '',
                "es_visitante": es_visitante,
                "nuestro_equipo": nuestro_equipo,
                "rival": rival_nombre,
                "camiseta": rival_lado.get('camiseta', '') if rival_lado else '',
                "pantalon": rival_lado.get('pantalon', '') if rival_lado else '',
                "medias": rival_lado.get('medias', '') if rival_lado else '',
                "warning": warning,
                "sin_datos": rival_lado is None
            })

        _save_kit_cache(kit_cache)

        def parse_fecha_sort(f):
            m = re.search(r'(\d{2})-(\d{2})-(\d{4})', f or '')
            if m:
                return (m.group(3), m.group(2), m.group(1))
            return ('0000', '00', '00')

        report_entries.sort(key=lambda x: parse_fecha_sort(x['fecha']))

        report_data = {
            "status": "success",
            "last_updated": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "temporada": f"{fecha_desde} a {fecha_hasta}",
            "entries": report_entries
        }
        os.makedirs(os.path.dirname(KIT_REPORT_FILE), exist_ok=True)
        with open(KIT_REPORT_FILE, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        return True, f"Informe generado: {len(report_entries)} partidos de visitante analizados.", report_data

    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f"Error generando el informe: {str(e)}", None

def load_kit_clash_report():
    if os.path.exists(KIT_REPORT_FILE):
        try:
            with open(KIT_REPORT_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None


def build_convocatorias_report(username, password):
    """Genera el informe de convocatorias (jugadores llamados a cada partido) a partir
    del Listado de Partidos + el PDF de alineaciones (Acta) de cada partido jugado."""
    url_login = "https://intranet.ffmadrid.es/nfg/NLogin"
    session = get_session()
    payload = {"NUser": username, "NPass": password, "LoginAjax": "1"}

    try:
        res_login = session.post(url_login, data=payload)
        match_est = re.search(r'var estado="(\d+)"', res_login.text)
        estado = match_est.group(1) if match_est else "2"
        if estado != "1":
            return False, "Credenciales incorrectas o error de acceso.", None

        cod_temporada, fecha_desde, fecha_hasta = _temporada_actual()
        matches = fetch_temporada_matches(session, CLUB_FUENTELARREYNA_ID, cod_temporada, fecha_desde, fecha_hasta)
        if not matches:
            return False, "No se encontraron partidos en el Listado de Partidos de la RFFM.", None

        codacta_map = fetch_codacta_map(session, CLUB_FUENTELARREYNA_ID, cod_temporada, fecha_desde, fecha_hasta)
        convocatorias_cache = _load_convocatorias_cache()

        report_entries = []
        for match in matches:
            somos_casa = 'FUENTELARREYNA' in match['nombre_casa'].upper()
            somos_fuera = 'FUENTELARREYNA' in match['nombre_fuera'].upper()
            es_visitante = somos_fuera and not somos_casa
            if not somos_casa and not somos_fuera:
                continue
            if somos_casa and somos_fuera:
                continue

            match_key = f"{match['fecha']}_{match['hora']}_{match['cod_equipo_casa']}_{match['cod_equipo_fuera']}"
            codacta = codacta_map.get(match_key)

            cat, letra_heuristica = detect_categoria_y_letra(match['competicion'])
            es_futbol_11 = 'futbol-11' in (match.get('deporte') or '').lower().replace(' ', '')

            convocados = None
            if codacta:
                if codacta in convocatorias_cache:
                    convocados = convocatorias_cache[codacta]
                else:
                    parsed = fetch_alineacion_convocados(session, codacta, es_futbol_11)
                    if parsed:
                        convocados = parsed['fuera'] if es_visitante else parsed['casa']
                        convocatorias_cache[codacta] = convocados

            nuestro_nombre = match['nombre_fuera'] if es_visitante else match['nombre_casa']
            rival_nombre = match['nombre_casa'] if es_visitante else match['nombre_fuera']
            # El "letra" heurístico de detect_categoria_y_letra viene del nombre de la competición
            # (grupo, fase...) y NO distingue entre nuestros propios sub-equipos (Fuentelarreyna "A" vs "B" vs "C"...).
            # Para identificar el equipo real usamos el sufijo entre comillas del nombre RFFM de nuestro equipo.
            m_letra_real = re.search(r'"([^"]+)"', nuestro_nombre or '')
            if m_letra_real:
                letra = m_letra_real.group(1)
            else:
                _words = (nuestro_nombre or '').strip().upper().split()
                _last = _words[-1] if _words else ''
                letra = _last if (_last and len(_last) <= 2 and all(c in 'ABCDE' for c in _last)) else letra_heuristica
            nuestro_equipo = nuestro_nombre or f"{cat or ''} {letra or ''}".strip()

            report_entries.append({
                "match_key": match_key,
                "fecha": match['fecha'],
                "jornada": match['jornada'],
                "hora": match['hora'],
                "campo": match['campo'],
                "competicion": match['competicion'],
                "categoria": cat or '',
                "letra": letra or '',
                "es_visitante": es_visitante,
                "es_futbol_11": es_futbol_11,
                "nuestro_equipo": nuestro_equipo,
                "rival": rival_nombre,
                "convocados": convocados or [],
                "sin_datos": convocados is None
            })

        _save_convocatorias_cache(convocatorias_cache)

        def parse_fecha_sort(f):
            m = re.search(r'(\d{2})-(\d{2})-(\d{4})', f or '')
            if m:
                return (m.group(3), m.group(2), m.group(1))
            return ('0000', '00', '00')

        report_entries.sort(key=lambda x: parse_fecha_sort(x['fecha']))

        report_data = {
            "status": "success",
            "last_updated": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "temporada": f"{fecha_desde} a {fecha_hasta}",
            "entries": report_entries
        }
        rpt_path = _conv_report_file()
        os.makedirs(os.path.dirname(rpt_path), exist_ok=True)
        with open(rpt_path, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        con_datos = sum(1 for e in report_entries if not e['sin_datos'])
        return True, f"Informe generado: {len(report_entries)} partidos, {con_datos} con convocatoria disponible.", report_data

    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f"Error generando el informe: {str(e)}", None


def load_convocatorias_report():
    rpt_path = _conv_report_file()
    if os.path.exists(rpt_path):
        try:
            with open(rpt_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None

# --- CHECKLIST DE EQUIPACIÓN LLEVADA POR PARTIDO (llevo / devuelvo, tallas, comentario) ---

CHECKLIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'equipacion_checklist.json')

def load_checklist_equipacion():
    if os.path.exists(CHECKLIST_FILE):
        try:
            with open(CHECKLIST_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def guardar_checklist_equipacion(match_key, datos):
    """Actualiza (merge) los datos del checklist de un partido concreto y los persiste."""
    checklist = load_checklist_equipacion()
    actual = checklist.get(match_key, {})
    actual.update(datos)
    checklist[match_key] = actual
    os.makedirs(os.path.dirname(CHECKLIST_FILE), exist_ok=True)
    with open(CHECKLIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(checklist, f, ensure_ascii=False, indent=2)
    return actual

# --- ANOTACIONES MANUALES DE CONVOCATORIA (editar convocado/no convocado + comentario por celda) ---

def load_convocatoria_anotaciones():
    anot_path = _conv_anot_file()
    if os.path.exists(anot_path):
        try:
            with open(anot_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def guardar_convocatoria_anotacion(clave, datos):
    """clave = '<match_key>|<nombre_jugador>'. Permite sobreescribir manualmente si fue convocado y/o añadir un comentario."""
    anotaciones = load_convocatoria_anotaciones()
    actual = anotaciones.get(clave, {})
    actual.update(datos)
    anotaciones[clave] = actual
    anot_path = _conv_anot_file()
    os.makedirs(os.path.dirname(anot_path), exist_ok=True)
    with open(anot_path, 'w', encoding='utf-8') as f:
        json.dump(anotaciones, f, ensure_ascii=False, indent=2)
    return actual


# --- OBJ SEMANALES: objetivo automático por partido, clasificación reconstruida jornada a jornada,
# e historial de cualquier rival. Todo a partir de "calendario_grupo" (ya cacheado), sin scraping adicional ---

def _numero_jornada(jornada_str):
    """'Jornada 23' -> 23. Si no se puede extraer, devuelve un número alto para no romper el orden."""
    m = re.search(r'(\d+)', jornada_str or '')
    return int(m.group(1)) if m else 999


def calcular_clasificacion_en_jornada(calendario_grupo, hasta_jornada):
    """Reconstruye la clasificación del grupo (puntos, jugados, G/E/P, GF, GC, posición) usando
    solo los partidos con número de jornada <= hasta_jornada. Devuelve {nombre_equipo: {datos}}."""
    tabla = {}

    def _equipo(nombre):
        if nombre not in tabla:
            tabla[nombre] = {"equipo": nombre, "puntos": 0, "jugados": 0, "ganados": 0,
                              "empatados": 0, "perdidos": 0, "goles_favor": 0, "goles_contra": 0}
        return tabla[nombre]

    for partido in calendario_grupo:
        if _numero_jornada(partido.get('jornada')) > hasta_jornada:
            continue
        resultado = (partido.get('resultado') or '').strip()
        m = re.search(r'(\d+)\s*-\s*(\d+)', resultado)
        if not m:
            continue
        g_local, g_visit = int(m.group(1)), int(m.group(2))
        local, visit = partido.get('local'), partido.get('visitante')
        if not local or not visit:
            continue

        eq_local = _equipo(local)
        eq_visit = _equipo(visit)
        eq_local['jugados'] += 1
        eq_visit['jugados'] += 1
        eq_local['goles_favor'] += g_local
        eq_local['goles_contra'] += g_visit
        eq_visit['goles_favor'] += g_visit
        eq_visit['goles_contra'] += g_local

        if g_local > g_visit:
            eq_local['ganados'] += 1
            eq_local['puntos'] += 3
            eq_visit['perdidos'] += 1
        elif g_local < g_visit:
            eq_visit['ganados'] += 1
            eq_visit['puntos'] += 3
            eq_local['perdidos'] += 1
        else:
            eq_local['empatados'] += 1
            eq_local['puntos'] += 1
            eq_visit['empatados'] += 1
            eq_visit['puntos'] += 1

    # Orden habitual: puntos desc, diferencia de goles desc, goles a favor desc
    ordenados = sorted(tabla.values(), key=lambda e: (-e['puntos'], -(e['goles_favor'] - e['goles_contra']), -e['goles_favor']))
    for i, eq in enumerate(ordenados, start=1):
        eq['posicion'] = i

    return {eq['equipo']: eq for eq in ordenados}


def ultimos_resultados_equipo(calendario_grupo, equipo, hasta_jornada, n=2):
    """Últimos n resultados de 'equipo' (jornadas anteriores a hasta_jornada) y contra quién fueron."""
    partidos_equipo = []
    for partido in calendario_grupo:
        num = _numero_jornada(partido.get('jornada'))
        if num >= hasta_jornada:
            continue
        local, visit = partido.get('local'), partido.get('visitante')
        if local != equipo and visit != equipo:
            continue
        resultado = (partido.get('resultado') or '').strip()
        m = re.search(r'(\d+)\s*-\s*(\d+)', resultado)
        if not m:
            continue
        g_local, g_visit = int(m.group(1)), int(m.group(2))
        es_local = (local == equipo)
        rival = visit if es_local else local
        g_favor = g_local if es_local else g_visit
        g_contra = g_visit if es_local else g_local
        estado = 'G' if g_favor > g_contra else ('E' if g_favor == g_contra else 'P')
        partidos_equipo.append({"jornada_num": num, "rival": rival, "resultado": f"{g_favor} - {g_contra}", "estado": estado})

    partidos_equipo.sort(key=lambda p: p['jornada_num'], reverse=True)
    return partidos_equipo[:n]


CRITERIOS_OBJETIVO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'criterios_objetivo.json')

CRITERIOS_OBJETIVO_DEFAULT = {
    "ganar_pos_dif_min": 3,
    "ganar_pts_dif_min": 5,
    "empatar_pos_dif_max": 1,
    "empatar_pts_dif_max": 3,
}

CRITERIOS_OBJETIVO_DESCRIPCIONES = {
    "ganar_pos_dif_min": "Diferencia mínima de posiciones en la tabla a nuestro favor para que el objetivo sea GANAR",
    "ganar_pts_dif_min": "Diferencia mínima de puntos a nuestro favor para que el objetivo sea GANAR",
    "empatar_pos_dif_max": "Diferencia máxima de posiciones (en cualquier sentido) para considerar el partido igualado (EMPATAR)",
    "empatar_pts_dif_max": "Diferencia máxima de puntos (en cualquier sentido) para considerar el partido igualado (EMPATAR)",
}


def load_criterios_objetivo():
    """Lee los umbrales que determinan el objetivo automático de cada partido. Si no hay
    fichero o falta alguna clave, se completa con los valores por defecto."""
    criterios = dict(CRITERIOS_OBJETIVO_DEFAULT)
    if os.path.exists(CRITERIOS_OBJETIVO_FILE):
        try:
            with open(CRITERIOS_OBJETIVO_FILE, 'r', encoding='utf-8') as f:
                guardado = json.load(f)
            for k in CRITERIOS_OBJETIVO_DEFAULT:
                if k in guardado:
                    criterios[k] = int(guardado[k])
        except Exception:
            pass
    return criterios


def guardar_criterios_objetivo(nuevos):
    """Valida y persiste los nuevos umbrales. Devuelve el conjunto final guardado."""
    criterios = load_criterios_objetivo()
    for k in CRITERIOS_OBJETIVO_DEFAULT:
        if k in nuevos:
            criterios[k] = int(nuevos[k])
    os.makedirs(os.path.dirname(CRITERIOS_OBJETIVO_FILE), exist_ok=True)
    with open(CRITERIOS_OBJETIVO_FILE, 'w', encoding='utf-8') as f:
        json.dump(criterios, f, ensure_ascii=False, indent=2)
    return criterios


def calcular_objetivo(pos_nuestra, pts_nuestro, pos_rival, pts_rival):
    """GANAR / GANAR/EMPATAR / EMPATAR / COMPETIR según diferencia de posición y puntos.
    pos_dif > 0 = estamos mejor posicionados (numéricamente más arriba en la tabla).
    Los umbrales son configurables (ver load_criterios_objetivo)."""
    if pos_nuestra is None or pos_rival is None or pts_nuestro is None or pts_rival is None:
        return None
    pos_dif = pos_rival - pos_nuestra
    pts_dif = pts_nuestro - pts_rival
    c = load_criterios_objetivo()

    if pos_dif >= c["ganar_pos_dif_min"] and pts_dif >= c["ganar_pts_dif_min"]:
        return "GANAR"
    if pos_dif < 0 or pts_dif < 0:
        return "COMPETIR"
    if abs(pos_dif) <= c["empatar_pos_dif_max"] and abs(pts_dif) <= c["empatar_pts_dif_max"]:
        return "EMPATAR"
    return "GANAR/EMPATAR"


def _cumple_objetivo(objetivo, estado_real):
    """estado_real: 'G' (ganamos) / 'E' (empate) / 'P' (perdimos). Devuelve True/False/None (sin jugar aún)."""
    if not objetivo or not estado_real:
        return None
    if objetivo == "GANAR":
        return estado_real == 'G'
    if objetivo in ("GANAR/EMPATAR", "EMPATAR"):
        return estado_real in ('G', 'E')
    if objetivo == "COMPETIR":
        return True  # no hay forma de "fallar" este objetivo, cualquier resultado vale
    return None


def _clasif_fila_a_dict(fila):
    """Convierte una fila de clasificacion (strings del caché RFFM) al mismo formato que
    calcular_clasificacion_en_jornada(), para poder usarla como fallback."""
    def _i(v):
        try: return int(str(v).strip())
        except: return 0
    return {
        'equipo': fila.get('equipo', ''),
        'posicion': _i(fila.get('pos', 0)),
        'puntos': _i(fila.get('puntos', 0)),
        'jugados': _i(fila.get('jugados', 0)),
        'ganados': _i(fila.get('ganados', 0)),
        'empatados': _i(fila.get('empatados', 0)),
        'perdidos': _i(fila.get('perdidos', 0)),
        'goles_favor': _i(fila.get('goles_favor', 0)),
        'goles_contra': _i(fila.get('goles_contra', 0)),
        '_approx': True,  # indica que son datos de fin de temporada, no reconstruidos por jornada
    }


def _rival_coincide(rival_norm, nombre_eq_norm):
    """Comparación flexible de nombres de equipos: primero exacto (sin comillas), luego por inclusión."""
    if not rival_norm or not nombre_eq_norm:
        return False
    if rival_norm == nombre_eq_norm:
        return True
    # Inclusión bidireccional para variantes con/sin sufijo entre comillas
    if rival_norm in nombre_eq_norm or nombre_eq_norm in rival_norm:
        return True
    return False


def _formato_grupo(nombre_grupo):
    """Extrae una etiqueta corta de formato del nombre del grupo (F7, F11, FEM)."""
    ng = (nombre_grupo or '').upper()
    if 'F-7' in ng or ' F7' in ng:
        return 'F7'
    if 'F-11' in ng or ' F11' in ng:
        return 'F11'
    if 'FEMENIN' in ng or 'FEM' in ng:
        return 'FEM'
    return ''


def _grupo_short(nombre_grupo):
    """Extrae un identificador corto del grupo: 'Gr.22', 'Gr.4', etc."""
    import re as _re
    m = _re.search(r'Grupo\s*(\d+)', nombre_grupo or '', _re.IGNORECASE)
    return f'Gr.{m.group(1)}' if m else ''


def construir_obj_semanales():
    """Recorre el caché de RFFM y devuelve, por cada partido de cada equipo del club: rival,
    clasificación de ambos (previa al partido o de fin de temporada como fallback), objetivo calculado,
    resultado si ya se jugó, cumplimiento del objetivo, y los últimos resultados del rival.
    Deduplica grupos con nombre idéntico (bug del scraper) y añade ida_resultado si existe."""
    cache = load_cached_data()
    resultado_final = []

    # Deduplicar: saltar entries con nombre_grupo ya procesado
    nombres_vistos = set()

    for equipo_data in cache.get('equipos', []):
        categoria = equipo_data.get('categoria', '')
        letra = equipo_data.get('letra', '')
        nombre_grupo = equipo_data.get('nombre', '')
        formato = _formato_grupo(nombre_grupo)
        grp_short = _grupo_short(nombre_grupo)
        calendario = equipo_data.get('calendario', [])
        calendario_grupo = equipo_data.get('calendario_grupo', [])
        clasif_temporada = equipo_data.get('clasificacion', [])

        # Saltar duplicados exactos (mismo nombre de competición)
        if nombre_grupo in nombres_vistos:
            continue
        nombres_vistos.add(nombre_grupo)

        # Nombre exacto de Fuentelarreyna tal y como aparece en calendario_grupo
        nombre_nuestro = None
        for p in calendario_grupo:
            if "FUENTELARREYNA" in (p.get('local') or '').upper():
                nombre_nuestro = p['local']; break
            if "FUENTELARREYNA" in (p.get('visitante') or '').upper():
                nombre_nuestro = p['visitante']; break

        # Construir mapa rival → partido más temprano (IDA) con resultado
        # Para cada rival, el primer partido jugado contra él es la IDA
        partidos_ordenados = sorted(calendario, key=lambda p: _numero_jornada(p.get('jornada')))
        ida_por_rival = {}
        for pt in partidos_ordenados:
            if not pt.get('resultado'):
                continue
            r = pt.get('rival', '').replace('"', '').replace("'", '').strip().upper()
            if r not in ida_por_rival:
                ida_por_rival[r] = pt

        for partido in partidos_ordenados:
            jornada_num = _numero_jornada(partido.get('jornada'))
            rival = partido.get('rival', '')
            rival_norm = rival.replace('"', '').replace("'", '').strip().upper()

            # IDA: buscar si existe un partido anterior contra el mismo rival CON resultado
            ida_partido = ida_por_rival.get(rival_norm)
            # Solo contar como IDA si es un partido DISTINTO (jornada menor)
            if ida_partido and _numero_jornada(ida_partido.get('jornada')) >= jornada_num:
                ida_partido = None

            # Clasificación previa (histórica) si hay calendario_grupo, fallback si no
            clasif_previa = calcular_clasificacion_en_jornada(calendario_grupo, jornada_num - 1) if calendario_grupo else {}
            datos_nuestro = clasif_previa.get(nombre_nuestro) if nombre_nuestro else None
            datos_rival = None
            for nombre_eq, datos_eq in clasif_previa.items():
                if _rival_coincide(rival_norm, nombre_eq.replace('"', '').replace("'", '').strip().upper()):
                    datos_rival = datos_eq; break

            # Fallback: usar la clasificación final de temporada cuando no hay calendario_grupo
            if datos_nuestro is None or datos_rival is None:
                for fila in clasif_temporada:
                    nombre_eq_norm = fila.get('equipo', '').replace('"', '').replace("'", '').strip().upper()
                    if 'FUENTELARREYNA' in nombre_eq_norm and datos_nuestro is None:
                        datos_nuestro = _clasif_fila_a_dict(fila)
                    if datos_rival is None and _rival_coincide(rival_norm, nombre_eq_norm):
                        datos_rival = _clasif_fila_a_dict(fila)

            objetivo = None
            if datos_nuestro and datos_rival:
                objetivo = calcular_objetivo(
                    datos_nuestro.get('posicion'), datos_nuestro.get('puntos'),
                    datos_rival.get('posicion'), datos_rival.get('puntos')
                )

            cumplido = _cumple_objetivo(objetivo, partido.get('estado'))
            ultimos_rival = ultimos_resultados_equipo(calendario_grupo, rival, jornada_num, n=2) if calendario_grupo else []

            resultado_final.append({
                "categoria": categoria,
                "letra": letra,
                "formato": formato,
                "grupo": nombre_grupo,
                "grupo_short": grp_short,
                "jornada": partido.get('jornada'),
                "jornada_num": jornada_num,
                "fecha": partido.get('fecha'),
                "es_local": partido.get('es_local'),
                "rival": rival,
                "rival_shield": partido.get('rival_shield', ''),
                "resultado": partido.get('resultado', ''),
                "estado": partido.get('estado', ''),
                "objetivo": objetivo,
                "cumplido": cumplido,
                "datos_nuestro": datos_nuestro,
                "datos_rival": datos_rival,
                "ultimos_rival": ultimos_rival,
                "datos_approx": bool(datos_nuestro and datos_nuestro.get('_approx')),
                "ida_resultado": ida_partido.get('resultado', '') if ida_partido else None,
                "ida_estado": ida_partido.get('estado', '') if ida_partido else None,
                "ida_jornada": ida_partido.get('jornada', '') if ida_partido else None,
            })

    return resultado_final
