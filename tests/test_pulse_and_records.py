import hashlib
import json
from pathlib import Path
import numpy as np
import pytest
import usb.core
from labcontrol.controller import Controller
from labcontrol.drivers.generator import Generator
from labcontrol.drivers.transport import Link

CONFIG=json.loads((Path(__file__).resolve().parents[1]/'config.example.json').read_text())


class PulseLink:
    identity='WaveStation 2012'
    def __init__(self):self.commands=[]
    def write(self,command):self.commands.append(command)
    def query(self,command):
        self.commands.append(command)
        if command.endswith('OUTP?'):return 'C1:OUTP OFF,LOAD,HZ'
        return 'C1:BSWV WVTP,PULSE,FRQ,1000HZ,AMP,1V,OFST,0V,DUTY,50,DLY,0'


def test_pulse_parameters_are_sent_as_separate_short_messages():
    link=PulseLink();result=Generator(link).configure({'waveform':'PULSE'},full_state=False)
    assert result['channels']['1']['signal']['waveform']=='PULSE'
    assert 'C1:BSWV WVTP,PULSE' in link.commands
    assert 'C1:BSWV DUTY,50' in link.commands
    assert all(len(c.split(' ',1)[1].split(','))==2 for c in link.commands if ':BSWV ' in c)
    assert not any('OUTP ON' in c for c in link.commands)


def test_only_query_is_retried_on_timeout(monkeypatch):
    class USB:
        def __init__(self):self.writes=[];self.reads=0
        def write(self,data):self.writes.append(data)
        def read(self,limit):
            self.reads+=1
            if self.reads==1:raise usb.core.USBTimeoutError('timeout')
            return b'C1:BSWV WVTP,PULSE'
    link=object.__new__(Link);link.kind='generator';link.delay=0;link.usb=USB();link.log=lambda *args:None
    monkeypatch.setattr('labcontrol.drivers.transport.time.sleep',lambda seconds:None)
    assert 'PULSE' in link.query('C1:BSWV?')
    assert link.usb.writes==[b'C1:BSWV?',b'C1:BSWV?']


def test_readable_names_and_existing_id_links_survive_migration(tmp_path):
    c=Controller(CONFIG,tmp_path,demo=True);session=c.start({})['session']
    shown=c.preview(session);saved=c.save_frame(shown['frame_id'],'Мой опыт')
    path=c.store.locate(saved['id']);assert 'Мой опыт' in path.name and 'Синус' in path.name
    old=path.with_name(saved['id']);path.rename(old)
    before={name:hashlib.sha256((old/name).read_bytes()).hexdigest() for name in ['metadata.json','waveform.csv','waveform.npz']}
    changes=c.store.make_readable();assert len(changes)==1
    readable=c.store.locate(saved['id'])
    for name,digest in before.items():assert hashlib.sha256((readable/name).read_bytes()).hexdigest()==digest
    assert (readable/'Читать.html').exists()
    assert 'Мой опыт' in (tmp_path/'Открыть записи.html').read_text()
    np.testing.assert_array_equal(c.store.load(saved['id'])['voltage_v'],shown['voltage_v'])
