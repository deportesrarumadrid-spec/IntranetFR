"""Impresión automática de las clasificaciones en la impresora de la oficina (Canon iX6800, por WiFi).

El servidor no llega a la impresora: un pequeño programa en el PC de la oficina (tarea programada cada 5 min) pregunta
aquí si toca imprimir. Toca el lunes desde las 15:00 y el martes, si esa semana aún no se ha impreso.
- Impresora encendida → abre /clasificaciones/pdf con autoprint (Chrome --kiosk-printing) e imprime todo, y avisa.
- Impresora apagada → aviso por WhatsApp a Rubén Pozón y Raúl Lucas (una vez por semana); se imprime al encenderla.
Estado en datos_privados/impresion_clasif.json. El PC se identifica con una clave derivada de FLASK_SECRET_KEY.
"""
import os
import json
import hashlib
import unicodedata
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, Response

impresion_bp = Blueprint('impresion_clasif', __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'datos_privados', 'impresion_clasif.json')
URL_BASE = 'https://intranet.clubfuentelarreyna.com'
IMPRESORA = 'Canon iX6800 series'
MAC_IMPRESORA = '6C-F2-D8-74-1A-40'
DESTINATARIOS = ('RUBEN POZON', 'RAUL LUCAS')


def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
    return ' '.join(s.upper().split())


def clave():
    return hashlib.sha256(((os.environ.get('FLASK_SECRET_KEY') or '') + '|imprimir_clasif').encode()).hexdigest()[:32]


def _clave_ok():
    k = request.args.get('k') or ''
    return bool(k) and secrets_compare(k, clave())


def secrets_compare(a, b):
    import hmac
    return hmac.compare_digest(a, b)


def _leer():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault('semanas', {})
    return d


def _escribir(d):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def _semana(ahora):
    return (ahora.date() - timedelta(days=ahora.weekday())).isoformat()


def toca(ahora, d):
    if d.get('prueba'):
        return True
    en_horario = (ahora.weekday() == 0 and ahora.hour >= 15) or ahora.weekday() == 1
    return en_horario and not d['semanas'].get(_semana(ahora), {}).get('impreso')


def _telefonos(solo_ruben=False):
    from app import client, NOMBRE_EXCEL
    p = client.open(NOMBRE_EXCEL).worksheet('PERFILES').get_all_values()
    h = [x.strip().upper() for x in p[0]]
    i_u, i_t = h.index('USUARIO'), h.index('TELEFONO')
    out = []
    for buscado in (('RUBEN POZON',) if solo_ruben else DESTINATARIOS):
        r = next((r for r in p[1:] if len(r) > max(i_u, i_t) and _norm(r[i_u]) == buscado and r[i_t].strip()), None)
        if r:
            out.append(r[i_t].strip())
    return out


def _avisar(texto, prueba):
    from app import enviar_whatsapp
    for tel in _telefonos(solo_ruben=prueba):
        enviar_whatsapp(tel, ('🧪 PRUEBA. ' if prueba else '') + texto)


# ---------- lo que pregunta el PC de la oficina ----------

@impresion_bp.route('/api/impresion/estado')
def api_estado_pc():
    if not _clave_ok():
        return jsonify({'status': 'error'}), 403
    ahora = datetime.now()
    d = _leer()
    d['ultimo_contacto'] = ahora.isoformat(timespec='minutes')
    _escribir(d)
    return jsonify({'status': 'ok', 'toca': toca(ahora, d), 'semana': _semana(ahora),
                    'url': f'{URL_BASE}/clasificaciones/pdf?k={clave()}&autoprint=1'})


@impresion_bp.route('/api/impresion/aviso', methods=['POST'])
def api_aviso_pc():
    if not _clave_ok():
        return jsonify({'status': 'error'}), 403
    evento = (request.json or {}).get('evento')
    ahora = datetime.now()
    d = _leer()
    prueba = bool(d.get('prueba'))
    sem = d['semanas'].setdefault(_semana(ahora), {})
    if evento == 'apagada':
        if prueba or not sem.get('aviso_apagada'):
            if not prueba:
                sem['aviso_apagada'] = ahora.isoformat(timespec='minutes')
            _escribir(d)
            _avisar('🖨️ La impresora de la oficina está apagada. Enciéndela y se imprimirán solas las clasificaciones '
                    'de esta semana.\n\nDirección Deportiva', prueba)
    elif evento == 'impreso':
        if prueba:
            d.pop('prueba', None)
        else:
            sem['impreso'] = ahora.isoformat(timespec='minutes')
        d['ultima_impresion'] = ahora.isoformat(timespec='minutes')
        _escribir(d)
        _avisar('✅ Clasificaciones de la semana impresas en la oficina.\n\nDirección Deportiva', prueba)
    elif evento == 'error':
        d['ultimo_error'] = f"{ahora.isoformat(timespec='minutes')} {(request.json or {}).get('detalle', '')}"[:200]
        _escribir(d)
    print(f'[IMPRESION] {evento}{" (prueba)" if prueba else ""}', flush=True)
    return jsonify({'status': 'ok'})


@impresion_bp.route('/api/impresion/script.ps1')
def api_script():
    if not _clave_ok():
        return Response('', status=403)
    return Response(_SCRIPT.replace('__BASE__', URL_BASE).replace('__K__', clave())
                    .replace('__IMPRESORA__', IMPRESORA).replace('__MAC__', MAC_IMPRESORA),
                    mimetype='text/plain; charset=utf-8')


@impresion_bp.route('/api/impresion/instalar.ps1')
def api_instalar():
    if not _clave_ok():
        return Response('', status=403)
    return Response(_INSTALADOR.replace('__BASE__', URL_BASE).replace('__K__', clave()),
                    mimetype='text/plain; charset=utf-8')


# ---------- pantalla (pestaña WHATSAPP) ----------

def _permitido():
    from whatsapp_envios import _permitido as p
    return p()


@impresion_bp.route('/api/impresion', methods=['GET', 'POST'])
def api_pantalla():
    if not _permitido():
        return jsonify({'status': 'error', 'message': 'Sin permiso'}), 403
    d = _leer()
    if request.method == 'POST' and (request.json or {}).get('accion') == 'prueba':
        d['prueba'] = datetime.now().isoformat(timespec='minutes')
        _escribir(d)
    return jsonify({'status': 'ok', 'ultimo_contacto': d.get('ultimo_contacto', ''),
                    'ultima_impresion': d.get('ultima_impresion', ''), 'prueba': bool(d.get('prueba')),
                    'ultimo_error': d.get('ultimo_error', ''),
                    'comando': f'irm "{URL_BASE}/api/impresion/instalar.ps1?k={clave()}" | iex'})


# ---------- programas del PC (PowerShell) ----------

_INSTALADOR = r'''
$Dir = Join-Path $env:LOCALAPPDATA 'FRImpresion'
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri '__BASE__/api/impresion/script.ps1?k=__K__' -OutFile (Join-Path $Dir 'imprimir.ps1')
$ps1 = Join-Path $Dir 'imprimir.ps1'
Set-Content -Path (Join-Path $Dir 'run.vbs') -Value ('CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""' + $ps1 + '""", 0, False')
$accion = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"' + (Join-Path $Dir 'run.vbs') + '"')
$disparo = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName 'FR Imprimir clasificaciones' -Action $accion -Trigger $disparo -Settings $ajustes -Force | Out-Null
Write-Host 'INSTALADO' -ForegroundColor Green
'''

_SCRIPT = r'''# Club Fuentelarreyna: imprime las clasificaciones el lunes (desde las 15:00) y el martes si aún no se han impreso.
$ErrorActionPreference = 'Continue'
$Base = '__BASE__'; $K = '__K__'
$Impresora = '__IMPRESORA__'; $Mac = '__MAC__'
$Dir = Join-Path $env:LOCALAPPDATA 'FRImpresion'
$Log = Join-Path $Dir 'log.txt'
function Log($m) { Add-Content -Path $Log -Value ('{0:yyyy-MM-dd HH:mm} {1}' -f (Get-Date), $m) }
function Avisar($evento, $detalle) {
    try { Invoke-RestMethod -Uri "$Base/api/impresion/aviso?k=$K" -Method Post -ContentType 'application/json' `
            -Body (@{ evento = $evento; detalle = "$detalle" } | ConvertTo-Json) -TimeoutSec 30 | Out-Null } catch { Log "aviso $evento fallido: $_" }
}

try { $e = Invoke-RestMethod -Uri "$Base/api/impresion/estado?k=$K" -TimeoutSec 30 } catch { exit }
if (-not $e.toca) { exit }

# ¿Impresora encendida? (por WiFi: responde al ping; la IP se busca por su MAC si cambia)
$ipFile = Join-Path $Dir 'ip.txt'
$ip = Get-Content $ipFile -ErrorAction SilentlyContinue
if (-not ($ip -and (Test-Connection -ComputerName $ip -Count 2 -Quiet))) {
    $ip = $null
    $n = Get-NetNeighbor -LinkLayerAddress $Mac -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($n -and (Test-Connection -ComputerName $n.IPAddress -Count 2 -Quiet)) { $ip = $n.IPAddress; Set-Content $ipFile $ip }
}
if (-not $ip) { Log 'impresora apagada'; Avisar 'apagada' ''; exit }

$nav = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
         "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe") |
       Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $nav) { Log 'no encuentro Chrome ni Edge'; Avisar 'error' 'no encuentro Chrome ni Edge'; exit }

$red = New-Object -ComObject WScript.Network
$antes = (Get-CimInstance Win32_Printer -Filter 'Default=TRUE' -ErrorAction SilentlyContinue).Name
try { $red.SetDefaultPrinter($Impresora) } catch { Log "no puedo elegir la impresora: $_"; Avisar 'error' 'impresora no instalada'; exit }
$perfil = Join-Path $Dir 'navegador'
$p = Start-Process -FilePath $nav -PassThru -ArgumentList @('--kiosk-printing', "--user-data-dir=`"$perfil`"", '--no-first-run',
        '--no-default-browser-check', '--disable-extensions', "--app=$($e.url)")
$p | Wait-Process -Timeout 240 -ErrorAction SilentlyContinue
$cerrado = $p.HasExited
if (-not $cerrado) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
if ($antes -and $antes -ne $Impresora) { try { $red.SetDefaultPrinter($antes) } catch {} }
# Se da por impreso aunque la ventana no se cerrara sola: mejor eso que repetir 11 hojas cada 5 minutos
if (-not $cerrado) { Log 'la ventana no se cerró sola' }
Log 'impreso'; Avisar 'impreso' ''
'''
