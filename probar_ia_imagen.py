"""Prueba qué modelos de imagen de Gemini funcionan con la clave del club (gratis) y si Pollinations está configurado.

Uso en el servidor:  venv/bin/python probar_ia_imagen.py
No muestra ninguna clave. Guarda en datos_privados/gol/modelos_imagen.json los modelos de Gemini que han funcionado.
"""
import os
import json
import base64
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, '.env'))
except Exception:
    pass


def main():
    import gol_ig_wa as gi
    clave = gi._clave_gemini()
    if not clave:
        print('Gemini: no hay clave configurada')
        return
    r = requests.get('https://generativelanguage.googleapis.com/v1beta/models', params={'key': clave, 'pageSize': 300}, timeout=30)
    modelos = [m['name'] for m in r.json().get('models', []) if 'generateContent' in (m.get('supportedGenerationMethods') or [])
               and ('image' in m['name'] or 'exp' in m['name'] or '2.0-flash' in m['name'])]
    print(f'Gemini: {len(modelos)} modelos candidatos')
    buenos = []
    for nombre in modelos:
        cuerpo = {'contents': [{'parts': [{'text': 'A small red football on green grass, simple illustration'}]}],
                  'generationConfig': {'responseModalities': ['TEXT', 'IMAGE']}}
        try:
            rr = requests.post(f'https://generativelanguage.googleapis.com/v1beta/{nombre}:generateContent',
                               params={'key': clave}, json=cuerpo, timeout=120)
            j = rr.json()
            img = any((p.get('inline_data') or p.get('inlineData')) for c in j.get('candidates', [])
                      for p in (c.get('content') or {}).get('parts', []))
            if rr.status_code == 200 and img:
                print(f'  ✅ {nombre}: FUNCIONA (gratis con esta clave)')
                buenos.append(nombre)
            else:
                msg = ((j.get('error') or {}).get('message') or ('sin imagen' if rr.status_code == 200 else rr.text))[:90]
                print(f'  ❌ {nombre}: {msg}')
        except Exception as e:
            print(f'  ❌ {nombre}: {str(e)[:90]}')
    os.makedirs(os.path.join(BASE_DIR, 'datos_privados', 'gol'), exist_ok=True)
    with open(os.path.join(BASE_DIR, 'datos_privados', 'gol', 'modelos_imagen.json'), 'w', encoding='utf-8') as f:
        json.dump({'gemini': buenos}, f)
    pk = os.environ.get('POLLINATIONS_KEY', '').strip()
    if not pk:
        print('Pollinations: sin clave (POLLINATIONS_KEY) todavía')
        return
    rr = requests.get('https://gen.pollinations.ai/account/balance', headers={'Authorization': f'Bearer {pk}'}, timeout=30)
    print(f'Pollinations: clave {"válida" if rr.status_code == 200 else "NO válida"} · saldo: {rr.text[:120]}')


if __name__ == '__main__':
    main()
