import pytest
from fastapi.testclient import TestClient
from angleforge.app import create_app

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path),base_url='http://127.0.0.1:8765') as c:
        c.headers['X-AngleForge-Token']=c.get('/api/session').json()['token']
        yield c

def test_security_boundary(client):
    response=client.post('/api/projects',json={'name':'Good'})
    assert response.status_code==200
    assert client.post('/api/projects',json={'name':'Evil'},headers={'Origin':'https://evil.test'}).status_code==403
    assert client.post('/api/projects',json={'name':'Evil'},headers={'X-AngleForge-Token':'bad'}).status_code==403
    assert client.get('/api/session',headers={'Origin':'https://evil.test'}).status_code==403
    assert client.get('/api/projects',headers={'Host':'evil.test'}).status_code==400

def test_create_edit_undo_reopen(client):
    p=client.post('/api/projects',json={'name':'Test'}).json()
    path='/api/projects/'+p['id']
    t=dict(base_revision=0,label='Rename',actor='manual',operations=[dict(type='rename_project',name='Changed')])
    result=client.post(path+'/transactions',json=t)
    assert result.status_code==200 and result.json()['name']=='Changed'
    assert client.post(path+'/transactions',json=t).status_code==409
    assert client.post(path+'/undo',json={'base_revision':1}).json()['name']=='Test'
    assert client.post(path+'/redo',json={'base_revision':2}).json()['name']=='Changed'
    assert client.post(path+'/save').json()['saved']
    assert client.get(path).json()['revision']==3
    assert client.get(path+'/document').headers['content-type'].startswith('application/json')
    assert client.get('/api/projects').json()['projects'][0]['id']==p['id']

def test_traversal_unsupported_upload_and_source_deletion(client):
    p=client.post('/api/projects',json={'name':'Safe'}).json(); path='/api/projects/'+p['id']
    assert client.post(path+'/import',files=[('files',('bad.txt',b'not media','text/plain'))]).status_code==400
    assert client.post(path+'/export',json={'base_revision':0,'name':'../../escaped.mp4'}).status_code==400
    assert client.get('/api/media/not-an-asset/original').status_code==404
    assert client.delete(path).status_code==405

def test_local_and_no_key_states(client,monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY',raising=False)
    monkeypatch.delenv('OPENCODE_API_KEY',raising=False)
    assert not client.get('/api/provider').json()['configured']
    p=client.post('/api/projects',json={'name':'Offline'}).json()
    response=client.post('/api/projects/'+p['id']+'/command',json={'base_revision':0,'prompt':'make a vertical version'})
    assert response.status_code==200
    assert response.json()['operations'][0]['type']=='set_settings'
    assert client.get('/api/projects/'+p['id']).json()['settings']['height']==1080
    response=client.post('/api/projects/'+p['id']+'/ai/plan',json={'base_revision':0,'prompt':'edit','provider':'openrouter','model':'x','consent':{'metadata':True}})
    assert response.status_code==400

def test_ai_provenance_requires_matching_finished_plan(client):
    import time
    p=client.post('/api/projects',json={'name':'AI proposal provenance'}).json()
    ops=[{'type':'rename_project','name':'Accepted plan'}]
    result={'project_id':p['id'],'base_revision':0,'operations':ops,'provider':'openrouter','model':'test/model','usage':{'total_tokens':10}}
    job=client.app.state.jobs.submit('ai',lambda ctx:result)
    for _ in range(100):
        if client.app.state.jobs.get(job['id'])['status']=='succeeded': break
        time.sleep(.01)
    route='/api/projects/'+p['id']+'/transactions'
    bad=client.post(route,json={'base_revision':0,'actor':'ai','proposal_job_id':job['id'],'operations':[{'type':'rename_project','name':'Altered'}]})
    assert bad.status_code==400
    valid=client.post(route,json={'base_revision':0,'actor':'ai','proposal_job_id':job['id'],'operations':ops})
    assert valid.status_code==200
    assert valid.json()['analysis'][-1]['provider']=='openrouter'
    assert valid.json()['history'][0]['provenance']['proposal_job_id']==job['id']
