from mfrc522 import MFRC522

class MFRC522_Reader:
    def __init__(self, spi_id, sck, miso, mosi, cs, rst):
        self.lector = MFRC522(spi_id=spi_id, sck=sck, miso=miso, mosi=mosi, cs=cs, rst=rst)
        self.lector.init()
        print("Lector RFID inicializado.")

    def read_uid(self):
        """Escanea por una tarjeta y devuelve su UID como un entero."""
        (stat, tag_type) = self.lector.request(self.lector.REQIDL)
        if stat == self.lector.OK:
            (stat, uid) = self.lector.SelectTagSN()
            if stat == self.lector.OK:
                return int.from_bytes(bytes(uid), "little", False)
        return None
