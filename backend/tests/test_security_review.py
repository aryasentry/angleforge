"""Independent integration checks. No network inference or real credentials."""
import asyncio
import base64
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
from angleforge.app import create_app
from angleforge.store import ProjectStore
from angleforge.timeline import new_project, RevisionConflict, TimelineError
from angleforge import director


def asset(identifier='A'):
    return {'id':identifier,'name':f'Camera {identifier}.mp4','kind':'video','status':'ready','duration_us':12_000_000,'width':1280,'height':720,'fps':{'num':30,'den':1},'has_audio':True,'fingerprint':f'fp-{identifier}','proxy':{'source_origin_us':0,'timebase':1_000_000},'waveform':[]}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path),base_url='http://127.0.0.1:8765',raise_server_exceptions=False) as c:
        c.headers['X-AngleForge-Token']=c.get('/api/session').json()['token']
        yield c


def setup_project(client):
    store=client.app.state.store
    p=store.create('Review')
    store.add_asset(p['id'],asset('A'))
    store.add_asset(p['id'],asset('B'))
    return store.get(p['id'])


def fake_submit(client,monkeypatch):
    class Context:
        id='mock-job'
        def check_cancelled(self):pass
        def progress(self,*args):pass
    def submit(kind,fn):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result=pool.submit(fn,Context()).result()
        return {'id':'mock-job','status':'succeeded','kind':kind,'result':result}
    monkeypatch.setattr(client.app.state.jobs,'submit',submit)


def request(p,**extra):
    return {'base_revision':p['revision'],'prompt':'use camera A','provider':'openrouter','model':'test/model','asset_ids':['A'],'consent':{'metadata':True,'frames':False},**extra}


def test_client_frame_payload_stripped_without_frame_consent(client,monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','test-review-credential-not-real')
    p=setup_project(client)
    fake_submit(client,monkeypatch)
    planner=AsyncMock(return_value={'base_revision':p['revision'],'operations':[],'summary':'test'})
    monkeypatch.setattr(director,'plan_edit',planner)
    response=client.post(f"/api/projects/{p['id']}/ai/plan",json=request(p,_sampled_frames=[{'data_url':'https://attacker.test/private'}]))
    assert response.status_code==200
    passed=planner.call_args.args[1]
    assert '_sampled_frames' not in passed


def test_frame_payload_created_from_selected_internal_assets(client,monkeypatch):
    from angleforge import media
    monkeypatch.setenv('OPENROUTER_API_KEY','test-review-credential-not-real')
    p=setup_project(client)
    directory=client.app.state.root/'assets'/'A'
    directory.mkdir(parents=True)
    (directory/'original.mp4').write_bytes(b'mocked media')
    jpeg=b'\xff\xd8internal-selected-test-frame\xff\xd9'
    (directory/'analysis-0.jpg').write_bytes(jpeg)
    monkeypatch.setattr(media,'analyze_asset',lambda *args:{'frames':[{'source_us':0,'image_source_us':100_000,'image_url':'/api/media/A/analysis-0'}]})
    fake_submit(client,monkeypatch)
    planner=AsyncMock(return_value={'base_revision':p['revision'],'operations':[],'summary':'test'})
    monkeypatch.setattr(director,'plan_edit',planner)
    response=client.post(f"/api/projects/{p['id']}/ai/plan",json=request(p,consent={'metadata':True,'frames':True},_sampled_frames=[{'data_url':'https://attacker.test/unconsented'}]))
    assert response.status_code==200
    frames=planner.call_args.args[1]['_sampled_frames']
    assert len(frames)==1 and frames[0]['asset_id']=='A' and frames[0]['source_us']==100_000
    assert base64.b64decode(frames[0]['data_url'].split(',',1)[1])==jpeg


def test_malformed_consent_and_selection_fail_before_job(client,monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','test-review-credential-not-real')
    p=setup_project(client)
    submitted=[]
    monkeypatch.setattr(client.app.state.jobs,'submit',lambda *args:submitted.append(args))
    for bad in [request(p,consent=None),request(p,consent=['yes']),request(p,asset_ids=[]),request(p,asset_ids=['A','A']),request(p,asset_ids=['unknown']),request(p,asset_ids={'A':True})]:
        result=client.post(f"/api/projects/{p['id']}/ai/plan",json=bad)
        assert result.status_code==400
    assert submitted==[]


def test_stale_request_fails_before_job(client,monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','test-review-credential-not-real')
    p=setup_project(client)
    submitted=[]
    monkeypatch.setattr(client.app.state.jobs,'submit',lambda *args:submitted.append(args))
    response=client.post(f"/api/projects/{p['id']}/ai/plan",json=request(p,base_revision=0))
    assert response.status_code==409 and submitted==[]


def test_scoped_source_rejects_symlink(client,tmp_path):
    directory=client.app.state.root/'assets'/'allowed-id'
    directory.mkdir(parents=True)
    outside=tmp_path/'outside-source.mp4'
    outside.write_bytes(b'unrelated file')
    (directory/'original.mp4').symlink_to(outside)
    assert client.get('/api/media/allowed-id/original').status_code==400
    (directory/'proxy.mp4').symlink_to(outside)
    assert client.get('/api/media/allowed-id/proxy').status_code==404
    assert client.get('/api/media/%2e%2e/original').status_code!=200


def test_older_snapshot_publisher_cannot_overwrite_newer_revision(tmp_path):
    store=ProjectStore(tmp_path)
    try:
        p=store.create('Initial')
        older=store.transact(p['id'],{'base_revision':0,'operations':[{'type':'rename_project','name':'Older'}]})
        newer=store.transact(p['id'],{'base_revision':1,'operations':[{'type':'rename_project','name':'Newer'}]})
        # A worker finishing late must publish the authoritative revision rather
        # than its older argument, exactly the interleaving of concurrent edits.
        store._snapshot(older)
        persisted=json.loads((tmp_path/'projects'/p['id']/'project.json').read_text())
        assert persisted['revision']==newer['revision'] and persisted['name']=='Newer'
    finally:store.close()


def test_undo_redo_retains_imports_and_rejects_old_ai_plan(tmp_path):
    store=ProjectStore(tmp_path)
    try:
        p=store.create('Initial')
        edited=store.transact(p['id'],{'base_revision':0,'actor':'ai','operations':[{'type':'rename_project','name':'AI edit'}]})
        original_edit={key:copy.deepcopy(edited[key]) for key in ('name','settings','timeline')}
        store.add_asset(p['id'],asset('A'))
        current=store.get(p['id'])
        with pytest.raises(RevisionConflict):
            store.transact(p['id'],{'base_revision':edited['revision'],'actor':'ai','operations':[{'type':'rename_project','name':'Stale'}]})
        undone=store.undo(p['id'],current['revision'])
        assert undone['assets']['A']==asset('A') and undone['name']=='Initial'
        redone=store.redo(p['id'],undone['revision'])
        assert redone['assets']['A']==asset('A')
        assert {key:redone[key] for key in original_edit}==original_edit
        before=store.get(p['id'])
        with pytest.raises(TimelineError):
            store.transact(p['id'],{'base_revision':before['revision'],'operations':[{'type':'rename_project','name':'Would change'},{'type':'shell','command':'unreachable'}]})
        assert store.get(p['id'])==before
    finally:store.close()


def test_local_measurement_whitelist_selected_fresh_numeric_only():
    p=new_project('Measurements')
    p['assets']={'A':asset('A'),'B':asset('B')}
    p['analysis']=[{'asset_id':'A','fingerprint':'fp-A','analysis_version':2,'method':'malicious free text','frames':[{'source_us':1_000_000,'mean_luma':.5,'contrast':.1,'scene_change_score':.2,'image_url':'/private/file','transcript':'private speech'},{'source_us':2_000_000,'mean_luma':float('nan')}],'audio':{'rms':.2,'silence_fraction':.1,'clipping_fraction':.01,'transcript':'private speech'},'scene_boundaries_us':[1_000_000,13_000_000]}, {'asset_id':'B','fingerprint':'fp-B','analysis_version':2,'audio':{'rms':.8}}, {'asset_id':'A','fingerprint':'stale-fingerprint','analysis_version':2,'audio':{'rms':.9}}]
    context=director._metadata(p,['A'])
    evidence=context['selected_local_measurements']
    assert len(evidence)==1 and evidence[0]['asset_id']=='A'
    assert evidence[0]['quality_samples']==[{'source_us':1_000_000,'mean_luma':.5,'contrast':.1,'scene_change_score':.2}]
    assert evidence[0]['scene_boundary_candidates_us']==[1_000_000]
    serialized=json.dumps(context)
    for forbidden in ('malicious free text','private speech','/private/file','stale-fingerprint','NaN'):
        assert forbidden not in serialized
