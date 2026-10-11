"""SQLite coordinator for atomic, revision-checked edits and persistent history."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import sqlite3
import threading

from .timeline import new_project, apply_transaction, RevisionConflict, TimelineError, now, identifier

EDITABLE_FIELDS=('name','settings','sync','timeline')


class ProjectStore:
    def __init__(self, data_dir):
        self.root=Path(data_dir).resolve(); self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.db=sqlite3.connect(self.root/'projects.sqlite3',check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,document TEXT NOT NULL,updated_at TEXT NOT NULL,cursor INTEGER NOT NULL DEFAULT 0)')
        self.db.execute('CREATE TABLE IF NOT EXISTS edits(project_id TEXT,position INTEGER,before_json TEXT,after_json TEXT,PRIMARY KEY(project_id,position))')
        self.db.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,project_id TEXT,document TEXT NOT NULL)')
        self.db.commit()

    def _raw(self,id):
        row=self.db.execute('SELECT document,cursor FROM projects WHERE id=?',(id,)).fetchone()
        if row is None: raise KeyError('Project not found')
        return json.loads(row[0]),row[1]

    def _decorate(self,p,cursor):
        p['can_undo']=cursor>0
        p['can_redo']=self.db.execute('SELECT 1 FROM edits WHERE project_id=? AND position=?',(p['id'],cursor+1)).fetchone() is not None
        p['history']=[json.loads(r[0]) for r in self.db.execute('SELECT document FROM events WHERE project_id=? ORDER BY rowid DESC LIMIT 100',(p['id'],))]
        return p

    def get(self,id):
        with self.lock:
            p,cursor=self._raw(id); return self._decorate(p,cursor)

    def list(self):
        with self.lock:
            result=[]
            for raw,updated in self.db.execute('SELECT document,updated_at FROM projects ORDER BY updated_at DESC'):
                p=json.loads(raw); result.append(dict(id=p['id'],name=p['name'],revision=p['revision'],updated_at=updated))
            return result

    def _snapshot(self,p):
        # A worker may have committed again since the caller released its lock.
        # Read the latest database revision under the same lock as publication.
        with self.lock:
            current,cursor=self._raw(p['id']); current=self._decorate(current,cursor)
            directory=self.root/'projects'/p['id']; directory.mkdir(parents=True,exist_ok=True)
            temporary=directory/('project.'+identifier()+'.tmp')
            try:
                temporary.write_text(json.dumps(current,indent=2,ensure_ascii=False),encoding='utf-8')
                os.replace(temporary,directory/'project.json')
            finally: temporary.unlink(missing_ok=True)

    def _save(self,p,cursor):
        # The database is authoritative; JSON is an inspectable portable snapshot.
        self.db.execute('UPDATE projects SET document=?,updated_at=?,cursor=? WHERE id=?',(json.dumps(p),now(),cursor,p['id']))

    def create(self,name='Untitled edit'):
        p=new_project(name)
        with self.lock,self.db:
            self.db.execute('INSERT INTO projects VALUES(?,?,?,0)',(p['id'],json.dumps(p),now()))
        self._snapshot(p)
        return p

    def _event(self,p,label,actor,kind='edit',provenance=None):
        event=dict(id=identifier(),revision=p['revision'],label=label,actor=actor,kind=kind,created_at=now())
        if provenance: event['provenance']=provenance
        self.db.execute('INSERT INTO events VALUES(?,?,?)',(event['id'],p['id'],json.dumps(event)))

    def transact(self,id,transaction,provenance=None):
        with self.lock,self.db:
            p,cursor=self._raw(id)
            result=apply_transaction(p,transaction)
            if provenance: result['analysis'].append({'type':'ai_edit','revision':result['revision'],**provenance})
            before={k:p[k] for k in EDITABLE_FIELDS}; after={k:result[k] for k in EDITABLE_FIELDS}
            self.db.execute('DELETE FROM edits WHERE project_id=? AND position>?',(id,cursor))
            self.db.execute('INSERT INTO edits VALUES(?,?,?,?)',(id,cursor+1,json.dumps(before),json.dumps(after)))
            self._event(result,transaction.get('label','Edit'),transaction.get('actor','manual'),provenance=provenance)
            self._save(result,cursor+1)
        result=self.get(id); self._snapshot(result); return result

    def _travel(self,id,revision,redo):
        with self.lock,self.db:
            p,cursor=self._raw(id)
            if type(revision) is not int or revision!=p['revision']:
                raise RevisionConflict('Project revision changed; refresh before undo/redo.')
            position=cursor+1 if redo else cursor
            row=self.db.execute('SELECT before_json,after_json FROM edits WHERE project_id=? AND position=?',(id,position)).fetchone()
            if row is None: raise TimelineError('Nothing to redo.' if redo else 'Nothing to undo.')
            p.update(json.loads(row[1 if redo else 0])); p['revision']+=1
            # Newly imported assets remain available when undoing an older edit.
            if p['sync']['reference_asset_id'] is None:
                first=next((a for a in p['assets'].values() if a['status']=='ready' and a.get('kind')=='video'),None)
                if first:
                    p['sync']['reference_asset_id']=first['id']; p['sync']['offsets'][first['id']]=0
            self._event(p,'Redo' if redo else 'Undo','manual','redo' if redo else 'undo')
            self._save(p,cursor+(1 if redo else -1))
        p=self.get(id); self._snapshot(p); return p

    def undo(self,id,revision): return self._travel(id,revision,False)
    def redo(self,id,revision): return self._travel(id,revision,True)

    def add_asset(self,id,asset):
        with self.lock,self.db:
            p,cursor=self._raw(id); p['assets'][asset['id']]=copy.deepcopy(asset); p['revision']+=1
            if asset['status'] in ('processing','ready') and p['sync']['reference_asset_id'] is None and asset.get('kind')=='video':
                p['sync']['reference_asset_id']=asset['id']; p['sync']['offsets'][asset['id']]=0; p['sync']['confidence'][asset['id']]=1.
            self._save(p,cursor)
        p=self.get(id); self._snapshot(p); return p

    def add_analysis(self,id,analysis):
        with self.lock,self.db:
            p,cursor=self._raw(id)
            p['analysis']=[a for a in p['analysis'] if a.get('asset_id')!=analysis.get('asset_id')]+[analysis]
            p['revision']+=1; self._save(p,cursor)
        p=self.get(id); self._snapshot(p); return p

    def save(self,id):
        p=self.get(id); self._snapshot(p); return dict(saved=True,revision=p['revision'])

    def close(self):
        with self.lock: self.db.close()
