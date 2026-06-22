fields_p1 = {
    "JUGADOR_NOMBRE": (100, 150, 240, 168),
    "JUGADOR_APELLIDOS": (280, 150, 480, 168),
    "JUGADOR_COLEGIO": (500, 150, 620, 168),
    "JUGADOR_FECHA_NACIMIENTO": (130, 175, 260, 192),
    "JUGADOR_EQUIPO": (300, 175, 480, 192),
    "JUGADOR_LETRA": (520, 175, 620, 192),
    "JUGADOR_DOMICILIO": (100, 198, 520, 215),
    "JUGADOR_CP": (560, 198, 620, 215),
    "JUGADOR_TEL_FIJO": (130, 222, 260, 239),
    "JUGADOR_MOVIL": (290, 222, 430, 239),
    "JUGADOR_EMAIL": (460, 222, 620, 239),
    "PADRE_NOMBRE": (120, 246, 360, 263),
    "MADRE_NOMBRE": (440, 246, 620, 263),
    
    "CH_PAGO_OFICINA": (125, 321, 137, 333),
    "CH_PAGO_DOMICILIADO": (125, 344, 137, 356),
    
    "SEPA_NOMBRE_DEUDOR": (220, 625, 620, 642),
    "SEPA_DIRECCION_DEUDOR": (220, 648, 620, 665),
    "SEPA_CP_POBLACION_CIUDAD": (220, 671, 560, 688),
    "SEPA_PAIS": (580, 671, 620, 688),
    "SEPA_SWIFT_BIC": (170, 694, 360, 711),
    "SEPA_IBAN": (330, 717, 620, 734),
    
    "CH_SEPA_RECURRENTE": (96, 750, 108, 762),
    "CH_SEPA_UNICO": (96, 770, 108, 782),
    
    "SEPA_FECHA_LOCALIDAD": (200, 831, 480, 848),
    "SEPA_FIRMA": (440, 792, 595, 878)
}

fields_p2 = {
    "AUTORIZA_TUTOR_NOMBRE": (100, 142, 400, 159),
    "AUTORIZA_TUTOR_DNI": (430, 142, 580, 159),
    "AUTORIZA_JUGADOR_NOMBRE": (300, 166, 620, 183),
    "CIERRE_FECHA_LOCALIDAD": (200, 831, 480, 848), # Align with Page 1 coordinates for consistency
    "AUTORIZA_FIRMA": (195, 810, 445, 896)
}

print("Page 1 Percentages (W=634, H=898):")
for name, (x1, y1, x2, y2) in fields_p1.items():
    left = (x1 / 634.0) * 100
    top = (y1 / 898.0) * 100
    w = ((x2 - x1) / 634.0) * 100
    h = ((y2 - y1) / 898.0) * 100
    print(f"{name}: left: {left:.2f}%; top: {top:.2f}%; width: {w:.2f}%; height: {h:.2f}%;")

print("\nPage 2 Percentages (W=634, H=900):")
for name, (x1, y1, x2, y2) in fields_p2.items():
    left = (x1 / 634.0) * 100
    top = (y1 / 900.0) * 100
    w = ((x2 - x1) / 634.0) * 100
    h = ((y2 - y1) / 900.0) * 100
    print(f"{name}: left: {left:.2f}%; top: {top:.2f}%; width: {w:.2f}%; height: {h:.2f}%;")
