from pathlib import Path
import numpy as np
import pytest
from scipy.io import wavfile
from angleforge.sync import propose_sync, SyncError, _drift, RATE


def assets(names):
    return {n: {"id": n, "name": n, "kind": "video", "status": "ready", "has_audio": True} for n in names}


def audio(tmp_path, name, values, rate=8000):
    path = tmp_path / (name + '.wav')
    wavfile.write(path, rate, (np.clip(values, -1, 1) * 25000).astype(np.int16))
    return path


def test_known_offsets_accuracy(tmp_path):
    rng = np.random.default_rng(448)
    signal = rng.normal(0, .2, 12 * 8000)
    paths = {name: audio(tmp_path, name, signal[offset * 8000:]) for name, offset in [('A', 0), ('B', 2), ('C', 4)]}
    proposal = propose_sync(assets(paths), paths, 'A')
    for name, expected in [('A', 0), ('B', 2_000_000), ('C', 4_000_000)]:
        assert abs(proposal['offsets'][name] - expected) <= 50_000
    assert len(proposal['edges']) == 3
    assert min(proposal['confidence'].values()) > .99


def test_graph_chain_overlap(tmp_path):
    signal = np.random.default_rng(71).normal(0, .2, 14 * 8000)
    paths = {name: audio(tmp_path, name, signal[start*8000:(start+6)*8000]) for name,start in [('A',0),('B',4),('C',8)]}
    result = propose_sync(assets(paths), paths)
    assert abs(result['offsets']['C'] - 8_000_000) <= 50_000
    assert len(result['edges']) == 2


def test_repetitive_music_refuses(tmp_path):
    periodic = .3 * np.sin(np.arange(8*8000) * 2*np.pi*440/8000)
    paths = {n: audio(tmp_path,n,periodic) for n in ['A','B']}
    with pytest.raises(SyncError, match='refused') as error:
        propose_sync(assets(paths), paths)
    assert any(d.get('reason') == 'ambiguous audio peaks' for d in error.value.diagnostics)


def test_disconnected_and_silent_refuse(tmp_path):
    rng = np.random.default_rng(33)
    paths = {n: audio(tmp_path,n,rng.normal(0,.2,8*8000)) for n in ['A','B']}
    with pytest.raises(SyncError, match='disconnected'):
        propose_sync(assets(paths),paths)
    paths['B'] = audio(tmp_path,'B',np.zeros(8*8000))
    with pytest.raises(SyncError, match='refused'):
        propose_sync(assets(paths),paths)


def test_no_audio_requires_manual_anchors():
    with pytest.raises(SyncError, match='manual offsets'):
        propose_sync(assets(['A','B']),{})


def test_drift_measurement_does_not_stretch():
    rng = np.random.default_rng(556)
    source = rng.normal(size=40*RATE)
    shifted = source.copy()
    shifted[-8*RATE:] = source[-8*RATE-100:-100]
    diagnostic = _drift(source,shifted,0)
    assert abs(diagnostic['residual_change_us']) >= 25_000


def test_reference_and_single_video():
    assert propose_sync(assets(['A']),{})['offsets'] == {'A':0}
    with pytest.raises(SyncError, match='reference'):
        propose_sync(assets(['A']),{},'nope')


def test_cancellation(tmp_path):
    class Context:
        def check_cancelled(self):raise RuntimeError('canceled')
    with pytest.raises(RuntimeError,match='canceled'):
        propose_sync(assets(['A','B']),{},ctx=Context())


def test_long_overlap_warns_clock_shift_without_stretch(tmp_path):
    source=np.random.default_rng(998).normal(0,.2,40*RATE)
    shifted=source.copy()
    shifted[-8*RATE:]=source[-8*RATE-100:-100]
    paths={'A':audio(tmp_path,'A',source,RATE),'B':audio(tmp_path,'B',shifted,RATE)}
    result=propose_sync(assets(paths),paths)
    assert result['offsets']['B']==0
    assert any('clock drift' in warning for warning in result['warnings'])
