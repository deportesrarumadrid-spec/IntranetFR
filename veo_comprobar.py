"""Comprobación: ¿qué se puede sacar de Veo para un equipo y una fecha? (solo lee, no envía nada)

Uso en el servidor:  cd /var/www/intranet && venv/bin/python veo_comprobar.py "INFANTIL A" 2026-11-07
"""
import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)


def _cargar_env():
    try:
        with open(os.path.join(BASE_DIR, '.env'), encoding='utf-8') as f:
            for linea in f:
                linea = linea.strip()
                if linea and not linea.startswith('#') and '=' in linea:
                    k, v = linea.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"\''))
    except Exception:
        pass


if __name__ == '__main__':
    _cargar_env()
    equipo = sys.argv[1] if len(sys.argv) > 1 else 'INFANTIL A'
    fecha = sys.argv[2] if len(sys.argv) > 2 else '2026-11-07'
    import veo
    if not veo.configurado():
        print('Veo no está configurado (faltan VEO_EMAIL / VEO_PASSWORD en .env)')
        sys.exit(1)
    print(f'== Buscando en Veo: {equipo} · {fecha} ==')
    cands = veo.candidatos(equipo, fecha)
    for c in cands[:5]:
        print(f"  [{c['puntos']} pts] {c['fecha']} · {c['titulo']} · equipo Veo: {c['equipo_veo']} · {c['duracion_min']} min · {', '.join(c['motivos'])}")
    elegido = veo.sugerencia(equipo, fecha)
    if not elegido:
        print('No hay una grabación clara para ese equipo y fecha.')
        sys.exit(1)
    print(f"\n== Elegido: {elegido['titulo']} ({elegido['fecha']}) → {elegido['url']} ==")
    r = veo.resumen_partido(elegido['id'])
    print('\n== Apartados del informe ==')
    print('  ' + ', '.join(sorted(r.keys())))
    texto = veo.texto_para_ia(r)
    print(f'\n== Texto que se le pasaría a la IA ({len(texto)} caracteres) ==')
    print(texto[:3500] + ('\n…' if len(texto) > 3500 else ''))
    try:
        ev = veo.eventos(elegido['id'])
        n = len(ev) if isinstance(ev, list) else len((ev or {}).get('events', []) if isinstance(ev, dict) else [])
        print(f'\n== Eventos (tiros, goles…): {n} ==')
    except Exception as e:
        print(f'\n== Eventos: error {e} ==')
    try:
        v = veo.visual(elegido['id'])
        print('\n== Datos visuales (mapas, calor, pases) ==')
        print('  ' + ', '.join(sorted(v.keys())) if isinstance(v, dict) else '  (sin datos)')
    except Exception as e:
        print(f'\n== Datos visuales: error {e} ==')
