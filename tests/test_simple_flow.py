import json
from pathlib import Path
import numpy as np
import pytest
from labcontrol.controller import Controller

CONFIG = json.loads((Path(__file__).resolve().parents[1]/'config.example.json').read_text())


def test_start_preview_does_not_save_waveforms(tmp_path):
    c=Controller(CONFIG,tmp_path,demo=True)
    result=c.start({'frequency_hz':1000,'amplitude_vpp':1})
    assert c.running and c.demo_channels['1']['output_on']
    a=c.preview(result['session'])
    b=c.preview(result['session'])
    assert a['frame_id'] != b['frame_id']
    assert len(a['voltage_v'])==4000
    assert not list((tmp_path/'runs').rglob('*'))
    assert not list((tmp_path/'pending').iterdir())
    assert not list(tmp_path.rglob('*.csv'))
    assert not list(tmp_path.rglob('*.bin'))


def test_saves_exact_shown_frame_after_later_preview_and_stop(tmp_path):
    c=Controller(CONFIG,tmp_path,demo=True)
    session=c.start({})['session']
    shown=c.preview(session)
    later=c.preview(session)
    assert not np.array_equal(shown['voltage_v'],later['voltage_v'])
    c.stop()
    assert not c.demo_channels['1']['output_on']
    saved=c.save_frame(shown['frame_id'])
    loaded=c.store.load(saved['id'])
    np.testing.assert_array_equal(loaded['voltage_v'],shown['voltage_v'])
    assert loaded['metadata']['captured_at']==shown['captured_at']
    assert loaded['metadata']['generator']['channels']['1']['output_on']
    assert 'без повторного измерения' in loaded['metadata']['experiment']['description']


def test_old_session_cannot_resume_output_or_preview(tmp_path):
    c=Controller(CONFIG,tmp_path,demo=True)
    session=c.start({})['session'];c.stop()
    with pytest.raises(ValueError):c.preview(session)
    assert not c.running and not c.demo_channels['1']['output_on']
    fresh=c.start({'frequency_hz':2000})['session']
    assert fresh!=session
    with pytest.raises(ValueError):c.preview(session)


def test_automatic_scales_show_few_cycles():
    from labcontrol.signals import validate_signal
    scale,timebase=Controller.automatic_scales(validate_signal({'amplitude_vpp':3,'frequency_hz':1000}))
    assert scale==.5
    assert timebase==pytest.approx(.0005)
