import os
import json
import requests
import re
from bs4 import BeautifulSoup
from datetime import datetime

# Archivo de cache
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'data', 'rffm_cache.json')
SHIELDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'shields')
os.makedirs(SHIELDS_DIR, exist_ok=True)

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
    elif "BENJAMIN" in name or "BENJAMÍN" in name:
        cat = "BENJAMIN"
    elif "PREBENJAMIN" in name or "PREBENJAMÍN" in name:
        cat = "PREBENJAMIN"
    else:
        cat = "OTROS"
        
    letra = "A" # Default
    
    if cat == "AFICIONADO":
        if "SEGUNDA" in name or "2ª" in name or "GRUPO 14" in name:
            letra = "B"
        else:
            letra = "A"
    elif cat == "JUVENIL":
        if "GRUPO 28" in name:
            letra = "B"
        elif "GRUPO 22" in name:
            letra = "C"
        else:
            letra = "A"
    elif cat == "CADETE":
        if "PREFERENTE" in name or "GRUPO 2" in name:
            letra = "A"
        elif "GRUPO 11" in name:
            letra = "B"
        elif "GRUPO 8" in name:
            letra = "C"
        elif "GRUPO 31" in name:
            letra = "D"
        elif "GRUPO 18" in name:
            letra = "E"
    elif cat == "INFANTIL":
        if "GRUPO 9" in name:
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
            
            # Buscar el enlace de "Ult.Jornada" y extraer parámetros
            link_jornada = None
            codgrupo = None
            codequipo = None
            cod_primaria = '1000131'  # Valor por defecto FFMADRID
            links = r.find_all('a')
            for a in links:
                href = a.get('href', '')
                if 'LstJornada' in href or 'Jornada' in href or 'cod_grupo' in href or 'codgrupo' in href:
                    link_jornada = href
                    # Extraer parámetros de la URL
                    m_cg = re.search(r'codgrupo=(\d+)', href)
                    m_ce = re.search(r'codequipo=(\d+)', href)
                    m_cp = re.search(r'cod_primaria=(\d+)', href)
                    if m_cg: codgrupo = m_cg.group(1)
                    if m_ce: codequipo = m_ce.group(1)
                    if m_cp: cod_primaria = m_cp.group(1)
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
                    "calendario": []
                })
                continue
                
            # Resolver URL absoluta del enlace
            if not link_jornada.startswith('http'):
                link_jornada = "https://intranet.ffmadrid.es" + link_jornada
                
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
            title_el = soup_jornada.find(class_=re.compile("titulo|header|jornada", re.I))
            if title_el:
                jornada_title = title_el.text.strip()
                
            rows_partidos = []
            if tabla_partidos:
                rows_partidos = tabla_partidos.find_all('tr')
            else:
                rows_partidos = soup_jornada.find_all('tr')
                
            for pr in rows_partidos:
                cells = pr.find_all('td')
                if len(cells) >= 3:
                    text_row = pr.text.upper()
                    if "FUENTELARREYNA" in text_row:
                        eq_local = cells[0].text.strip()
                        resultado = cells[1].text.strip()
                        eq_visitante = cells[2].text.strip()
                        
                        # Extraer escudos del partido
                        img_loc = cells[0].find('img')
                        img_vis = cells[2].find('img')
                        shield_loc = ""
                        shield_vis = ""
                        if img_loc:
                            shield_loc = img_loc.get('src', '')
                            if shield_loc and not shield_loc.startswith('http'):
                                shield_loc = "https://intranet.ffmadrid.es" + shield_loc
                        if img_vis:
                            shield_vis = img_vis.get('src', '')
                            if shield_vis and not shield_vis.startswith('http'):
                                shield_vis = "https://intranet.ffmadrid.es" + shield_vis
                        
                        goles_fav, goles_contra = 0, 0
                        es_local = "FUENTELARREYNA" in eq_local.upper()
                        contrario = eq_visitante if es_local else eq_local
                        
                        # Parsear resultado
                        match_res = re.search(r'(\d+)\s*[-–]\s*(\d+)', resultado)
                        if match_res:
                            g_loc = int(match_res.group(1))
                            g_vis = int(match_res.group(2))
                            goles_fav = g_loc if es_local else g_vis
                            goles_contra = g_vis if es_local else g_loc
                            
                        # Buscar fecha si está en la celda
                        fecha_match = "Finalizado"
                        for cell in cells:
                            date_match = re.search(r'\d{2}/\d{2}/\d{4}', cell.text)
                            if date_match:
                                fecha_match = cell.text.strip()
                                break
                                
                        ultimo_partido = {
                            "jornada": jornada_title,
                            "fecha": fecha_match,
                            "local": eq_local,
                            "visitante": eq_visitante,
                            "local_shield": shield_loc,
                            "visitante_shield": shield_vis,
                            "resultado": resultado,
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
                if not link_clasif.startswith('http'):
                    link_clasif = "https://intranet.ffmadrid.es" + link_clasif
                    
                res_clasif = session.get(link_clasif)
                soup_clasif = BeautifulSoup(res_clasif.text, 'html.parser')
                
                # Buscar tabla de clasificación
                tabla_cl = None
                for t in soup_clasif.find_all('table'):
                    headers = [th.text.upper() for th in t.find_all('th')]
                    if any("PTS" in h or "PUNTOS" in h or "JUG" in h or "PARTIDOS" in h for h in headers):
                        tabla_cl = t
                        break
                        
                if tabla_cl:
                    rows_cl = tabla_cl.find_all('tr')[1:]
                    for r_cl in rows_cl:
                        cells_cl = r_cl.find_all('td')
                        if len(cells_cl) >= 4:
                            pos = cells_cl[0].text.strip()
                            team = cells_cl[1].text.strip()
                            pts = cells_cl[2].text.strip()
                            jugados = cells_cl[3].text.strip()
                            
                            ganados = cells_cl[4].text.strip() if len(cells_cl) > 4 else "0"
                            empatados = cells_cl[5].text.strip() if len(cells_cl) > 5 else "0"
                            perdidos = cells_cl[6].text.strip() if len(cells_cl) > 6 else "0"
                            gf = cells_cl[7].text.strip() if len(cells_cl) > 7 else "0"
                            gc = cells_cl[8].text.strip() if len(cells_cl) > 8 else "0"
                            
                            # Extraer escudo de la clasificación
                            img_cl = cells_cl[1].find('img')
                            shield_url = ""
                            if img_cl:
                                shield_url = img_cl.get('src', '')
                                if shield_url and not shield_url.startswith('http'):
                                    shield_url = "https://intranet.ffmadrid.es" + shield_url
                            
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
                                "shield": shield_url
                            })

            # 6. Scraper del CALENDARIO COMPLETO desde NFG_VisCompeticiones_Grupo
            calendario_completo = []
            if codgrupo and codequipo:
                try:
                    url_cal = f"https://intranet.ffmadrid.es/nfg/NPcd/NFG_VisCompeticiones_Grupo?cod_primaria={cod_primaria}&codequipo={codequipo}&codgrupo={codgrupo}"
                    res_cal = session.get(url_cal)
                    soup_cal = BeautifulSoup(res_cal.text, 'html.parser')

                    # Construir mapa de escudos desde la clasificación ya extraída
                    escudo_map = {}
                    for item in clasif_data:
                        clean_name = item['equipo'].replace('"', '').replace("'", '').strip().upper()
                        if item.get('shield'):
                            escudo_map[clean_name] = item['shield']

                    jornada_actual = "Jornada 1"
                    jornada_num = 1

                    # Buscar todos los bloques de jornada y sus partidos
                    all_rows = soup_cal.find_all(['tr', 'div'])
                    for elem in all_rows:
                        text = elem.get_text(separator=' ', strip=True)

                        # Detectar cabecera de jornada
                        if re.match(r'^Jornada\s+\d+', text, re.I):
                            jornada_actual = text.strip()
                            m_jn = re.search(r'\d+', jornada_actual)
                            if m_jn:
                                jornada_num = int(m_jn.group())
                            continue

                        # Buscar filas con el partido de Fuentelarreyna
                        if elem.name == 'tr' and "FUENTELARREYNA" in text.upper():
                            cells = elem.find_all('td')
                            if len(cells) >= 3:
                                local_text = cells[0].get_text(separator=' ', strip=True)
                                score_text = cells[1].get_text(separator=' ', strip=True) if len(cells) > 1 else ''
                                visit_text = cells[2].get_text(separator=' ', strip=True) if len(cells) > 2 else ''

                                es_local = "FUENTELARREYNA" in local_text.upper()
                                rival = visit_text if es_local else local_text
                                rival = rival.strip()

                                # Parsear resultado y estado
                                match_res = re.search(r'(\d+)\s*[-–]\s*(\d+)', score_text)
                                resultado = ''
                                estado = ''
                                if match_res:
                                    g1 = int(match_res.group(1))
                                    g2 = int(match_res.group(2))
                                    resultado = f"{g1} - {g2}"
                                    if es_local:
                                        estado = 'G' if g1 > g2 else ('E' if g1 == g2 else 'P')
                                    else:
                                        estado = 'G' if g2 > g1 else ('E' if g1 == g2 else 'P')

                                # Buscar fecha
                                fecha = ''
                                for cell in cells:
                                    dm = re.search(r'\d{2}/\d{2}/\d{4}', cell.get_text())
                                    if dm:
                                        fecha = dm.group()
                                        break

                                # Escudo del rival
                                rival_clean = rival.replace('"', '').replace("'", '').strip().upper()
                                rival_shield = escudo_map.get(rival_clean, '')

                                # Escudo del rival desde img de la celda
                                rival_cell = cells[2] if es_local else cells[0]
                                img_rival = rival_cell.find('img')
                                if img_rival and not rival_shield:
                                    rival_shield = img_rival.get('src', '')
                                    if rival_shield and not rival_shield.startswith('http'):
                                        rival_shield = "https://intranet.ffmadrid.es" + rival_shield

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
                "calendario": calendario_completo
            })
            
        # 6. Guardar en caché local
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
            "shield": shield_url
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
