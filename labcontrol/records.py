"""Human-readable names and portable record pages; raw measurements unchanged."""
import base64
import html
import json
import re
import csv
import numpy as np
from datetime import datetime
from urllib.parse import quote

FORMS = {"SINE": "Синус", "SQUARE": "Меандр", "PULSE": "Импульсы", "RAMP": "Пила", "DC": "Постоянное напряжение"}
STYLE = """body{font:16px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f1f5f6;color:#17333e;margin:0}main{max-width:1000px;margin:35px auto;padding:0 24px}article{background:white;border:1px solid #dce5e8;border-radius:12px;padding:28px;margin:18px 0}h1{font-size:28px;line-height:1.25;margin:8px 0 18px}h2{font-size:18px;margin:25px 0 10px}p{white-space:pre-wrap;overflow-wrap:anywhere}.sub{font-size:13px;color:#627985}.warn{background:#fff0db;padding:12px 16px;border-radius:7px;font-size:14px}.metrics{display:flex;gap:30px;flex-wrap:wrap;margin:22px 0}.metrics small{display:block;color:#627985}.metrics strong{font-size:23px;font-weight:550}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:10px 6px;border-bottom:1px solid #e1e9ec;overflow-wrap:anywhere}th{font-size:12px;font-weight:550;color:#627985}a{color:#006d77;text-decoration:none}a:hover{text-decoration:underline}img{width:100%;height:auto}.links{display:flex;flex-wrap:wrap;gap:18px;font-size:14px}.record{display:block;padding:16px 0;border-bottom:1px solid #e1e9ec}.record strong{display:block;color:#17333e}.record small{display:block;color:#627985}.tag{font-size:12px;letter-spacing:.1em;color:#006d77}@media(max-width:600px){article{padding:18px}main{padding:0 12px}h1{font-size:23px}.metrics{gap:18px}}@media print{body{background:white}main{margin:0;padding:0}article{border:0;padding:0}.links{display:none}img{break-inside:avoid}}"""


def fmt(value):
    if value is None:
        return "не определена"
    return f"{float(value):.6g}".replace(".", ",")


def frequency(value):
    if value >= 1e6:
        return fmt(value / 1e6) + " МГц"
    if value >= 1000:
        return fmt(value / 1000) + " кГц"
    return fmt(value) + " Гц"


def signal_info(meta):
    ch = str(meta.get("generator_channel", 1))
    state = meta["generator"]["channels"][ch]
    signal = state["signal"]
    label = FORMS.get(signal["waveform"], signal["waveform"])
    label += " " + frequency(signal["frequency_hz"]) + " " + fmt(signal["amplitude_vpp"]) + " Вpp"
    if signal["waveform"] in ("PULSE", "SQUARE"):
        label += " " + fmt(signal["duty_percent"]) + "%"
    return signal, state, label


def directory_name(meta):
    timestamp = datetime.fromisoformat(meta["created_at"]).strftime("%H-%M-%S")
    _, _, signal_label = signal_info(meta)
    title = meta["experiment"]["title"]
    title = re.sub(r'[\x00-\x1f/\\:<>|?*]', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip(' .')[:42] or "Измерение"
    return f"{timestamp} - {title} - {signal_label}"


def record_page(path, meta):
    e = html.escape
    signal, state, label = signal_info(meta)
    stats, scope, experiment = meta["statistics"], meta["scope"], meta["experiment"]
    captured = datetime.fromisoformat(meta.get("captured_at", meta["created_at"])).strftime("%d.%m.%Y, %H:%M:%S")
    saved = datetime.fromisoformat(meta["created_at"]).strftime("%H:%M:%S")
    warnings = list(meta.get("warnings", []))
    if not state["output_on"] and not any("выключен" in text for text in warnings):
        warnings.append("Выход генератора был выключен: это запись входного напряжения, а не подтверждённый отклик на поданный сигнал.")
    notices = ''.join(f'<p class="warn">{e(text)}</p>' for text in warnings)
    metric = ''.join(f'<div><small>{e(name)}</small><strong>{e(value)}</strong></div>' for name, value in
                     [("Размах", fmt(stats["vpp_v"])+" В"), ("RMS", fmt(stats["rms_v"])+" В"),
                      ("Среднее", fmt(stats["mean_v"])+" В"), ("Частота, оценка", "не определена" if stats.get("estimated_frequency_hz") is None else frequency(stats["estimated_frequency_hz"]))])
    rows = [("Сигнал генератора", label), ("Смещение генератора", fmt(signal["offset_v"])+" В"),
            ("Выход генератора при получении кадра", "Включён" if state["output_on"] else "Выключен"),
            ("Соединение", experiment.get("connection", "")),
            ("Осциллограф", scope["identity"]), ("Канал", "CH"+str(scope["channel"])),
            ("Масштаб напряжения", fmt(scope["scale_v_div"])+" В/дел"),
            ("Частота дискретизации", frequency(scope["sample_rate_hz"])),
            ("Отсчётов", str(scope["sample_count"])), ("Длительность кадра", fmt(stats["duration_s"]*1000)+" мс"),
            ("Генератор", meta["generator"]["identity"])]
    table = ''.join(f'<tr><th>{e(name)}</th><td>{e(value)}</td></tr>' for name,value in rows)
    png = base64.b64encode((path / "plot.png").read_bytes()).decode()
    with np.load(path / "waveform.npz", allow_pickle=False) as arrays:
        times, volts, codes = arrays["time_s"], arrays["voltage_v"], arrays["adc_code"]
    with open(path / "Данные.csv", "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["Время от первого отсчёта, мс", f"Напряжение CH{scope['channel']}, В", "Код АЦП"])
        writer.writerows((f"{t*1000:.12g}".replace(".", ","), f"{v:.12g}".replace(".", ","), int(code))
                         for t,v,code in zip(times,volts,codes))
    links = ''.join(f'<a href="{quote(filename)}">{e(name)}</a>' for filename,name in
                    [("Данные.csv","Таблица данных"),("plot.png","График PNG"),("plot.html","Интерактивный график"),("description.md","Описание"),("metadata.json","Все параметры")])
    demo = "ДЕМОНСТРАЦИЯ" if meta["mode"] == "demo" else "ЗАПИСЬ ИЗМЕРЕНИЯ"
    text = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(experiment['title'])}</title><style>{STYLE}</style></head><body><main><article><div class="tag">{demo}</div><h1>{e(experiment['title'])}</h1><div class="sub">Кадр: {captured} · Сохранён: {saved} · {e(meta.get('timezone', ''))}</div>{notices}<p>{e(experiment['description'])}</p><div class="metrics">{metric}</div><img src="data:image/png;base64,{png}" alt="Измеренная осциллограмма"><div class="links">{links}</div><h2>Условия измерения</h2><table>{table}</table><p class="sub">Время на графике отсчитывается от первого отсчёта. Расчётная модель генератора хранится отдельно от измеренных данных. Идентификатор: {e(meta['id'])}</p></article></main></body></html>'''
    (path / "Читать.html").write_text(text, encoding="utf-8")


def journal_page(root, online=False):
    records = []
    for filename in (root / "runs").glob("*/*/metadata.json"):
        meta = json.loads(filename.read_text(encoding="utf-8"))
        records.append((meta, filename.parent))
    records.sort(key=lambda pair: pair[0]["created_at"], reverse=True)
    links = []
    for meta, path in records:
        _, state, label = signal_info(meta)
        href = f"/records/{meta['id']}/" if online else quote((path / "Читать.html").relative_to(root).as_posix())
        date = datetime.fromisoformat(meta["created_at"]).strftime("%d.%m.%Y %H:%M:%S")
        status = "выход включён" if state["output_on"] else "выход выключен"
        links.append(f'<a class="record" href="{href}"><strong>{html.escape(meta["experiment"]["title"])}</strong><small>{date} · {html.escape(label)} · {status}</small></a>')
    return f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Сохранённые измерения</title><style>{STYLE}</style></head><body><main><article><div class="tag">ЛАБОРАТОРИЯ</div><h1>Сохранённые измерения</h1><p class="sub">Записей: {len(records)}. Нажмите на название, чтобы открыть данные и график.</p>{"".join(links) or "Пока нет сохранённых записей"}</article></main></body></html>'
