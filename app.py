#!/usr/bin/env python3
"""Run the local instrument panel. Launch does not connect or enable outputs."""
import argparse
import atexit
import fcntl
import logging
import os
from pathlib import Path
import threading
import webbrowser

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))
from labcontrol.config import load_config
from labcontrol.controller import Controller
from labcontrol.web import create_app


def main():
    parser = argparse.ArgumentParser(description="WaveBench · macOS")
    parser.add_argument("--demo", action="store_true", help="Демонстрация без USB-приборов")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    config = load_config(ROOT)
    for key in ("adc_codes_per_division", "voltage_gain_correction", "usb_timeout_ms"):
        if not isinstance(config[key], (float, int)) or config[key] <= 0:
            raise ValueError(f"Некорректный {key} в config.json")
    data = ROOT / ("data_demo" if args.demo else "data")
    data.mkdir(exist_ok=True)
    lock = open(data / ".app.lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Приложение уже запущено. Откройте его окно или остановите прежний запуск.")
    controller = Controller(config, data, demo=args.demo)
    atexit.register(controller.close)
    logging.basicConfig(level=logging.INFO,
                        handlers=[logging.FileHandler(data / "logs" / "application.log", encoding="utf-8"),
                                  logging.StreamHandler()])
    app = create_app(controller)
    port = args.port or config["port"]
    if not 1024 <= port <= 65535:
        raise ValueError("Порт должен быть от 1024 до 65535")
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", port, app, threaded=True)
    url = f"http://127.0.0.1:{port}"
    print(f"\nWaveBench: {url}\nОстановка: Ctrl+C. Выходы приборов управляются кнопками в окне.\n", flush=True)
    if not args.no_browser:
        threading.Timer(.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        controller.close()


if __name__ == "__main__":
    main()
