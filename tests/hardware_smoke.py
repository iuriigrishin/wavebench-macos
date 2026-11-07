"""Explicit hardware check; not included in automatic pytest runs.

Re-applies the existing CH1 signal with its output off, restores output,
records a frame, then restores the previous scope scales. No arbitrary signal.
"""
from pathlib import Path
from labcontrol.config import load_config
from labcontrol.controller import Controller

ROOT = Path(__file__).resolve().parents[1]
c = Controller(load_config(ROOT), ROOT / "data")
original = None
try:
    original = c.connect()
    channel = original["generator"]["channels"]["1"]
    if any("STATE,ON" in raw.replace(" ", "") for raw in channel["modes"].values()):
        raise RuntimeError("Skip preparation check: modulation, burst or sweep is enabled")
    c.configure_generator(channel["signal"])
    print("Preparation readback verified; output OFF", flush=True)
    c.output(1, channel["output_on"])
    c.configure_scope(1, .5, .0002)
    result = c.capture({
        "title": "Проверка проекта · CH1 → CH1",
        "description": "Реальная проверка программного управления и сохранения. Исходный периодический сигнал CH1 повторно установлен с проверкой обратным чтением; затем восстановлено состояние выхода. На время записи выбран масштаб 0,5 В/дел и 200 мкс/дел. Исходные масштабы осциллографа после проверки восстановлены.",
        "connection": "WaveStation CH1 → BNC → Hantek CH1; прямое соединение, щуп ×1, DC",
        "tags": "проверка установки, реальные приборы"}, 1, 1)
    print("SAVED", result["metadata"]["id"], result["metadata"]["statistics"], flush=True)
finally:
    if original:
        ch = original["generator"]["channels"]["1"]
        try:
            c.generator.link.write(ch["raw_wave"])
            c.output(1, ch["output_on"])
            c.configure_scope(1, original["scope"]["channels"]["1"]["scale_v_div"], original["scope"]["timebase_s_div"])
            print("RESTORED", c.state(), flush=True)
        finally:
            c.close()
    else:
        c.close()
