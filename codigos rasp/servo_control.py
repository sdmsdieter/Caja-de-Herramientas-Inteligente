from machine import Pin, PWM
import time

class ServoManager:
    """
    Controla un par de servomotores con logica de proteccion
    y angulos de operacion especificos para cada uno.
    """
    def __init__(self, pin_servo1, pin_servo2):
        """Inicializa los dos servos y los mueve a la posicion de bloqueo."""
        self.servo1_pin = Pin(pin_servo1)
        self.servo2_pin = Pin(pin_servo2)
        
        self.ANGLE_CLOSE_SERVO1 = 180
        self.ANGLE_CLOSE_SERVO2 = 90
        self.ANGLE_OPEN = 0

        print("Inicializando servos en posicion de bloqueo...")
        self.close_all_trays()

    def _set_angle(self, pin_obj, angle):
        """Metodo interno para mover un servo y luego desactivarlo."""
        try:
            pwm = PWM(pin_obj)
            pwm.freq(50)
            min_duty = 1638  
            max_duty = 8191
            duty = min_duty + (angle / 180) * (max_duty - min_duty)
            pwm.duty_u16(int(duty))
            time.sleep_ms(500)
            pwm.deinit()
        except Exception as e:
            print(f"Error al mover el servo en el pin {pin_obj}: {e}")

    def open_tray_1(self):
        self._set_angle(self.servo1_pin, self.ANGLE_OPEN)

    def open_tray_2(self):
        self._set_angle(self.servo2_pin, self.ANGLE_OPEN)
        
    def close_tray_1(self):
        self._set_angle(self.servo1_pin, self.ANGLE_CLOSE_SERVO1)

    def close_tray_2(self):
        self._set_angle(self.servo2_pin, self.ANGLE_CLOSE_SERVO2)
        
    def close_all_trays(self):
        self.close_tray_1()
        self.close_tray_2()



