import copy
import pytest

from angleforge.timeline import new_project, apply_transaction, validate_project, TimelineError

US = 1_000_000

def project():
    p = new_project('Test')
    for id, duration in [('a', 20), ('b', 14), ('c', 6)]:
        p['assets'][id] = dict(id=id, name=f'Camera {id.upper()}', kind='video', status='ready', duration_us=duration*US, width=640, height=360, fps={'num':30,'den':1}, has_audio=True, rotation=0, fingerprint=id, proxy={'source_origin_us':0,'timebase':US}, waveform=[],error=None)
    p['sync']['offsets'] = {'a':0,'b':2*US,'c':5*US}
    return p

def edit(p,*ops):
    return apply_transaction(p, {'base_revision':p['revision'],'label':'Test edit','actor':'manual','operations':list(ops)})

def base():
    return edit(project(),dict(type='insert_segment',asset_id='a',source_in_us=0,source_out_us=12*US))

def test_split_half_open_and_ripple_mapping():
    p=base(); first=p['timeline']['video'][0]['id']
    p=edit(p,dict(type='split_segment',segment_id=first,at_us=4*US))
    assert [(s['source_in_us'],s['source_out_us'],s['output_in_us']) for s in p['timeline']['video']]==[(0,4*US,0),(4*US,12*US,4*US)]
    p=edit(p,dict(type='remove_segment',segment_id=first,ripple=True))
    assert p['timeline']['video'][0]['source_in_us']==4*US
    assert p['timeline']['video'][0]['event_in_us']==4*US
    assert p['timeline']['video'][0]['output_in_us']==0
    assert p['sync']['offsets']['b']==2*US

def test_switch_angle_and_master_audio_preserve_event():
    p=edit(base(),dict(type='set_audio_source',asset_id='a'),dict(type='switch_angle',asset_id='b',start_us=3*US,end_us=6*US))
    assert [(s['asset_id'],s['source_in_us'],s['source_out_us']) for s in p['timeline']['video']]==[('a',0,3*US),('b',1*US,4*US),('a',6*US,12*US)]
    assert all(s['asset_id']=='a' for s in p['timeline']['audio'])
    assert sum(s['source_out_us']-s['source_in_us'] for s in p['timeline']['audio'])==12*US

def test_transaction_is_atomic_and_stale_rejected():
    p=base(); original=copy.deepcopy(p)
    with pytest.raises(TimelineError):
        edit(p,dict(type='set_audio_source',asset_id='a'),dict(type='switch_angle',asset_id='c',start_us=0,end_us=2*US))
    assert p==original
    with pytest.raises(TimelineError,match='revision'):
        apply_transaction(p,dict(base_revision=0,operations=[dict(type='rename_project',name='stale')]))

@pytest.mark.parametrize('op',[
    dict(type='insert_segment',asset_id='../etc/passwd',source_in_us=0,source_out_us=US),
    dict(type='insert_segment',asset_id='a',source_in_us=-1,source_out_us=US),
    dict(type='insert_segment',asset_id='a',source_in_us=0.5,source_out_us=US),
    dict(type='insert_segment',asset_id='a',source_in_us=0,source_out_us=21*US),
    dict(type='shell',command='echo unsafe'),
    dict(type='set_settings',width=1,height=5,fps={'num':30,'den':1}),
    dict(type='set_audio_gain',gain=float('nan')),
    dict(type='rename_project',name='x',unexpected=True),
])
def test_invalid_edits_rejected(op):
    with pytest.raises(TimelineError): edit(base(),op)

def test_trim_reorder_audio_gain_and_crop():
    p=base(); id=p['timeline']['video'][0]['id']
    p=edit(p,dict(type='split_segment',segment_id=id,at_us=4*US))
    second=p['timeline']['video'][1]['id']
    p=edit(p,dict(type='trim_segment',segment_id=second,source_in_us=5*US,source_out_us=10*US),dict(type='reorder_segments',segment_ids=[second,id]),dict(type='set_crop',segment_id=second,mode='fill',x=0.2,y=0.8),dict(type='set_audio_source',asset_id='a'),dict(type='set_audio_gain',gain=0.7,fade_in_us=100000,fade_out_us=100000))
    assert [(s['output_in_us'],s['event_in_us']) for s in p['timeline']['video']]==[(0,5*US),(5*US,0)]
    assert p['timeline']['audio'][0]['source_in_us']==5*US
    assert p['timeline']['audio'][0]['gain']==0.7
    assert p['timeline']['video'][0]['crop']['x']==0.2
    validate_project(p)

def test_missing_audio_coverage_is_explicit():
    p=edit(base(),dict(type='set_audio_source',asset_id='b'))
    assert p['timeline']['audio'][0]['output_in_us']==2*US
    assert p['timeline']['audio_warnings']

def test_set_sync_realigns_event_without_changing_source():
    p=base()
    p=edit(p,dict(type='set_sync',reference_asset_id='a',offsets={'a':3*US,'b':5*US,'c':8*US}))
    assert p['timeline']['video'][0]['source_in_us']==0
    assert p['timeline']['video'][0]['event_in_us']==3*US

def test_reorder_requires_all_ids_once():
    p=base()
    with pytest.raises(TimelineError): edit(p,dict(type='reorder_segments',segment_ids=[]))

def test_empty_and_nonripple_edits_refused():
    p=base()
    with pytest.raises(TimelineError): edit(p)
    with pytest.raises(TimelineError): edit(p,dict(type='remove_segment',segment_id=p['timeline']['video'][0]['id'],ripple=False))
