"""Sincroniza ya los datos de la Federación (clasificaciones, calendarios y goleadores) y muestra el resultado.

Uso en el servidor:  cd /var/www/intranet && venv/bin/python sincronizar_rffm.py
"""
import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _cargar_env():
    """Lee el .env del servidor (sin depender de que esté cargado en el entorno)."""
    try:
        with open(os.path.join(BASE_DIR, '.env'), encoding='utf-8') as f:
            for linea in f:
                linea = linea.strip()
                if linea and not linea.startswith('#') and '=' in linea:
                    k, v = linea.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"\''))
    except Exception:
        pass


def credenciales():
    import competicion_scraper as cs
    usuario, password = cs.cargar_credenciales_rffm()
    if not usuario or not password:
        _cargar_env()
        usuario, password = os.environ.get('RFFM_USER'), os.environ.get('RFFM_PASS')
    return usuario, password


if __name__ == '__main__':
    sys.path.insert(0, BASE_DIR)
    import competicion_scraper as cs
    usuario, password = credenciales()
    if not usuario or not password:
        print('No hay usuario/contraseña de la Federación (ni guardados en la intranet ni en .env)')
        sys.exit(1)
    print('Sincronizando con la Federación... (tarda unos minutos)', flush=True)
    ok, mensaje = cs.sync_rffm(usuario, password)
    print(mensaje)
    datos = cs.load_cached_data() or {}
    print('Última actualización:', datos.get('last_updated'))
    print('Grupos:', len(datos.get('equipos', [])), '· goleadores guardados:',
          sum(len(e.get('goleadores') or []) for e in datos.get('equipos', [])))
    sys.exit(0 if ok else 1)
