from competicion_scraper import load_cached_data

data = load_cached_data()
eq_afic = next((e for e in data['equipos'] if 'PRIMERA AFICIONADO' in e['nombre'].upper()), None)
if eq_afic:
    cal = eq_afic.get('calendario', [])
    print(f"Total partidos Aficionado A: {len(cal)}")
    shields_ok = 0
    shields_missing = 0
    for p in cal:
        shield = p.get('rival_shield', '')
        if shield:
            shields_ok += 1
        else:
            shields_missing += 1
        if p == cal[0] or p == cal[-1]:
            print(f"  Jornada={p['jornada']} | Rival={p['rival']} | Shield={'OK: '+shield[:50] if shield else 'FALTA'}")
    print(f"  Escudos OK: {shields_ok} / Missing: {shields_missing}")
else:
    print("Equipo Aficionado A no encontrado")
