"""Immutable, atomic experiment records with raw packets and portable plots."""
import csv
import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
import plotly.graph_objects as go

from . import __version__
from .signals import model
from .records import directory_name, record_page, journal_page

ID_RE = re.compile(r"^\d{8}-\d{6}-\d{6}-[0-9a-f]{8}$")


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def statistics(t, y):
    result = {"min_v": float(y.min()), "max_v": float(y.max()), "vpp_v": float(np.ptp(y)),
              "mean_v": float(y.mean()), "rms_v": float(np.sqrt(np.mean(y*y))),
              "samples": len(y), "duration_s": float(t[-1]-t[0])}
    centered = y - (np.quantile(y, .05) + np.quantile(y, .95)) / 2
    # Suppress noise-only pseudo-frequency; this remains an estimate.
    span = np.ptp(y)
    crossings = []
    armed = False
    for i in range(1, len(y)):
        if centered[i] < -span * .15:
            armed = True
        if armed and centered[i-1] <= 0 < centered[i]:
            fraction = -centered[i-1] / (centered[i] - centered[i-1])
            crossings.append(t[i-1] + fraction * (t[i] - t[i-1]))
            armed = False
    frequency = None
    if len(crossings) >= 3 and span > 0:
        periods = np.diff(crossings)
        if np.std(periods)/np.mean(periods) < .1:
            frequency = float(1 / np.median(periods))
    result["estimated_frequency_hz"] = frequency
    return result


class Store:
    def __init__(self, root):
        self.root = Path(root)
        for folder in ("runs", "failed", "pending", "presets", "logs"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)

    def begin(self, experiment, mode):
        title = str(experiment.get("title", "")).strip()
        description = str(experiment.get("description", "")).strip()
        if not title or not description:
            raise ValueError("Укажите название и описание измерения")
        if len(title) > 160 or len(description) > 20000:
            raise ValueError("Слишком длинное название или описание")
        now = datetime.now().astimezone()
        run_id = now.strftime("%Y%m%d-%H%M%S-%f-") + uuid.uuid4().hex[:8]
        path = self.root / "pending" / run_id
        path.mkdir()
        (path / "raw").mkdir()
        meta = {"schema_version": 1, "software_version": __version__, "id": run_id,
                "created_at": now.isoformat(), "timezone": str(now.tzinfo), "mode": mode,
                "status": "pending", "experiment": {"title": title, "description": description,
                    "connection": str(experiment.get("connection", ""))[:2000],
                    "tags": str(experiment.get("tags", ""))[:1000]}}
        write_json(path / "metadata.json", meta)
        return path, meta

    def finish(self, path, meta, t, y, codes, scope, generator, events, warnings):
        meta.update(status="complete", scope=scope, generator=generator,
                    statistics=statistics(t, y), warnings=warnings)
        np.savez_compressed(path / "waveform.npz", time_s=t, voltage_v=y, adc_code=codes)
        with open(path / "waveform.csv", "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time_s", f"CH{scope['channel']}_voltage_v", "adc_signed_int8"])
            writer.writerows(zip(t, y, codes))
        with open(path / "commands.jsonl", "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        self.plots(path, meta, t, y)
        write_json(path / "metadata.json", meta)
        (path / "description.md").write_text(self.description(meta), encoding="utf-8")
        record_page(path, meta)
        self.manifest(path)
        destination = self.root / "runs" / meta["created_at"][:10] / directory_name(meta)
        destination.parent.mkdir(exist_ok=True)
        if destination.exists():
            destination = destination.with_name(destination.name + " - " + meta["id"][-8:])
        path.rename(destination)
        self.refresh_journal()
        return meta

    def refresh_journal(self):
        temporary = self.root / "Открыть записи.html.updating"
        temporary.write_text(journal_page(self.root), encoding="utf-8")
        temporary.replace(self.root / "Открыть записи.html")
        with open(self.root / "Журнал.csv", "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["Дата и время", "Название", "Сигнал", "Частота, Гц", "Размах генератора, Вpp",
                             "Смещение, В", "Выход включён", "Измеренный размах, В", "RMS, В", "Папка"])
            from .records import signal_info
            for row in self.list():
                path = self.locate(row["id"])
                meta = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
                signal,state,label = signal_info(meta)
                writer.writerow([datetime.fromisoformat(meta["created_at"]).strftime("%d.%m.%Y %H:%M:%S"),
                    meta["experiment"]["title"], label, str(signal["frequency_hz"]).replace(".", ","),
                    str(signal["amplitude_vpp"]).replace(".", ","), str(signal["offset_v"]).replace(".", ","),
                    "Да" if state["output_on"] else "Нет", str(meta["statistics"]["vpp_v"]).replace(".", ","),
                    str(meta["statistics"]["rms_v"]).replace(".", ","),str(path.relative_to(self.root))])

    def make_readable(self):
        changes = []
        for metadata_path in list((self.root / "runs").glob("*/*/metadata.json")):
            path = metadata_path.parent
            meta = json.loads(metadata_path.read_text(encoding="utf-8"))
            record_page(path, meta)
            self.manifest(path)
            destination = path.with_name(directory_name(meta))
            if destination != path:
                if destination.exists():
                    destination = destination.with_name(destination.name + " - " + meta["id"][-8:])
                path.rename(destination)
                changes.append({"id": meta["id"], "old": str(path.relative_to(self.root)), "new": str(destination.relative_to(self.root))})
        if changes:
            write_json(self.root / "folder_names.json", changes)
        self.refresh_journal()
        return changes

    def fail(self, path, meta, error, events):
        meta.update(status="failed", error=str(error))
        write_json(path / "metadata.json", meta)
        write_json(path / "commands.json", events)
        destination = self.root / "failed" / meta["id"]
        path.rename(destination)
        return destination

    def manifest(self, path):
        hashes = {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(path.rglob("*")) if p.is_file() and p.name != "manifest.json"}
        write_json(path / "manifest.json", {"algorithm": "sha256", "files": hashes})

    def plots(self, path, meta, t, y):
        title = meta["experiment"]["title"]
        label = f"CH{meta['scope']['channel']} · measured"
        fig = Figure(figsize=(10, 4.8), layout="constrained")
        FigureCanvasAgg(fig)
        ax = fig.subplots()
        ax.plot(t, y, linewidth=.9, label=label)
        ax.set(xlabel="Time from first sample (s)", ylabel="Voltage (V)", title=title)
        ax.grid(alpha=.25)
        ax.legend()
        fig.savefig(path / "plot.png", dpi=180)
        fig.savefig(path / "plot.svg")
        plot = go.Figure(go.Scatter(x=t.tolist(), y=y.tolist(), mode="lines", name=label))
        plot.update_layout(title=title, template="plotly_white", xaxis_title="Время от первого отсчёта, с",
                           yaxis_title="Напряжение, В", hovermode="x unified")
        plot.write_html(path / "plot.html", include_plotlyjs=True, full_html=True,
                        config={"responsive": True, "displaylogo": False})
        signal = meta["generator"]["channels"].get(str(meta.get("generator_channel", 1)), {}).get("signal")
        if signal and signal["waveform"] in ("SINE", "SQUARE", "RAMP", "PULSE", "DC"):
            expected = model(signal, t)
            with open(path / "generator_model.csv", "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["time_s", "ideal_voltage_v_NOT_MEASURED_PHASE_ARBITRARY"])
                writer.writerows(zip(t, expected))

    @staticmethod
    def description(meta):
        experiment, scope, stats = meta["experiment"], meta["scope"], meta["statistics"]
        channels = meta["generator"]["channels"]
        state_lines = []
        for ch, state in channels.items():
            signal = state["signal"]
            state_lines.append(f"- CH{ch}: {signal['waveform']}, {signal['frequency_hz']:g} Гц, "
                               f"{signal['amplitude_vpp']:g} Вpp, смещение {signal['offset_v']:g} В; "
                               f"выход {'включён' if state['output_on'] else 'выключен'}.")
        warning_text = "\n".join("- " + w for w in meta["warnings"]) or "Нет."
        return (f"# {experiment['title']}\n\n{experiment['description']}\n\n"
                f"Дата: {meta['created_at']} ({meta['timezone']}).\n\n"
                f"Источник: {'ДЕМОНСТРАЦИЯ, не реальные измерения' if meta['mode'] == 'demo' else 'реальные USB-приборы'}.\n\n"
                f"Соединение: {experiment['connection']}\n\nМетки: {experiment['tags']}\n\n"
                f"## Генератор\n\n{meta['generator']['identity']}\n\n" + "\n".join(state_lines) +
                f"\n\n## Осциллограмма\n\n{scope['identity']}\n\n"
                f"- Канал: CH{scope['channel']}.\n- Отсчётов: {scope['sample_count']}.\n"
                f"- Частота дискретизации: {scope['sample_rate_hz']:g} Гц.\n"
                f"- Масштаб: {scope['scale_v_div']:g} В/дел.\n- Коэффициент щупа: {scope['probe']:g}.\n"
                "- Начало времени — первый отсчёт; абсолютное время триггера не восстановлено.\n"
                f"- Размах: {stats['vpp_v']:.8g} В; RMS: {stats['rms_v']:.8g} В; среднее: {stats['mean_v']:.8g} В.\n\n"
                f"## Замечания\n\n{warning_text}\n\n"
                "## Файлы\n\nwaveform.csv / waveform.npz — измеренные точки; raw/ — исходные USB-пакеты; "
                "plot.png / plot.svg / plot.html — графики; metadata.json — настройки и результат; "
                "commands.jsonl — обмен с приборами; manifest.json — контрольные суммы.\n\n"
                "generator_model.csv — идеальная математическая модель, НЕ измерение входа. "
                "Её фаза относительно осциллограммы не синхронизирована.\n")

    def list(self):
        result = []
        for path in sorted((self.root / "runs").glob("*/*/metadata.json"), reverse=True):
            meta = json.loads(path.read_text(encoding="utf-8"))
            result.append({k: meta[k] for k in ("id", "created_at", "experiment", "mode", "statistics", "warnings")})
        return sorted(result, key=lambda item: item["created_at"], reverse=True)

    def locate(self, run_id):
        if not ID_RE.fullmatch(run_id):
            raise ValueError("Некорректный номер записи")
        date = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"
        path = self.root / "runs" / date / run_id
        if not path.is_dir():
            for metadata_path in (self.root / "runs" / date).glob("*/metadata.json"):
                if json.loads(metadata_path.read_text(encoding="utf-8")).get("id") == run_id:
                    return metadata_path.parent
            raise FileNotFoundError("Запись не найдена")
        return path

    def load(self, run_id):
        path = self.locate(run_id)
        meta = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        with np.load(path / "waveform.npz", allow_pickle=False) as arrays:
            t, y = arrays["time_s"], arrays["voltage_v"]
        return {"metadata": meta, "time_s": t.tolist(), "voltage_v": y.tolist()}

    def save_preset(self, name, signal):
        if not str(name).strip() or len(str(name)) > 120:
            raise ValueError("Нужно название шаблона, до 120 символов")
        preset_id = uuid.uuid4().hex
        write_json(self.root / "presets" / (preset_id + ".json"),
                   {"id": preset_id, "name": str(name).strip(), "signal": signal})
        return self.presets()

    def presets(self):
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted((self.root / "presets").glob("*.json"))]
