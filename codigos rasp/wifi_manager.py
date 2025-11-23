import network
import time
import secrets
import urequests
import ujson
from machine import Pin, reset

def connect(led):
    """Intenta conectarse a la red Wi-Fi de forma persistente."""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    time.sleep(1)

    wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASSWORD)
    
    max_wait = 60
    
    while max_wait > 0:
        if wlan.status() < 0 or wlan.status() >= 3:
            break
        max_wait -= 1
        print('Esperando conexion...')
        led.toggle()
        time.sleep(1)

    if wlan.status() != 3:
        print("No se pudo conectar. Reiniciando en 5 segundos...")
        time.sleep(5)
        reset() # Fuerza un reinicio si la conexión falla
    else:
        print('Conectado.')
        status = wlan.ifconfig()
        print('IP = ' + status[0])
        led.on()
        return True

def report_event_to_server(event_data):
    """Envía un evento de sensor al servidor."""
    url = f"http://{secrets.SERVER_IP}:5000/report_event"
    headers = {'Content-Type': 'application/json'}
    try:
        response = urequests.post(url, data=ujson.dumps(event_data), headers=headers)
        response.close()
        return True
    except Exception as e:
        print(f"Error al reportar evento: {e}")
        return False

def verify_uid_on_server(uid):
    """Envía un UID al servidor para su verificación."""
    url = f"http://{secrets.SERVER_IP}:5000/verificar_rfid"
    headers = {'Content-Type': 'application/json'}
    data = {'uid': str(uid)}
    
    try:
        response = urequests.post(url, data=ujson.dumps(data), headers=headers)
        response_data = response.json()
        response.close()
        return response_data
    except Exception as e:
        print(f"Error al verificar UID: {e}")
        return {"status": "error", "message": str(e)}

def poll_server():
    """Pide al servidor si hay algún comando pendiente."""
    url = f"http://{secrets.SERVER_IP}:5000/poll_command"
    try:
        response = urequests.get(url, timeout=5) 
        command_data = response.json()
        response.close()
        return command_data
    except Exception:
        # Es normal que esto falle a veces, no se imprime el error.
        return None


