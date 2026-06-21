import requests

# Test 1: URL pública www.ffmadrid.es
urls_test = [
    "https://www.ffmadrid.es/rffm/escudos/club_1125.png",
    "https://intranet.ffmadrid.es/rffm/escudos/club_1125.png",
    "https://www.ffmadrid.es/rffm/escudos/escudo_default.png",
]

for url in urls_test:
    try:
        r = requests.get(url, timeout=5)
        ct = r.headers.get("Content-Type", "?")
        print(f"[{r.status_code}] {url[:60]} | CT: {ct} | {len(r.content)} bytes")
    except Exception as e:
        print(f"[ERROR] {url[:60]} | {e}")
