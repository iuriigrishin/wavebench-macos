import json
from types import SimpleNamespace
import pytest
from labcontrol.config import load_config
from labcontrol.drivers import transport


def test_config_works_without_personal_file_and_merges_local_overrides(tmp_path):
    defaults = {'port': 8765, 'generator_serial': None, 'usb_timeout_ms': 5000}
    (tmp_path/'config.example.json').write_text(json.dumps(defaults))
    assert load_config(tmp_path) == defaults
    (tmp_path/'config.json').write_text(json.dumps({'generator_serial': 'test-device'}))
    loaded = load_config(tmp_path)
    assert loaded['generator_serial'] == 'test-device'
    assert loaded['port'] == 8765


@pytest.mark.parametrize('serials,requested,expected', [
    (['device-a'], None, 'device-a'),
    (['device-a', 'device-b'], 'device-b', 'device-b'),
    (['device-a', 'device-b'], None, None),
    (['device-a'], 'missing', None),
])
def test_usb_discovery_never_silently_selects_ambiguous_device(monkeypatch, serials, requested, expected):
    devices = [SimpleNamespace(serial_number=s) for s in serials]
    disposed = []
    opened = []
    monkeypatch.setattr(transport.libusb_package, 'get_libusb1_backend', lambda: None)
    monkeypatch.setattr(transport.libusb_package, 'find', lambda **kwargs: iter(devices))
    monkeypatch.setattr(transport.usb.util, 'dispose_resources', disposed.append)
    def open_usb(**kwargs):
        opened.append(kwargs['serial_number'])
        return SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(transport, 'WaveStationUSB', open_usb)
    monkeypatch.setattr(transport.Link, 'query', lambda self, command: f'WaveStation 2012,{expected}')
    config = dict(generator_serial=requested, query_delay_s=.2, usb_timeout_ms=5000)
    if expected is None:
        with pytest.raises(RuntimeError):
            transport.Link('generator', config, lambda *args: None)
        assert not opened
    else:
        link = transport.Link('generator', config, lambda *args: None)
        assert opened == [expected]
        link.close()
    assert disposed == devices
