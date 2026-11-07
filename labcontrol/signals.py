"""Validated signal descriptions; no executable expressions or eval."""
import math
import numpy as np

LIMITS = {"SINE": 10e6, "SQUARE": 10e6, "RAMP": 300e3, "PULSE": 5e6, "DC": 10e6}


def number(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name}: нужно число")
    try:
        result = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError(f"{name}: нужно число") from None
    if not math.isfinite(result):
        raise ValueError(f"{name}: число должно быть конечным")
    return result


def validate_signal(values):
    kind = str(values.get("waveform", "SINE")).upper()
    if kind not in LIMITS:
        raise ValueError("Доступны SINE, SQUARE, RAMP, PULSE, DC")
    channel = number(values.get("channel", 1), "Канал")
    if channel not in (1, 2):
        raise ValueError("Канал генератора: 1 или 2")
    result = {"channel": int(channel), "waveform": kind}
    defaults = {"frequency_hz": 1000, "amplitude_vpp": 1, "offset_v": 0, "phase_deg": 0,
                "duty_percent": 50, "symmetry_percent": 50}
    for key, default in defaults.items():
        result[key] = number(values.get(key, default), key)
    f, a, offset = (result[k] for k in ("frequency_hz", "amplitude_vpp", "offset_v"))
    if not 1e-6 <= f <= LIMITS[kind]:
        raise ValueError(f"Частота для {kind}: от 0,000001 до {LIMITS[kind]:g} Гц")
    max_amplitude = 6 if channel == 1 else 20
    if not .004 <= a <= max_amplitude:
        raise ValueError(f"Размах CH{int(channel)}: от 0,004 до {max_amplitude} В")
    if abs(offset) + (0 if kind == "DC" else a / 2) > max_amplitude / 2:
        raise ValueError(f"Смещение с пиком сигнала выходит за пределы ±{max_amplitude / 2:g} В")
    if not 0 <= result["phase_deg"] <= 360:
        raise ValueError("Фаза: от 0 до 360°")
    low, high = (20, 80) if kind == "SQUARE" else (.1, 99.9)
    if not low <= result["duty_percent"] <= high:
        raise ValueError(f"Коэффициент заполнения: от {low} до {high}%")
    if not .1 <= result["symmetry_percent"] <= 99.9:
        raise ValueError("Симметрия: от 0,1 до 99,9%")
    return result


def model(signal, t):
    t = np.asarray(t, dtype=float)
    phase = np.remainder(t * signal["frequency_hz"] + signal["phase_deg"] / 360, 1)
    kind = signal["waveform"]
    if kind == "SINE":
        shape = np.sin(phase * 2 * np.pi)
    elif kind in ("SQUARE", "PULSE"):
        shape = np.where(phase < signal["duty_percent"] / 100, 1., -1.)
    elif kind == "RAMP":
        symmetry = signal["symmetry_percent"] / 100
        shape = np.where(phase < symmetry, -1 + 2 * phase / symmetry,
                         1 - 2 * (phase - symmetry) / (1 - symmetry))
    else:
        shape = np.zeros_like(t)
    return signal["offset_v"] + signal["amplitude_vpp"] / 2 * shape


def parse_wave(reply, channel):
    fields = reply.split(" ", 1)[-1].strip().split(",")
    pairs = dict(zip(fields[::2], fields[1::2]))
    def get(key, default):
        raw = pairs.get(key, str(default)).upper().removesuffix("HZ").removesuffix("V")
        return float(raw)
    return {"channel": channel, "waveform": pairs.get("WVTP", pairs.get("TYPE", "UNKNOWN")),
            "frequency_hz": get("FRQ", 1000), "amplitude_vpp": get("AMP", 1),
            "offset_v": get("OFST", 0), "phase_deg": get("PHSE", 0),
            "duty_percent": get("DUTY", 50), "symmetry_percent": get("SYM", 50)}
