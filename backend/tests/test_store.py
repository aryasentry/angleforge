import copy
import pytest
from angleforge.store import ProjectStore
from angleforge.timeline import RevisionConflict

def test_persistence_undo_redo_stale_and_branch(tmp_path):
    store=ProjectStore(tmp_path)
    p=store.create('Original')
    changed=store.transact(p['id'],dict(base_revision=0,label='Rename',actor='ai',operations=[dict(type='rename_project',name='New name')]))
    assert changed['revision']==1 and changed['can_undo']
    assert ProjectStore(tmp_path).get(p['id'])['name']=='New name'
    with pytest.raises(RevisionConflict): store.undo(p['id'],0)
    undone=store.undo(p['id'],1)
    assert undone['name']=='Original' and undone['can_redo'] and undone['revision']==2
    restored=store.redo(p['id'],2)
    assert restored['name']=='New name' and restored['revision']==3
    undone=store.undo(p['id'],3)
    branched=store.transact(p['id'],dict(base_revision=4,operations=[dict(type='rename_project',name='Branch')]))
    assert not branched['can_redo']
    assert len(branched['history'])==5

def test_background_import_retained_on_undo(tmp_path):
    store=ProjectStore(tmp_path); p=store.create('Original')
    store.transact(p['id'],dict(base_revision=0,operations=[dict(type='rename_project',name='Renamed')]))
    store.add_asset(p['id'],dict(id='a',name='A',kind='video',status='ready',duration_us=1000000,has_audio=True))
    p=store.get(p['id']); result=store.undo(p['id'],p['revision'])
    assert result['name']=='Original' and 'a' in result['assets']

def test_document_snapshot_atomic(tmp_path):
    store=ProjectStore(tmp_path); p=store.create('Project')
    path=tmp_path/'projects'/p['id']/'project.json'
    assert path.exists()
    assert len(store.list())==1
    assert not list(path.parent.glob('*.tmp'))
