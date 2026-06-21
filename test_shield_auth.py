"""
Prueba de descarga de escudos desde intranet.ffmadrid.es con sesión autenticada.
Ejecutar una vez para verificar qué URL funciona para los escudos.
"""
import requests
import re

def get_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://intranet.ffmadrid.es/"
    })
    return s

# ---- AJUSTA CREDENCIALES AQUÍ ----
USERNAME = "TU_USUARIO"
PASSWORD = "TU_PASS"
# ----------------------------------

session = get_session()
res_login = session.post(
    "https://intranet.ffmadrid.es/nfg/NLogin",
    data={"NUser": USERNAME, "NPass": PASSWORD, "LoginAjax": "1"}
)
match_est = re.search(r'var estado="(\d+)"', res_login.text)
estado = match_est.group(1) if match_est else "2"
print(f"Login status: {estado} ({'OK' if estado == '1' else 'FAIL'})")

if estado == "1":
    # Intentar descargar escudo de Rupe Sahagún (club 1125)
    test_urls = [
        "https://intranet.ffmadrid.es/rffm/escudos/club_1125.png",
        "https://intranet.ffmadrid.es/nfg/publico/escudos/club_1125.png",
        "https://intranet.ffmadrid.es/nfg/NPcd/NFG_Escudo?codclub=1125",
        "https://intranet.ffmadrid.es/nfg/publico/fotos/escudos/1125.png",
    ]
    for url in test_urls:
        try:
            r = session.get(url, timeout=8)
            ct = r.headers.get("Content-Type", "?")
            size = len(r.content)
            is_img = "image" in ct
            print(f"[{r.status_code}] {'IMAGE' if is_img else 'HTML'} {size}B  {url}")
            if is_img and size > 500:
                with open("test_shield_rupe.png", "wb") as f:
                    f.write(r.content)
                print("  -> Guardado como test_shield_rupe.png")
        except Exception as e:
            print(f"[ERROR] {url}: {e}")
