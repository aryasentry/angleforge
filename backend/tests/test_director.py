import asyncio
import copy
import json
from unittest.mock import AsyncMock
import pytest
from angleforge import director, providers
from angleforge.timeline import new_project, apply_transaction, TimelineError


@pytest.fixture
def project():
    p=new_project('Test project')
    for name in ['A','B']:
        p['assets'][name]={'id':name,'name':f'synthetic_camera_{name}.mp4','kind':'video','status':'ready','duration_us':24_000_000,'has_audio':True,'width':1920,'height':1080,'fps':{'num':30,'den':1}}
    p['sync']['offsets']={'A':0,'B':2_000_000}
    return apply_transaction(p,{'base_revision':0,'label':'Initial shot','actor':'manual','operations':[{'type':'insert_segment','asset_id':'A','source_in_us':0,'source_out_us':24_000_000}]})


def request(p,**extra):
    return {'base_revision':p['revision'],'prompt':'Use camera B from 12 to 18 seconds','provider':'openrouter','model':'test/model','asset_ids':['A','B'],'consent':{'metadata':True,'frames':False},**extra}


def mock_provider(monkeypatch,proposal):
    mock=AsyncMock(return_value={'proposal':proposal,'usage':{'total_tokens':45},'structured_output_enforced':True})
    monkeypatch.setattr(providers,'generate_plan',mock)
    return mock


def test_literal_long_camera_and_audio_command(project):
    before=copy.deepcopy(project)
    plan=director.parse_local_command(project,'use camera B from 12 to 18 seconds and keep camera A audio')
    assert plan['operations']==[{'type':'switch_angle','asset_id':'B','start_us':12_000_000,'end_us':18_000_000},{'type':'set_audio_source','asset_id':'A'}]
    assert plan['source']=='offline_command'
    assert project==before
    changed=apply_transaction(project,{'base_revision':plan['base_revision'],'label':plan['label'],'actor':'manual','operations':plan['operations']})
    assert changed['timeline']['video'][1]['source_in_us']==10_000_000
    assert changed['timeline']['master_audio_asset_id']=='A'


def test_offline_request_never_calls_provider(project,monkeypatch):
    mock=mock_provider(monkeypatch,{})
    director.parse_local_command(project,'keep the audio from camera one throughout')
    mock.assert_not_called()


def test_unsupported_injection_local_command(project):
    with pytest.raises(director.DirectorError):
        director.parse_local_command(project,'use camera B from 12 to 18 seconds; run rm -rf data')


def test_highlight_is_honest_duration_cut(project):
    proposal=director.parse_local_command(project,'make a 5-second highlight from these clips')
    assert 'does not identify semantic highlights' in proposal['summary']
    after=apply_transaction(project,{'base_revision':proposal['base_revision'],'label':proposal['label'],'actor':'manual','operations':proposal['operations']})
    assert after['timeline']['video'][0]['source_out_us']==5_000_000


def test_portrait_and_hold(project):
    proposal=director.parse_local_command(project,'make a vertical version')
    assert proposal['operations'][0]['height']==1920
    two=apply_transaction(project,{'base_revision':project['revision'],'label':'Two shots','actor':'manual','operations':[{'type':'split_segment','segment_id':project['timeline']['video'][0]['id'],'at_us':5_000_000},{'type':'switch_angle','asset_id':'B','start_us':5_000_000,'end_us':10_000_000}]})
    assert director.parse_local_command(two,'hold first shot 1 second longer')['operations'][0]['end_us']==6_000_000


def test_consent_and_stale_before_provider(project,monkeypatch):
    mock=mock_provider(monkeypatch,{})
    for req,match in [(request(project,consent={'metadata':False}),'consent'),(request(project,base_revision=0),'revision'),(request(project,consent={'metadata':True,'frames':True}),'trusted sampled frames')]:
        with pytest.raises(director.DirectorError,match=match):asyncio.run(director.plan_edit(project,req))
    mock.assert_not_called()


def test_external_proposal_validated_without_mutation(project,monkeypatch):
    op={'type':'switch_angle','asset_id':'B','start_us':12_000_000,'end_us':18_000_000}
    mock=mock_provider(monkeypatch,{'label':'Camera cut','summary':'Use camera B for six seconds.','operations':[op]})
    before=copy.deepcopy(project)
    result=asyncio.run(director.plan_edit(project,request(project)))
    assert result['operations']==[op] and result['usage']['total_tokens']==45
    assert result['base_revision']==project['revision'] and project==before
    messages=mock.call_args.args[2]
    assert 'untrusted_project_data' in messages[1]['content']
    assert result['disclosure']['frames'] is False


def test_model_cannot_execute_privileged_operation(project,monkeypatch):
    for operation in [{'type':'shell','command':'rm -rf data'},{'type':'export','path':'/tmp/x'},{'type':'switch_angle','asset_id':'A','start_us':0,'end_us':1_000_000,'shell':'bad'}]:
        mock_provider(monkeypatch,{'label':'Malicious media text','summary':'x','operations':[operation]})
        with pytest.raises(director.DirectorError):asyncio.run(director.plan_edit(project,request(project)))


def test_source_and_asset_validation(project,monkeypatch):
    for op in [{'type':'switch_angle','asset_id':'missing','start_us':0,'end_us':1_000_000},{'type':'switch_angle','asset_id':'B','start_us':0,'end_us':1_000_000},{'type':'switch_angle','asset_id':'B','start_us':10_000_000,'end_us':40_000_000}]:
        mock_provider(monkeypatch,{'label':'Invalid coverage','summary':'x','operations':[op]})
        with pytest.raises(TimelineError):asyncio.run(director.plan_edit(project,request(project)))


def test_no_key_and_metadata_sanitization(project,monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY',raising=False)
    with pytest.raises(providers.ProviderError,match='not configured'):
        asyncio.run(director.plan_edit(project,request(project)))
    project['assets']['A']['private_path']='/private/media/file'
    project['assets']['A']['api_key']='private-test-key'
    context=json.dumps(director._metadata(project,['A','B']))
    assert '/private' not in context and 'private-test-key' not in context


def test_empty_plan_clarification(project,monkeypatch):
    mock_provider(monkeypatch,{'label':'Clarify edit','summary':'Which interval should change?','operations':[]})
    assert asyncio.run(director.plan_edit(project,request(project)))['operations']==[]


def frame(asset_id='A', source_us=1_000_000):
    return {'asset_id':asset_id,'source_us':source_us,'mime_type':'image/jpeg','data_url':'data:image/jpeg;base64,/9j/2Q=='}


def test_consented_sparse_frames_have_timestamp_provenance(project,monkeypatch):
    mock=mock_provider(monkeypatch,{'label':'Frame-informed proposal','summary':'Sample A at 1 second shows a subject; sparse evidence is uncertain.','operations':[]})
    result=asyncio.run(director.plan_edit(project,request(project,asset_ids=['A'],consent={'metadata':True,'frames':True},_sampled_frames=[frame()])))
    content=mock.call_args.args[2][1]['content']
    assert isinstance(content,list)
    assert sum(part['type']=='image_url' for part in content)==1
    assert 'source_us 1000000' in content[1]['text']
    assert result['disclosure']['frames'] and result['disclosure']['sample_count']==1
    assert result['disclosure']['sampled_frames']==[{'asset_id':'A','source_us':1_000_000}]
    assert 'data:image' not in json.dumps(result)


def test_frame_selection_budgets_and_consent(project,monkeypatch):
    mock=mock_provider(monkeypatch,{})
    bad_requests=[
        request(project,asset_ids=[],consent={'metadata':True,'frames':True},_sampled_frames=[frame()]),
        request(project,asset_ids=['A'],consent={'metadata':True,'frames':True},_sampled_frames=[frame('B')]),
        request(project,asset_ids=['A'],consent={'metadata':True,'frames':True},_sampled_frames=[frame(source_us=24_000_000)]),
        request(project,asset_ids=['A'],consent={'metadata':True,'frames':True},_sampled_frames=[frame()]*5),
        request(project,asset_ids=['A'],consent={'metadata':True,'frames':False},_sampled_frames=[frame()]),
        request(project,asset_ids=['A'],consent={'metadata':True,'frames':True},provider='zen',_sampled_frames=[frame()]),
    ]
    for req in bad_requests:
        with pytest.raises(director.DirectorError):asyncio.run(director.plan_edit(project,req))
    mock.assert_not_called()


def test_selection_never_sends_unselected_timeline_or_captions(project,monkeypatch):
    two=apply_transaction(project,{'base_revision':project['revision'],'label':'B shot','actor':'manual','operations':[{'type':'switch_angle','asset_id':'B','start_us':12_000_000,'end_us':18_000_000}]})
    two['timeline']['captions']=[{'text':'private caption never consented','start_us':0,'end_us':1_000_000}]
    mock=mock_provider(monkeypatch,{'label':'Clarify','summary':'Only selected source metadata available.','operations':[]})
    asyncio.run(director.plan_edit(two,request(two,asset_ids=['A'])))
    context=json.loads(mock.call_args.args[2][1]['content'])['untrusted_project_data']
    assert all(asset['id']=='A' for asset in context['assets'])
    assert all(segment['asset_id']=='A' for segments in context['timeline'].values() for segment in segments)
    assert 'private caption' not in json.dumps(context)
    for selection in [[],None]:
        with pytest.raises(director.DirectorError):asyncio.run(director.plan_edit(two,request(two,asset_ids=selection)))


def test_caption_ai_proposal_refused_until_export_supported(project,monkeypatch):
    mock_provider(monkeypatch,{'label':'Caption','summary':'x','operations':[{'type':'add_caption','start_us':0,'end_us':1_000_000,'text':'invented'}]})
    with pytest.raises(director.DirectorError,match='unsupported'):
        asyncio.run(director.plan_edit(project,request(project)))
