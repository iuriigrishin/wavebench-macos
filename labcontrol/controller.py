import copy
import json
import threading
import uuid
import time
import math
from collections import OrderedDict
from datetime import datetime
import numpy as np
from .storage import Store, statistics
from .signals import model, validate_signal


class Controller:
    def __init__(self, config, data_dir, demo=False):
        self.config, self.demo = config, demo
        self.store = Store(data_dir)
        self.lock = threading.RLock()
        self.links, self.generator, self.scope = [], None, None
        self.connected = False
        self.running = False
        self.session = None
        self.frames = OrderedDict()
        self.preview_events = None
        self.active_generator = None
        self.events = []
        self.demo_channels = {str(ch): {"signal": validate_signal({"channel": ch}), "output_on": False,
                                          "raw_wave": "demo", "raw_output": "demo"} for ch in (1, 2)}
        self.demo_scope = {"channel": 1, "scale_v_div": .2, "timebase_s_div": .0002}

    def event(self, instrument, direction, value):
        entry = {"time": datetime.now().astimezone().isoformat(), "instrument": instrument,
                 "direction": direction, "value": value}
        if self.preview_events is not None:
            self.preview_events.append(entry)
            return
        self.events.append(entry)
        with open(self.store.root / "logs" / "commands.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")

    def require_connection(self):
        if not self.connected:
            raise RuntimeError("Приборы не подключены. Нажмите «Пуск».")

    @staticmethod
    def automatic_scales(signal):
        candidates = sorted(m * 10.**e for e in range(-9, 4) for m in (1, 2, 5))
        envelope = abs(signal["offset_v"]) + (0 if signal["waveform"] == "DC" else signal["amplitude_vpp"] / 2)
        scale = next(x for x in candidates if x >= max(.0005, envelope / 3))
        timebase = .0005 if signal["waveform"] == "DC" else .4 / signal["frequency_hz"]
        timebase = min(100, max(2e-9, timebase))
        timebase = min(candidates, key=lambda x: abs(math.log(x / timebase)))
        return min(10, scale), min(100, max(2e-9, timebase))

    def start(self, values):
        signal = validate_signal({**values, "channel": 1})
        with self.lock:
            # No disk snapshots are produced by start or live preview.
            if not self.connected:
                self.connect(read_state=False)
            self.running = False
            self.session = None
            self.active_generator = None
            try:
                if self.demo:
                    self.configure_generator(signal)
                else:
                    self.generator.configure(signal, full_state=False)
                scale, timebase = self.automatic_scales(signal)
                if not self.demo:
                    self.scope.link.write(":CHANnel1:PROBe 1")
                    self.scope.link.write(":CHANnel1:COUPling DC")
                    self.scope.link.write(":CHANnel1:OFFSet 0")
                self.configure_scope(1, scale, timebase)
                if not self.demo:
                    self.scope.link.write(":TRIGger:SWEep AUTO")
                    self.scope.link.write(":RUNning ON")
                if self.demo:
                    self.demo_channels["1"]["output_on"] = True
                    self.active_generator = self.generator_state()
                else:
                    self.generator.output(1, True)
                    self.active_generator = {"channels": {"1": {"signal": signal}}}
                self.running = True
                self.session = uuid.uuid4().hex
                return {"session": self.session, "signal": self.active_generator["channels"]["1"]["signal"],
                        "running": True, "scale_v_div": scale, "timebase_s_div": timebase, "demo": self.demo}
            except Exception:
                try:
                    self.output(1, False)
                except Exception:
                    pass
                raise

    def stop(self):
        with self.lock:
            self.running = False
            self.session = None
            if self.connected:
                if self.demo:
                    self.demo_channels["1"]["output_on"] = False
                else:
                    self.generator.output(1, False)
            return {"running": False}

    def preview(self, session):
        with self.lock:
            if not self.running or session != self.session:
                raise ValueError("Просмотр остановлен. Нажмите «Пуск».")
            self.preview_events = []
            try:
                generator = self.generator_state() if self.demo else self.generator.state(channels=(1,))
                if self.demo:
                    signal = generator["channels"]["1"]["signal"]
                    rate = 4000 / (self.demo_scope["timebase_s_div"] * 12)
                    t = np.arange(4000) / rate
                    y = model(signal, t) + np.random.default_rng().normal(0, .002, len(t))
                    codes = np.clip(np.rint(y / self.demo_scope["scale_v_div"] * 25), -128, 127).astype(np.int8)
                    scope = {"identity": "DEMO · Hantek DSO4104B", "channel": 1,
                             "sample_rate_hz": rate, "sample_count": len(t),
                             "scale_v_div": self.demo_scope["scale_v_div"], "probe": 1,
                             "time_reference": "first_sample", "was_running": True}
                    warnings = ["Демонстрационные данные"]
                    packets = []
                else:
                    packets = []
                    t, y, codes, scope, warnings = self.scope.capture(1, packets=packets)
                if not generator["channels"]["1"]["output_on"]:
                    warnings.append("Выход генератора выключен на приборе.")
                frame_id = uuid.uuid4().hex
                frame = {"time_s": t.copy(), "voltage_v": y.copy(), "codes": codes.copy(),
                         "scope": copy.deepcopy(scope), "generator": copy.deepcopy(generator),
                         "warnings": list(warnings), "packets": list(packets),
                         "captured_at": datetime.now().astimezone().isoformat(), "events": list(self.preview_events),
                         "statistics": statistics(t, y)}
                self.frames[frame_id] = frame
                while len(self.frames) > 16:
                    self.frames.popitem(last=False)
                return {"frame_id": frame_id, "captured_at": frame["captured_at"],
                        "time_s": t.tolist(), "voltage_v": y.tolist(),
                        "statistics": frame["statistics"], "warnings": warnings,
                        "signal": generator["channels"]["1"]["signal"], "demo": self.demo}
            finally:
                self.preview_events = None

    def save_frame(self, frame_id, title="", note=""):
        with self.lock:
            if frame_id not in self.frames:
                raise ValueError("Этот кадр уже вышел из памяти. Сохраните текущий график.")
            frame = self.frames[frame_id]
            signal = frame["generator"]["channels"]["1"]["signal"]
            title = str(title).strip() or f"{signal['waveform']} · {signal['frequency_hz']:g} Гц"
            automatic = (f"Сигнал {signal['waveform']}, частота {signal['frequency_hz']:g} Гц, "
                         f"размах {signal['amplitude_vpp']:g} Вpp, смещение {signal['offset_v']:g} В. "
                         f"Кадр получен {frame['captured_at']}. Сохранён именно показанный кадр без повторного измерения.")
            experiment = {"title": title, "description": automatic + ("\n\n" + str(note).strip() if str(note).strip() else ""),
                          "connection": "WaveStation CH1 → BNC → Hantek CH1; щуп ×1, DC", "tags": ""}
            path, meta = self.store.begin(experiment, "demo" if self.demo else "hardware")
            meta.update(generator_channel=1, frame_id=frame_id, captured_at=frame["captured_at"])
            try:
                for index, packet in enumerate(frame["packets"]):
                    (path / "raw" / f"packet_{index:04}.bin").write_bytes(packet)
                self.store.finish(path, meta, frame["time_s"], frame["voltage_v"], frame["codes"],
                                  frame["scope"], frame["generator"], frame["events"], frame["warnings"])
                return {"id": meta["id"], "folder": str(self.store.locate(meta["id"])),
                        "captured_at": frame["captured_at"]}
            except Exception as error:
                if path.exists():
                    self.store.fail(path, meta, error, frame["events"])
                raise

    def connect(self, read_state=True):
        with self.lock:
            if not self.connected and not self.demo:
                from .drivers.transport import Link
                from .drivers.generator import Generator
                from .drivers.scope import Scope
                try:
                    g = Link("generator", self.config, self.event)
                    self.links.append(g)
                    s = Link("scope", self.config, self.event)
                    self.links.append(s)
                    self.generator, self.scope = Generator(g), Scope(s, self.config)
                except Exception:
                    self.close()
                    raise
            self.connected = True
            return self.state() if read_state else {"connected": True}

    def generator_state(self):
        return ({"identity": "DEMO · WaveStation 2012", "channels": copy.deepcopy(self.demo_channels)}
                if self.demo else self.generator.state())

    def state(self):
        self.require_connection()
        return {"connected": True, "demo": self.demo, "generator": self.generator_state(),
                "scope": ({"identity": "DEMO · Hantek DSO4104B", **self.demo_scope}
                          if self.demo else self.scope.state())}

    def configure_generator(self, values):
        with self.lock:
            self.require_connection()
            signal = validate_signal(values)
            if self.demo:
                self.demo_channels[str(signal["channel"])].update(signal=signal, output_on=False)
                return self.generator_state()
            return self.generator.configure(signal)

    def output(self, channel, enabled):
        with self.lock:
            self.require_connection()
            if channel not in (1, 2) or type(enabled) is not bool:
                raise ValueError("Некорректный канал или состояние выхода")
            if self.demo:
                self.demo_channels[str(channel)]["output_on"] = enabled
            else:
                self.generator.output(channel, enabled)
            self.event("application", "output", {"channel": channel, "enabled": enabled})
            return self.generator_state()

    def configure_scope(self, channel, scale, timebase):
        with self.lock:
            self.require_connection()
            if self.demo:
                from .drivers.scope import Scope
                # Validate using the same constraints without touching hardware.
                if channel not in (1, 2, 3, 4) or scale <= 0 or timebase <= 0:
                    raise ValueError("Некорректные параметры осциллографа")
                self.demo_scope = {"channel": channel, "scale_v_div": scale, "timebase_s_div": timebase}
                return self.demo_scope
            return self.scope.configure(channel, scale, timebase)

    def capture(self, experiment, scope_channel, generator_channel):
        with self.lock:
            self.require_connection()
            if scope_channel not in (1, 2, 3, 4) or generator_channel not in (1, 2):
                raise ValueError("Некорректный канал")
            path, meta = self.store.begin(experiment, "demo" if self.demo else "hardware")
            meta["generator_channel"] = generator_channel
            start = len(self.events)
            try:
                generator = self.generator_state()
                if self.demo:
                    signal = generator["channels"][str(generator_channel)]["signal"]
                    t = np.arange(4000) / (4000 / (self.demo_scope["timebase_s_div"] * 12))
                    y = model(signal, t) if generator["channels"][str(generator_channel)]["output_on"] else np.zeros_like(t)
                    y += np.random.default_rng(42).normal(0, .002, len(t))
                    codes = np.clip(np.rint(y / self.demo_scope["scale_v_div"] * 25), -128, 127).astype(np.int8)
                    scope = {"identity": "DEMO · Hantek DSO4104B", "channel": scope_channel,
                             "sample_rate_hz": float(1/(t[1]-t[0])), "sample_count": len(t),
                             "scale_v_div": self.demo_scope["scale_v_div"], "probe": 1,
                             "time_reference": "first_sample", "was_running": True}
                    warnings = ["ДЕМОНСТРАЦИОННЫЕ данные: реальные приборы не использовались."]
                else:
                    t, y, codes, scope, warnings = self.scope.capture(scope_channel, path / "raw")
                    after = self.generator_state()
                    meta["generator_after"] = after
                    for ch in ("1", "2"):
                        if after["channels"][ch] != generator["channels"][ch]:
                            warnings.append(f"Настройки генератора CH{ch} изменились во время записи; оба состояния сохранены.")
                modes = generator["channels"][str(generator_channel)].get("modes", {})
                if any("STATE,ON" in raw.replace(" ", "") for raw in modes.values()):
                    warnings.append("Активна модуляция, свип или пачки: идеальная периодическая модель не описывает весь сигнал.")
                if not generator["channels"][str(generator_channel)]["output_on"]:
                    warnings.append("Выход выбранного канала генератора выключен.")
                self.store.finish(path, meta, t, y, codes, scope, generator, self.events[start:], warnings)
                return self.store.load(meta["id"])
            except Exception as error:
                if path.exists():
                    failed = self.store.fail(path, meta, error, self.events[start:])
                    raise RuntimeError(f"{error}. Незавершённая запись сохранена: {failed}") from error
                raise

    def close(self):
        with self.lock:
            for link in self.links:
                try:
                    link.close()
                except Exception:
                    pass
            self.links = []
            self.connected = False
            self.running = False
            self.session = None
