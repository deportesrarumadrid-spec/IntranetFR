"""Revisión rápida (solo lectura) de los avisos de WhatsApp en el servidor.

Uso en el servidor:  cd /var/www/intranet && venv/bin/python revisar_whatsapp.py
No importa app (no arranca nada), no envía nada y no muestra contraseñas ni teléfonos.
"""
import os
import re
import json
import subprocess
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DP = os.path.join(BASE, 'datos_privados')


def corto(v):
    if isinstance(v, dict):
        v = v.get('fecha') or v.get('texto') or json.dumps(v, ensure_ascii=False)
    return str(v)[:70]


print('=== WhatsApp del club (WAHA) ===')
try:
    out = subprocess.run(['docker', 'ps', '-a', '--format', '{{.Names}}  {{.Status}}'], capture_output=True, text=True, timeout=20).stdout
    print('\n'.join(l for l in out.splitlines() if 'waha' in l.lower()) or 'No encuentro el contenedor de WAHA')
except Exception as e:
    print(f'docker: {e}')

print('\n=== Avisos (ACTIVO y última vez) ===')
for f in sorted(os.listdir(DP)):
    if not f.endswith('.json'):
        continue
    try:
        with open(os.path.join(DP, f), encoding='utf-8') as fh:
            d = json.load(fh)
    except Exception as e:
        print(f'{f:32} ERROR leyendo: {e}')
        continue
    if not isinstance(d, dict) or 'activo' not in d:
        continue
    ult = [f'{k}={corto(v)}' for k, v in d.items() if k.startswith('ultim') and v]
    print(f"{f[:-5]:28} {'ACTIVO' if d['activo'] else 'apagado':8} {' | '.join(ult)[:150]}")

print('\n=== Fallos de las últimas 24 h ===')
try:
    log = subprocess.run(['journalctl', '-u', 'intranet', '--since', '-24h', '--no-pager', '-o', 'cat'],
                         capture_output=True, text=True, timeout=60).stdout
    malos = [l for l in log.splitlines()
             if re.search(r'no enviad|not sent|waha|traceback|exception|⚠|\[SCHED\].*:|error', l, re.I)
             and not re.search(r'PUSH DEBUG|convocatorias_report', l)]
    malos = [re.sub(r'\b(34)?[67]\d{8}\b', '<tel>', l) for l in malos]
    print('\n'.join(malos[-40:]) or 'Ninguno')
except Exception as e:
    print(f'journalctl: {e}')
print(f'\nRevisado {datetime.now():%d/%m %H:%M}')
