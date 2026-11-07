import secrets
from urllib.parse import urlsplit
from pathlib import Path
from flask import Flask, jsonify, render_template, request, send_from_directory, abort
from plotly.offline import get_plotlyjs
from .signals import validate_signal, number
from .records import journal_page


def create_app(controller):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1_000_000
    token = secrets.token_urlsafe(32)
    plotly_js = get_plotlyjs()

    @app.before_request
    def local_only():
        if request.host.split(":")[0] not in ("127.0.0.1", "localhost", "[::1]"):
            abort(403)
        if request.method == "POST":
            if request.headers.get("X-Lab-Token") != token:
                abort(403)
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                abort(403)

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(Exception)
    def error(error):
        from werkzeug.exceptions import HTTPException
        code = error.code if isinstance(error, HTTPException) else (400 if isinstance(error, ValueError) else 500)
        app.logger.exception("Operation failed")
        return jsonify(error=str(error)), code

    @app.get("/")
    def index():
        return render_template("index.html", token=token, demo=controller.demo)

    @app.get("/assets/plotly.js")
    def plotly():
        return app.response_class(plotly_js, mimetype="application/javascript")

    @app.get("/api/health")
    def health():
        return jsonify(application="lab-project", connected=controller.connected, running=controller.running,
                       session=controller.session, active_generator=controller.active_generator, demo=controller.demo)

    @app.post("/api/start")
    def start():
        return jsonify(controller.start(request.get_json()))

    @app.post("/api/stop")
    def stop():
        return jsonify(controller.stop())

    @app.post("/api/preview")
    def preview():
        return jsonify(controller.preview(request.get_json().get("session")))

    @app.post("/api/save-frame")
    def save_frame():
        values = request.get_json()
        return jsonify(controller.save_frame(values.get("frame_id"), values.get("title", ""), values.get("note", "")))

    @app.post("/api/connect")
    def connect():
        with controller.lock:
            controller.close()
            return jsonify(controller.connect())

    @app.post("/api/generator")
    def generator():
        return jsonify(controller.configure_generator(request.get_json()))

    @app.post("/api/output")
    def output():
        values = request.get_json()
        channel = number(values.get("channel"), "Канал")
        if channel not in (1, 2):
            raise ValueError("Канал генератора: 1 или 2")
        return jsonify(controller.output(int(channel), values.get("enabled")))

    @app.post("/api/scope")
    def scope():
        values = request.get_json()
        channel = number(values.get("channel"), "Канал")
        if channel not in (1, 2, 3, 4):
            raise ValueError("Канал осциллографа: от 1 до 4")
        return jsonify(controller.configure_scope(int(channel), number(values.get("scale_v_div"), "В/дел"),
                                                  number(values.get("timebase_s_div"), "с/дел")))

    @app.post("/api/capture")
    def capture():
        values = request.get_json()
        scope_channel, generator_channel = values.get("scope_channel", 1), values.get("generator_channel", 1)
        if type(scope_channel) is not int or type(generator_channel) is not int:
            raise ValueError("Нужен целый номер канала")
        return jsonify(controller.capture(values.get("experiment", {}), scope_channel, generator_channel))

    @app.get("/api/runs")
    def runs():
        return jsonify(controller.store.list())

    @app.get("/archive")
    def archive():
        return app.response_class(journal_page(controller.store.root, online=True), mimetype="text/html")

    @app.get("/records/<run_id>/")
    def record_view(run_id):
        return send_from_directory(controller.store.locate(run_id), "Читать.html", as_attachment=False)

    @app.get("/records/<run_id>/<path:filename>")
    def record_file(run_id, filename):
        return send_from_directory(controller.store.locate(run_id), filename, as_attachment=True)

    @app.get("/api/runs/<run_id>")
    def run(run_id):
        return jsonify(controller.store.load(run_id))

    @app.get("/files/<run_id>/<path:filename>")
    def download(run_id, filename):
        return send_from_directory(controller.store.locate(run_id), filename, as_attachment=True)

    @app.get("/api/presets")
    def presets():
        return jsonify(controller.store.presets())

    @app.post("/api/presets")
    def preset_save():
        values = request.get_json()
        return jsonify(controller.store.save_preset(values.get("name"), validate_signal(values.get("signal", {}))))

    return app
