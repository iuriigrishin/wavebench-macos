"""Hantek DSO4104B chunk assembly and empirically checked ADC conversion."""
import math
import struct
import time
import numpy as np


def decode_scale(raw):
    value = float(raw)
    if 0 < abs(value) < 1e-100:
        value = struct.unpack("<Q", struct.pack("<d", value))[0] / 1e6
        # Firmware prints only four significant digits of a subnormal number.
        candidates = [m * 10. ** exponent for exponent in range(-5, 4) for m in (1, 2, 5)]
        nearest = min(candidates, key=lambda x: abs(x - value))
        if math.isclose(value, nearest, rel_tol=.001):
            value = nearest
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Прибор вернул некорректный масштаб напряжения")
    return value


class FrameAssembler:
    def __init__(self):
        self.buffer = None
        self.covered = None
        self.metadata = None
        self.segments = []

    def feed(self, packet):
        if packet == b"#9000000000":
            raise RuntimeError("Осциллограф пока не имеет данных. Включите RUN/STOP и проверьте вход.")
        if len(packet) < 29 or packet[:2] != b"#9":
            raise ValueError("Неизвестный формат пакета Hantek")
        try:
            length, total, offset = [int(packet[i:i+9]) for i in (2, 11, 20)]
        except ValueError:
            raise ValueError("Некорректные длины в заголовке Hantek") from None
        if length != len(packet) or not 99 < total <= 1_000_000:
            raise ValueError("Некорректная длина выгрузки Hantek")
        payload = packet[29:]
        if offset < 0 or offset + len(payload) > total:
            raise ValueError("Пакет Hantek выходит за границы выгрузки")
        if self.buffer is None:
            self.buffer, self.covered = bytearray(total), bytearray(total)
        if total != len(self.buffer):
            raise ValueError("Размер записи изменился во время выгрузки")
        if offset == 0 and length == 128:
            if self.metadata is not None:
                raise RuntimeError("Осциллограф начал новую запись до завершения предыдущей")
            self.metadata = packet
        for start, end in self.segments:
            if max(start, offset) < min(end, offset + len(payload)):
                raise ValueError("Повторяющиеся области в пакетах Hantek")
        self.segments.append((offset, offset + len(payload)))
        self.buffer[offset:offset+len(payload)] = payload
        self.covered[offset:offset+len(payload)] = b"\1" * len(payload)
        return self.metadata is not None and all(self.covered)

    def samples(self):
        if self.metadata is None or self.covered is None or not all(self.covered):
            raise ValueError("Осциллограмма получена не полностью")
        # The 128-byte metadata frame includes a 29-byte transfer header.
        # Actual metadata occupies 99 bytes in the assembled byte stream.
        return bytes(self.buffer[len(self.metadata)-29:])


def convert(samples, scale_v, offset_v, sample_rate, config):
    if not math.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("Некорректная частота дискретизации")
    codes = np.frombuffer(samples, dtype=np.int8).copy()
    voltage = codes.astype(float) * scale_v / config["adc_codes_per_division"]
    voltage = config["adc_polarity"] * voltage - offset_v
    voltage = voltage * config["voltage_gain_correction"] + config["voltage_offset_correction_v"]
    return np.arange(len(codes), dtype=float) / sample_rate, voltage, codes


class Scope:
    def __init__(self, link, config):
        self.link, self.config = link, config

    def state(self):
        channels = {}
        for ch in range(1, 5):
            prefix = f":CHANnel{ch}:"
            raw_scale = self.link.query(prefix + "SCALe?")
            channels[str(ch)] = {
                "enabled": self.link.query(prefix + "DISPlay?").upper() in ("1", "ON"),
                "scale_v_div": decode_scale(raw_scale), "raw_scale": raw_scale,
                "offset_v": float(self.link.query(prefix + "OFFSet?")),
                "probe": float(self.link.query(prefix + "PROBe?")),
                "coupling": self.link.query(prefix + "COUPling?")}
        return {"identity": self.link.identity, "channels": channels,
                "timebase_s_div": float(self.link.query(":TIMebase:SCALe?")),
                "sample_rate_hz": float(self.link.query(":ACQuire:SRATe?"))}

    def configure(self, channel, scale_v_div, timebase_s_div):
        if channel not in (1, 2, 3, 4):
            raise ValueError("Канал осциллографа: от 1 до 4")
        if not any(math.isclose(scale_v_div, m * 10.**e, rel_tol=1e-8)
                   for m in (1, 2, 5) for e in range(-4, 2)) or not .0005 <= scale_v_div <= 10:
            raise ValueError("Масштаб: значения 1–2–5, от 0,5 мВ/дел до 10 В/дел")
        allowed = [m * 10.**e for m in (1, 2, 5) for e in range(-9, 3)]
        if not any(math.isclose(timebase_s_div, x, rel_tol=1e-8) for x in allowed) or not 2e-9 <= timebase_s_div <= 100:
            raise ValueError("Развёртка: значения 1–2–5, от 2 нс до 100 с/дел")
        self.link.write(f":CHANnel{channel}:DISPlay ON")
        self.link.write(f":CHANnel{channel}:SCALe {scale_v_div:.10g}")
        self.link.write(f":TIMebase:SCALe {timebase_s_div:.10g}")
        result = self.state()
        if not math.isclose(result["channels"][str(channel)]["scale_v_div"], scale_v_div, rel_tol=.002):
            raise RuntimeError("Осциллограф не подтвердил масштаб напряжения")
        if not math.isclose(result["timebase_s_div"], timebase_s_div, rel_tol=.002):
            raise RuntimeError("Осциллограф не подтвердил развёртку")
        return result

    def capture(self, channel, raw_dir=None, packets=None):
        if channel not in (1, 2, 3, 4):
            raise ValueError("Канал осциллографа: от 1 до 4")
        before = self.state()
        # Read the first metadata frame to learn the original RUN state.
        first = self.link.query_bytes(":WAVeform:DATA:ALL?")
        for _ in range(256):
            if len(first) == 128 and first.startswith(b"#9") and first[29:30] in (b"0", b"1"):
                break
            first = self.link.query_bytes(":WAVeform:DATA:ALL?")
        else:
            raise RuntimeError("Не удалось прочитать состояние RUN/STOP")
        was_running = first[29:30] == b"1"
        restore_errors = []
        try:
            # One channel per record: avoid assuming undocumented interleaving.
            for ch in range(1, 5):
                desired = ch == channel
                if before["channels"][str(ch)]["enabled"] != desired:
                    self.link.write(f":CHANnel{ch}:DISPlay {'ON' if desired else 'OFF'}")
            if was_running:
                time.sleep(min(.6, max(.1, before["timebase_s_div"] * 12)))
            self.link.write(":RUNning OFF")
            assembler = FrameAssembler()
            for index in range(512):
                packet = self.link.query_bytes(":WAVeform:DATA:ALL?")
                if raw_dir is not None:
                    (raw_dir / f"packet_{index:04}.bin").write_bytes(packet)
                if packets is not None:
                    packets.append(packet)
                if assembler.feed(packet):
                    break
            else:
                raise RuntimeError("Превышено число пакетов при чтении осциллограммы")
            scale_raw = self.link.query(f":CHANnel{channel}:SCALe?")
            scale_v = decode_scale(scale_raw)
            offset_v = float(self.link.query(f":CHANnel{channel}:OFFSet?"))
            rate = float(self.link.query(":ACQuire:SRATe?"))
            samples = assembler.samples()
            t, voltage, codes = convert(samples, scale_v, offset_v, rate, self.config)
            clipped = np.mean((codes <= -127) | (codes >= 126)) > .005
            metadata = {"identity": self.link.identity, "channel": channel, "before": before,
                        "sample_rate_hz": rate, "sample_count": len(codes),
                        "scale_v_div": scale_v, "raw_scale": scale_raw, "offset_v": offset_v,
                        "probe": before["channels"][str(channel)]["probe"],
                        "time_reference": "first_sample", "was_running": was_running,
                        "adc_encoding": "signed_int8", "clipping_detected": bool(clipped),
                        "conversion": {key: self.config[key] for key in
                            ("adc_codes_per_division", "adc_polarity", "voltage_gain_correction", "voltage_offset_correction_v")},
                        "conversion_note": "Scale query includes probe ratio; do not multiply it twice.",
                        "restore_errors": restore_errors}
            warnings = []
            if clipped:
                warnings.append("Возможна обрезка сигнала: увеличьте В/дел и повторите запись.")
            if not was_running:
                warnings.append("Осциллограф был остановлен: сохранён кадр из памяти, а не новая запись.")
            return t, voltage, codes, metadata, warnings
        finally:
            for ch in range(1, 5):
                try:
                    enabled = before["channels"][str(ch)]["enabled"]
                    self.link.write(f":CHANnel{ch}:DISPlay {'ON' if enabled else 'OFF'}")
                except Exception as error:
                    restore_errors.append(f"CH{ch}: {error}")
            try:
                self.link.write(":RUNning " + ("ON" if was_running else "OFF"))
            except Exception as error:
                restore_errors.append(f"RUN/STOP: {error}")
            if restore_errors:
                raise RuntimeError("Не удалось восстановить состояние осциллографа: " + "; ".join(restore_errors))
