import time
import _thread
from machine import Pin
from servo_control import ServoManager
import wifi_manager
from rfid_reader import MFRC522_Reader

# --- Configuracion de Hardware ---
led = Pin("LED", Pin.OUT)
rfid = MFRC522_Reader(spi_id=0, sck=2, mosi=3, miso=4, cs=1, rst=0)
ttp_1 = Pin(16, Pin.IN, Pin.PULL_DOWN)
ttp_2 = Pin(17, Pin.IN, Pin.PULL_DOWN)
buzzer = Pin(15, Pin.OUT)
servos = ServoManager(pin_servo1=28, pin_servo2=27)

# --- Variables de Estado Global ---
uid_to_verify = None
server_response = None
command_queue_pico = []

# Estados anteriores de los sensores TTP
prev_ttp1_state = 0
prev_ttp2_state = 0

# --- Hilo de Red ---
def network_thread():
    """Hilo secundario que maneja toda la comunicacion de red."""
    global uid_to_verify, server_response, command_queue_pico
    
    print("THREAD RED: Hilo de red iniciado.")
    wifi_manager.connect(led)
    
    while True:
        try:
            if uid_to_verify:
                response = wifi_manager.verify_uid_on_server(uid_to_verify)
                server_response = response
                uid_to_verify = None

            command = wifi_manager.poll_server()
            if command and command.get('command'):
                 print(f"THREAD RED: Comando recibido del servidor: {command}")
                 command_queue_pico.append(command)
        except Exception as e:
            print(f"THREAD RED: Error de red - {e}")
            wifi_manager.connect(led)

        time.sleep_ms(200)

# --- Funciones de Sonido ---
def play_beep(duration_ms=100):
    buzzer.on()
    time.sleep_ms(duration_ms)
    buzzer.off()

def play_error_beep():
    for _ in range(3):
        play_beep(50)
        time.sleep_ms(50)

# --- Arranque ---
_thread.start_new_thread(network_thread, ())
print("HILO PRINCIPAL: Sistema listo. Escaneando RFID...")

# --- Bucle Principal ---
while True:
    # --- Deteccion y Reporte de Eventos de Sensores TTP ---
    current_ttp1_state = ttp_1.value()
    if current_ttp1_state == 1 and prev_ttp1_state == 0:
        print("HILO PRINCIPAL: Detectado inicio de cierre Bandeja 1 (TTP1).")
        wifi_manager.report_event_to_server({"event": "inicio_cierre_1"})
    prev_ttp1_state = current_ttp1_state

    current_ttp2_state = ttp_2.value()
    if current_ttp2_state == 1 and prev_ttp2_state == 0:
        print("HILO PRINCIPAL: Detectado inicio de cierre Bandeja 2 (TTP2).")
        wifi_manager.report_event_to_server({"event": "inicio_cierre_2"})
    prev_ttp2_state = current_ttp2_state

    # --- Logica de Lectura RFID ---
    if not uid_to_verify:
        uid = rfid.read_card()
        if uid:
            print(f"HILO PRINCIPAL: Tarjeta detectada. UID: {uid}. Enviando a hilo de red...")
            uid_to_verify = uid
            play_beep()

    # --- Procesamiento de Respuestas del Servidor ---
    if server_response:
        print(f"HILO PRINCIPAL: Respuesta de verificacion recibida: {server_response}")
        status = server_response.get('status')
        if status == "acceso_denegado": play_error_beep()
        else: play_beep(200)
        server_response = None

    if command_queue_pico:
        
        remote_command = command_queue_pico.pop(0)
        
        command = remote_command.get('command')
        tray = remote_command.get('tray')
        
        if command == 'open':
            if tray == 1: servos.open_tray_1()
            elif tray == 2: servos.open_tray_2()
        elif command == 'close':
            if tray == 1: servos.close_tray_1()
            elif tray == 2: servos.close_tray_2()
        elif command == 'close_all':
            print("HILO PRINCIPAL: Recibido comando de bloqueo final. BLOQUEANDO TODO.")
            servos.close_all_trays()
            play_beep(500)
            wifi_manager.report_event_to_server({"event": "cierre_exitoso_final"})

    time.sleep_ms(50)



