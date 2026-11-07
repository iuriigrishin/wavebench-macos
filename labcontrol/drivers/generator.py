import math
from ..signals import parse_wave, validate_signal


class Generator:
    def __init__(self, link):
        self.link = link

    def state(self, channels=(1, 2)):
        selected = channels
        channels = {}
        for channel in selected:
            raw_wave = self.link.query(f"C{channel}:BSWV?")
            raw_output = self.link.query(f"C{channel}:OUTP?")
            wave = parse_wave(raw_wave, channel)
            modes = {name: self.link.query(f"C{channel}:{name}?") for name in ("MDWV", "SWWV", "BTWV")}
            channels[str(channel)] = {"signal": wave, "output_on": "OUTP ON" in raw_output,
                                      "raw_wave": raw_wave, "raw_output": raw_output, "modes": modes}
        return {"identity": self.link.identity, "channels": channels}

    def output(self, channel, enabled):
        if channel not in (1, 2) or type(enabled) is not bool:
            raise ValueError("Некорректный канал или состояние выхода")
        self.link.write(f"C{channel}:OUTP {'ON' if enabled else 'OFF'}")
        actual = self.link.query(f"C{channel}:OUTP?")
        if ("OUTP ON" in actual) != enabled:
            raise RuntimeError("Прибор не подтвердил состояние выхода")
        return actual

    def configure(self, values, full_state=True):
        signal = validate_signal(values)
        channel = signal["channel"]
        # Explicit preparation always leaves the selected output OFF.
        self.output(channel, False)
        try:
            for mode in ("MDWV", "SWWV", "BTWV"):
                self.link.write(f"C{channel}:{mode} STATE,OFF")
            self.link.write(f"C{channel}:OUTP LOAD,HZ")
            items = ["WVTP", signal["waveform"]]
            if signal["waveform"] != "DC":
                items += ["FRQ", f"{signal['frequency_hz']:.12g}HZ", "AMP", f"{signal['amplitude_vpp']:.12g}V"]
            items += ["OFST", f"{signal['offset_v']:.12g}V"]
            if signal["waveform"] in ("SQUARE", "PULSE"):
                items += ["DUTY", f"{signal['duty_percent']:.9g}"]
            elif signal["waveform"] == "RAMP":
                items += ["SYM", f"{signal['symmetry_percent']:.9g}"]
            if signal["waveform"] in ("SINE", "SQUARE", "RAMP"):
                items += ["PHSE", f"{signal['phase_deg']:.9g}"]
            # This firmware can lose the next reply after one long multi-field
            # BSWV command (observed with PULSE). Apply documented parameter
            # pairs individually, keeping each command a separate message.
            for index in range(0, len(items), 2):
                self.link.write(f"C{channel}:BSWV {items[index]},{items[index + 1]}")
            actual = parse_wave(self.link.query(f"C{channel}:BSWV?"), channel)
            keys = ["offset_v"]
            if signal["waveform"] != "DC":
                keys += ["frequency_hz", "amplitude_vpp"]
            if signal["waveform"] in ("SQUARE", "PULSE"):
                keys += ["duty_percent"]
            if signal["waveform"] == "RAMP":
                keys += ["symmetry_percent"]
            if actual["waveform"] != signal["waveform"] or any(
                    not math.isclose(actual[key], signal[key], rel_tol=.002, abs_tol=1e-6) for key in keys):
                raise RuntimeError("Генератор вернул другие параметры. Выход оставлен выключенным.")
            return self.state() if full_state else {"identity": self.link.identity,
                "channels": {str(channel): {"signal": actual, "output_on": False}}}
        except Exception:
            try:
                self.output(channel, False)
            except Exception:
                pass
            raise
