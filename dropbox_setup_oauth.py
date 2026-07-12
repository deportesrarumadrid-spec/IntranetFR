"""
Ejecuta este script UNA SOLA VEZ para obtener un refresh_token permanente.
Después de ejecutarlo, ya no necesitarás renovar el token nunca más.
"""
import json, os
from dropbox import DropboxOAuth2FlowNoRedirect

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dropbox_config.json')

def main():
    print("=" * 55)
    print("  CONFIGURACIÓN DROPBOX - TOKEN PERMANENTE")
    print("=" * 55)
    print()
    print("Necesitas el App Key y App Secret de tu app Dropbox.")
    print("Los encuentras en: https://www.dropbox.com/developers/apps")
    print("→ Selecciona IntranetClub → pestaña Settings")
    print()

    app_key    = input("App Key    : ").strip()
    app_secret = input("App Secret : ").strip()

    if not app_key or not app_secret:
        print("Error: debes introducir App Key y App Secret.")
        return

    auth_flow = DropboxOAuth2FlowNoRedirect(
        app_key, app_secret, token_access_type='offline'
    )
    authorize_url = auth_flow.start()

    print()
    print("1. Abre este enlace en tu navegador:")
    print()
    print("  ", authorize_url)
    print()
    print("2. Haz clic en 'Permitir' (o 'Allow')")
    print("3. Copia el código que aparece en pantalla")
    print()

    auth_code = input("Pega aquí el código: ").strip()

    try:
        oauth_result = auth_flow.finish(auth_code)
    except Exception as e:
        print(f"\nError al verificar el código: {e}")
        return

    # Leer config existente para conservar dropbox_folder
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    cfg['app_key']        = app_key
    cfg['app_secret']     = app_secret
    cfg['refresh_token']  = oauth_result.refresh_token
    # Borrar el token corto si existía
    cfg.pop('access_token', None)

    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    print()
    print("✅ ¡Listo! refresh_token guardado en dropbox_config.json")
    print("   Ya no necesitarás renovar el token nunca más.")

if __name__ == '__main__':
    main()
