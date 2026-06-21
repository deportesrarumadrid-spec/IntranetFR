import requests

# Test del proxy de escudos
url_proxy = "http://127.0.0.1:5001/api/shield_proxy"
params = {"url": "https://www.ffmadrid.es/rffm/escudos/club_1125.png"}

r = requests.get(url_proxy, params=params, timeout=15)
ct = r.headers.get("Content-Type", "?")
print(f"Status: {r.status_code} | CT: {ct} | Size: {len(r.content)} bytes")
if "image" in ct:
    print("OK: Es una imagen real")
    with open("test_proxy_shield.png", "wb") as f:
        f.write(r.content)
    print("Guardado en test_proxy_shield.png")
else:
    print("ERROR: No es imagen, respuesta:")
    print(r.text[:300])
