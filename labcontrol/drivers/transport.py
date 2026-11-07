import struct
import time
import libusb_package
import usb.core
import usb.util
from pyvisa_py.protocols.usbtmc import USBTMC


class WaveStationUSB(USBTMC):
    """Single-packet reads: verified against WaveStation 2012 on Apple M4."""
    def read(self, size):
        result = bytearray()
        packet = self.usb_recv_ep.wMaxPacketSize
        for _ in range(128):
            wanted = min(256, size - len(result))
            if wanted <= 0:
                raise RuntimeError("Ответ генератора превысил разрешённый размер")
            self._btag = self._btag % 255 + 1
            tag = self._btag
            request = struct.pack("<BBBBIBBBB", 2, tag, tag ^ 255, 0, wanted, 0, 0, 0, 0)
            self.usb_send_ep.write(request, self.timeout)
            try:
                raw = bytes(self.usb_recv_ep.read(packet, self.timeout))
                if len(raw) < 12 or raw[:3] != bytes((2, tag, tag ^ 255)):
                    raise RuntimeError("Некорректный USBTMC-заголовок генератора")
                length = struct.unpack_from("<I", raw, 4)[0]
                if length > wanted:
                    raise RuntimeError("Некорректная длина USBTMC-ответа")
                payload = bytearray(raw[12:])
                while len(payload) < length:
                    chunk = self.usb_recv_ep.read(packet, self.timeout)
                    if not len(chunk):
                        raise RuntimeError("Неполный USBTMC-пакет")
                    payload.extend(chunk)
                result.extend(payload[:length])
                if raw[8] & 1:
                    return bytes(result)
            except usb.core.USBError:
                self._abort_bulk_in(tag)
                raise
        raise RuntimeError("Слишком длинный ответ генератора")


class Link:
    def __init__(self, kind, config, log):
        self.kind, self.log = kind, log
        self.delay = config["query_delay_s"] if kind == "generator" else config["scope_query_delay_s"]
        vid, pid = (0x05ff, 0x0a21) if kind == "generator" else (0x049f, 0x505c)
        serial = config.get(kind + "_serial") or None
        libusb_package.get_libusb1_backend()
        devices = list(libusb_package.find(find_all=True, idVendor=vid, idProduct=pid))
        if not devices:
            hint = " Выберите USBTMC в Utility → Interface → USB Setup." if kind == "generator" else ""
            raise RuntimeError(f"Не найден {kind} по USB.{hint} Проверьте питание, кабель и хаб.")
        try:
            matches = [d for d in devices if serial is None or d.serial_number == serial]
            if not matches:
                raise RuntimeError(f"Не найден {kind} с указанным серийным номером; проверьте config.json")
            if len(matches) != 1:
                raise RuntimeError(f"Найдено несколько {kind}; укажите {kind}_serial в config.json")
            serial = matches[0].serial_number or None
        finally:
            for device in devices:
                usb.util.dispose_resources(device)
        cls = WaveStationUSB if kind == "generator" else USBTMC
        self.usb = cls(vendor=vid, product=pid, serial_number=serial, timeout=config["usb_timeout_ms"])
        try:
            self.identity = self.query("*IDN?")
            expected = "WaveStation 2012" if kind == "generator" else "DSO4104B"
            if expected not in self.identity or (serial and serial not in self.identity):
                raise RuntimeError("Прибор ответил неожиданной моделью или серийным номером")
        except Exception:
            self.usb.close()
            raise

    def write(self, command):
        self.log(self.kind, "send", command)
        suffix = "" if self.kind == "generator" else "\n"
        self.usb.write((command + suffix).encode("ascii"))
        time.sleep(self.delay)

    def query_bytes(self, command, limit=65536):
        # Retry only read-only generator queries, never commands that set a
        # signal or enable an output. Some old firmware drops the first reply
        # immediately after changing waveform parameters.
        attempts = 2 if self.kind == "generator" and command.endswith("?") else 1
        for attempt in range(attempts):
            try:
                self.write(command)
                value = self.usb.read(limit)
                break
            except usb.core.USBTimeoutError:
                if attempt + 1 == attempts:
                    raise RuntimeError("Генератор не ответил на запрос. Повторите «Пуск»; подробности сохранены в журнале.") from None
                self.log(self.kind, "retry_read_query", command)
                time.sleep(.3)
        if not value:
            raise RuntimeError(f"Пустой ответ {self.kind}: {command}")
        self.log(self.kind, "receive", {"bytes": len(value), "preview": repr(value[:180])})
        return value

    def query(self, command):
        return self.query_bytes(command).decode("ascii").strip()

    def close(self):
        self.usb.close()
