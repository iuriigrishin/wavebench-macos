import json
from pathlib import Path
import numpy as np
import pytest
from labcontrol.drivers.scope import FrameAssembler, convert, decode_scale
from labcontrol.drivers.generator import Generator
from labcontrol.signals import validate_signal
from labcontrol.controller import Controller
from labcontrol.web import create_app

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.example.json").read_text())


def frame(payload, total, offset):
    return b"#9" + f"{len(payload)+29:09}{total:09}{offset:09}".encode() + payload


def test_hantek_packet_format_is_not_shifted_by_29_bytes():
    fixture = ROOT / "tests" / "fixtures"
    assembler = FrameAssembler()
    assert not assembler.feed((fixture / "metadata.bin").read_bytes())
    assert assembler.feed((fixture / "samples.bin").read_bytes())
    assert len(assembler.samples()) == 4000
    assert assembler.samples()[:5] == bytes([0, 2, 3, 5, 6])


def test_missing_chunk_never_becomes_silent_zeros():
    assembler = FrameAssembler()
    assembler.feed(frame(b"a" * 99, 200, 0))
    with pytest.raises(ValueError, match="не полностью"):
        assembler.samples()


def test_overlapping_and_invalid_packets_rejected():
    assembler = FrameAssembler()
    packet = frame(b"a" * 99, 200, 0)
    assembler.feed(packet)
    with pytest.raises((ValueError, RuntimeError)):
        assembler.feed(packet)
    with pytest.raises(ValueError):
        FrameAssembler().feed(frame(b"x" * 99, 100, 99))


@pytest.mark.parametrize("raw,expected", [("9.881e-321", .002), ("9.881e-319", .2), ("4.941e-318", 1), (".5", .5)])
def test_subnormal_scale_quirk(raw, expected):
    assert decode_scale(raw) == pytest.approx(expected)


def test_signed_adc_and_probe_ratio_not_applied_twice():
    t, voltage, codes = convert(bytes([231, 0, 25]), .2, .1, 1000, CONFIG)
    np.testing.assert_array_equal(codes, [-25, 0, 25])
    np.testing.assert_allclose(voltage, [-.3, -.1, .1])
    np.testing.assert_allclose(t, [0, .001, .002])


@pytest.mark.parametrize("values", [{"frequency_hz":"NaN"}, {"amplitude_vpp":float("inf")},
                                     {"waveform":"SINE;*RST"}, {"channel":1.5},
                                     {"waveform":"RAMP","frequency_hz":1e6},
                                     {"waveform":"SQUARE","duty_percent":10}])
def test_reject_invalid_physical_commands(values):
    with pytest.raises(ValueError):
        validate_signal(values)


class FakeLink:
    def __init__(self):
        self.commands = []
        self.identity = "WaveStation 2012"
    def write(self, command):
        self.commands.append(command)
    def query(self, command):
        self.commands.append(command)
        if "OUTP?" in command:
            return command.replace("?", " OFF,LOAD,HZ")
        if any(mode in command for mode in ("MDWV", "SWWV", "BTWV")):
            return command.replace("?", " STATE,OFF")
        return command.replace("?", " WVTP,SINE,FRQ,1000HZ,AMP,1V,OFST,0V,PHSE,0")


def test_configuring_signal_never_enables_output():
    link = FakeLink()
    Generator(link).configure({})
    assert "C1:OUTP OFF" in link.commands
    assert not any("OUTP ON" in c for c in link.commands)


def test_failed_readback_leaves_output_off():
    link = FakeLink()
    with pytest.raises(RuntimeError):
        Generator(link).configure({"frequency_hz":2000})
    assert link.commands[-2] == "C1:OUTP OFF"
    assert not any("OUTP ON" in c for c in link.commands)


def test_capture_is_atomic_and_exported(tmp_path):
    controller = Controller(CONFIG, tmp_path, demo=True)
    controller.connect()
    controller.output(1, True)
    controller.configure_scope(1, .2, .0005)
    record = controller.capture({"title":"Тест", "description":"Проверка целостности записи"}, 1, 1)
    path = controller.store.locate(record["metadata"]["id"])
    assert len(record["time_s"]) == 4000
    assert record["metadata"]["mode"] == "demo"
    assert (path / "plot.png").stat().st_size > 1000
    assert (path / "plot.html").stat().st_size > 1000
    assert (path / "description.md").read_text().startswith("# Тест")
    manifest = json.loads((path / "manifest.json").read_text())
    assert "waveform.csv" in manifest["files"]
    assert not list((tmp_path / "pending").iterdir())
    csv_lines = (path / "waveform.csv").read_text().splitlines()
    assert len(csv_lines) == 4001
    assert record["metadata"]["statistics"]["estimated_frequency_hz"] == pytest.approx(1000, rel=.02)


def test_no_description_no_record(tmp_path):
    controller = Controller(CONFIG, tmp_path, demo=True)
    controller.connect()
    with pytest.raises(ValueError):
        controller.capture({"title":"Test"}, 1, 1)
    assert not list((tmp_path / "pending").iterdir())


def test_web_rejects_external_and_unapproved_post(tmp_path):
    controller = Controller(CONFIG, tmp_path, demo=True)
    client = create_app(controller).test_client()
    assert client.post("/api/output", json={"channel":1,"enabled":True}).status_code == 403
    assert client.get("/", headers={"Host":"evil.test"}).status_code == 403
    response = client.get("/")
    assert response.status_code == 200
    import re
    token = re.search(rb'name="lab-token" content="([^"]+)"', response.data).group(1).decode()
    headers = {"X-Lab-Token":token}
    assert client.post("/api/connect", json={}, headers=headers).status_code == 200
    assert not controller.demo_channels["1"]["output_on"]
    assert client.post("/api/output", json={"channel":1,"enabled":"false"}, headers=headers).status_code == 400
    assert client.post("/api/output", json={"channel":1.5,"enabled":True}, headers=headers).status_code == 400
    assert client.get("/api/runs/not-a-valid-id").status_code == 400
