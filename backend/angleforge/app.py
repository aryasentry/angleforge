"""Loopback-only HTTP coordinator; external models can propose, never execute IO."""
from __future__ import annotations
import asyncio
import base64
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .timeline import TimelineError, RevisionConflict, identifier, validate_project
from .store import ProjectStore
from .jobs import JobManager, JobCancelled
from . import media, exporter, sync, providers, director

REPO=Path(__file__).resolve().parents[2]
ALLOWED_EXTENSIONS={'.mp4','.mov','.m4v','.webm','.mkv','.wav','.mp3','.m4a','.aac','.flac','.ogg'}
MAX_UPLOAD=2*1024**3
SAFE_ID=re.compile(r'^[a-zA-Z0-9_-]{1,80}$')


def create_app(data_dir=None):
    load_dotenv(REPO/'.env',override=False)
    root=Path(data_dir or os.getenv('ANGLEFORGE_DATA_DIR',REPO/'data')).resolve()
    root.mkdir(parents=True,exist_ok=True)
    store=ProjectStore(root); jobs=JobManager(root)
    token=secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        # These files are never published as finished media. Recover abandoned work.
        for pattern in ('assets/*/.prepare-*','exports/*/.render-*'):
            for path in root.glob(pattern):
                if path.is_dir(): shutil.rmtree(path,ignore_errors=True)
        for summary in store.list():
            for asset in store.get(summary['id'])['assets'].values():
                if asset['status']=='processing':
                    asset.update(status='failed',error='Preparation was interrupted by restart. Reimport this source to retry.')
                    store.add_asset(summary['id'],asset)
        yield
        await asyncio.to_thread(jobs.shutdown)
        store.close()

    app=FastAPI(title='AngleForge',version='0.1.0',lifespan=lifespan,docs_url=None,redoc_url=None)
    app.state.store=store; app.state.jobs=jobs; app.state.root=root
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','[::1]'])

    @app.middleware('http')
    async def local_boundary(request,call_next):
        origin=request.headers.get('origin')
        if origin:
            allowed={'http://127.0.0.1:8765','http://localhost:8765','http://127.0.0.1:5173','http://localhost:5173',str(request.base_url).rstrip('/')}
            if origin not in allowed:
                return JSONResponse({'detail':'Cross-origin requests are not permitted.'},403)
        if request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse({'detail':'Cross-site requests are not permitted.'},403)
        if request.method not in ('GET','HEAD','OPTIONS') and request.url.path.startswith('/api/'):
            if not secrets.compare_digest(request.headers.get('x-angleforge-token',''),token):
                return JSONResponse({'detail':'Missing or invalid local session token. Reload the editor.'},403)
        length=request.headers.get('content-length','0')
        if not length.isdigit() or int(length)>5*MAX_UPLOAD:
            return JSONResponse({'detail':'Request is too large.'},413)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        return response

    @app.exception_handler(TimelineError)
    async def timeline_error(request,error):
        return JSONResponse({'detail':str(error)},409 if isinstance(error,RevisionConflict) else 400)

    @app.exception_handler(ValueError)
    async def value_error(request,error): return JSONResponse({'detail':str(error)},400)

    @app.exception_handler(KeyError)
    async def not_found(request,error): return JSONResponse({'detail':'Project, asset or job was not found.'},404)

    async def body(request,max_bytes=100000):
        raw=await request.body()
        if len(raw)>max_bytes: raise HTTPException(413,'JSON request is too large.')
        try: data=json.loads(raw or '{}')
        except (json.JSONDecodeError,UnicodeDecodeError): raise HTTPException(400,'Invalid JSON.')
        if not isinstance(data,dict): raise HTTPException(400,'Expected a JSON object.')
        return data

    def asset_dir(id):
        if not isinstance(id,str) or not SAFE_ID.fullmatch(id): raise KeyError(id)
        directory=root/'assets'/id
        if not directory.is_dir(): raise KeyError(id)
        return directory

    def resolve_original(id):
        directory=asset_dir(id)
        candidates=list(directory.glob('original.*'))
        if len(candidates)!=1 or candidates[0].suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ValueError('Original media is missing. Reimport the source file.')
        path=candidates[0]
        if path.is_symlink() or not path.resolve().is_relative_to(root/'assets'):
            raise ValueError('Media reference is outside the local asset directory.')
        return path

    def project(id,revision=None):
        p=store.get(id)
        if revision is not None and (type(revision) is not int or revision!=p['revision']):
            raise RevisionConflict('Project revision changed. Refresh and review again.')
        return p

    @app.get('/api/session')
    def session(): return {'token':token}

    @app.get('/api/health')
    def health(): return dict(status='ok',version='0.1.0',preflight=media.preflight(),data_directory=str(root),transcription={'available':False,'message':'No local transcription model is installed. No models are downloaded automatically.'})

    @app.get('/api/projects')
    def projects(): return {'projects':store.list()}

    @app.post('/api/projects')
    async def create(request:Request): return store.create((await body(request)).get('name','Untitled edit'))

    @app.get('/api/projects/{id}')
    def get_project(id:str):
        p=project(id)
        for a in p['assets'].values():
            if a['status']=='ready':
                try:
                    resolve_original(a['id'])
                    if a['kind']=='video' and not (asset_dir(a['id'])/'proxy.mp4').exists(): raise ValueError('Editing proxy is missing; reimport the source.')
                except (ValueError,KeyError) as error:
                    a['status']='failed'; a['error']=str(error)
        return p

    @app.get('/api/projects/{id}/document')
    def document(id:str):
        return JSONResponse(project(id),headers={'Content-Disposition':'attachment; filename="angleforge-project.json"'})

    @app.post('/api/projects/{id}/transactions')
    async def transact(id:str,request:Request):
        data=await body(request); proposal_id=data.pop('proposal_job_id',None); provenance=None
        if proposal_id is not None:
            if not isinstance(proposal_id,str): raise ValueError('Invalid proposal job ID.')
            job=jobs.get(proposal_id); result=job.get('result') or {}
            if job['status']!='succeeded' or job['kind']!='ai' or data.get('actor')!='ai' or result.get('project_id')!=id or result.get('base_revision')!=data.get('base_revision') or result.get('operations')!=data.get('operations'):
                raise ValueError('The transaction must match the completed proposal, project and revision.')
            provenance={'proposal_job_id':proposal_id,**{key:result[key] for key in ('provider','model','usage','sampling','disclosure') if key in result}}
        elif data.get('actor')=='ai':
            raise ValueError('AI edits require a completed proposal job ID.')
        return store.transact(id,data,provenance=provenance)

    @app.post('/api/projects/{id}/undo')
    async def undo(id:str,request:Request): return store.undo(id,(await body(request)).get('base_revision'))

    @app.post('/api/projects/{id}/redo')
    async def redo(id:str,request:Request): return store.redo(id,(await body(request)).get('base_revision'))

    @app.post('/api/projects/{id}/save')
    def save(id:str): return store.save(id)

    def ingest(project_id,original,asset_id,name,ctx):
        try:
            asset=media.prepare_asset(original,original.parent,asset_id,name,ctx)
            ctx.check_cancelled()
            store.add_asset(project_id,asset)
            return dict(project_id=project_id,asset_id=asset_id,asset=asset)
        except Exception as error:
            # Keep the user's copied original even if preparation fails/cancels.
            store.add_asset(project_id,dict(id=asset_id,name=name,kind='video',status='failed',duration_us=0,width=0,height=0,fps={'num':30,'den':1},has_audio=False,rotation=0,fingerprint='',proxy={'source_origin_us':0,'timebase':1000000},waveform=[],error='Import canceled.' if isinstance(error,JobCancelled) else str(error)))
            raise

    @app.post('/api/projects/{id}/import')
    async def import_files(id:str,files:list[UploadFile]=File(...)):
        p=project(id)
        if not 1<=len(files)<=5: raise ValueError('Import 1–5 clips at a time.')
        if len(p['assets'])+len(files)>50: raise ValueError('This release supports up to 50 assets per project.')
        for file in files:
            if Path(file.filename or '').suffix.lower() not in ALLOWED_EXTENSIONS:
                raise ValueError('Unsupported extension. Import MP4/MOV video or WAV/MP3/M4A audio.')
        created=[]
        for file in files:
            aid=identifier(); directory=root/'assets'/aid; directory.mkdir(parents=True)
            name=Path((file.filename or 'media').replace('\\','/')).name[:200]
            original=directory/('original'+Path(name).suffix.lower())
            size=0
            try:
                with original.open('wb') as out:
                    while chunk:=await file.read(1024*1024):
                        size+=len(chunk)
                        if size>MAX_UPLOAD: raise HTTPException(413,'Maximum imported file size is 2 GB.')
                        out.write(chunk)
                if size==0: raise ValueError('Empty uploads cannot be imported.')
                store.add_asset(id,dict(id=aid,name=name,kind='audio' if original.suffix in {'.wav','.mp3','.m4a','.aac','.flac','.ogg'} else 'video',status='processing',duration_us=0,width=0,height=0,fps={'num':30,'den':1},has_audio=False,rotation=0,fingerprint='',proxy={'source_origin_us':0,'timebase':1000000},waveform=[],error=None))
                created.append(jobs.submit('import',lambda ctx,original=original,aid=aid,name=name:ingest(id,original,aid,name,ctx)))
            except Exception:
                if size==0 or size>MAX_UPLOAD: shutil.rmtree(directory,ignore_errors=True)
                raise
            finally: await file.close()
        return {'jobs':created}

    @app.get('/api/media/{id}/{variant}')
    def get_media(id:str,variant:str):
        directory=asset_dir(id)
        variants={'proxy':'proxy.mp4','thumbnail':'thumbnail.jpg','audio':'audio.wav','monitor':'monitor.m4a'}
        if variant=='original': path=resolve_original(id)
        elif variant in variants: path=directory/variants[variant]
        elif re.fullmatch(r'analysis-[0-5]',variant): path=directory/(variant+'.jpg')
        else: raise KeyError(variant)
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root/'assets'): raise KeyError(id)
        return FileResponse(path)

    @app.get('/api/jobs')
    def list_jobs(): return {'jobs':jobs.list()}

    @app.get('/api/jobs/{id}')
    def get_job(id:str): return jobs.get(id)

    @app.post('/api/jobs/{id}/cancel')
    def cancel(id:str): return jobs.cancel(id)

    @app.post('/api/projects/{id}/sync')
    async def synchronize(id:str,request:Request):
        data=await body(request); p=project(id,data.get('base_revision',-1))
        assets={aid:a for aid,a in p['assets'].items() if a['status']=='ready' and a['kind']=='video'}
        paths={aid:asset_dir(aid)/'audio.wav' for aid,a in assets.items() if a['has_audio']}
        ref=p['sync']['reference_asset_id']
        if ref not in assets: ref=next(iter(assets),None)
        def run(ctx):
            result=sync.propose_sync(assets,paths,ref,ctx)
            return {**result,'base_revision':p['revision'],'project_id':id}
        return {'job':jobs.submit('sync',run)}

    @app.post('/api/projects/{id}/analyze')
    async def analyze(id:str,request:Request):
        data=await body(request); p=project(id); aid=data.get('asset_id'); a=p['assets'].get(aid)
        if not a or a['status']!='ready': raise ValueError('Select a ready source for analysis.')
        def run(ctx):
            result=media.analyze_asset(resolve_original(aid),asset_dir(aid),a,ctx)
            ctx.check_cancelled(); store.add_analysis(id,result)
            return {**result,'project_id':id}
        return {'job':jobs.submit('analysis',run)}

    @app.post('/api/projects/{id}/export')
    async def export(id:str,request:Request):
        data=await body(request); p=project(id,data.get('base_revision',-1))
        name=data.get('name','angleforge-edit.mp4')
        if not isinstance(name,str) or not re.fullmatch(r'[\w .-]{1,100}\.mp4',name) or name.startswith('.') or '/' in name or '\\' in name:
            raise ValueError('Choose an MP4 filename, without folders or special characters.')
        validate_project(p)
        if not p['timeline']['video']: raise ValueError('Add footage to the timeline before exporting.')
        export_id=identifier(); destination=root/'exports'/export_id/name
        def run(ctx):
            result=exporter.render_project(p,resolve_original,destination,ctx)
            return {**result,'project_id':id,'export_id':export_id,'output_path':str(destination),'download_url':f'/api/exports/{export_id}/{name}'}
        return {'job':jobs.submit('export',run),'output_path':str(destination)}

    @app.get('/api/exports/{export_id}/{name}')
    def download_export(export_id:str,name:str):
        match=next((j for j in jobs.list() if j['status']=='succeeded' and isinstance(j.get('result'),dict) and j['result'].get('export_id')==export_id and j['result'].get('filename')==name),None)
        if not match: raise KeyError(export_id)
        return FileResponse(root/'exports'/export_id/name,filename=name,media_type='video/mp4')

    @app.get('/api/provider')
    def provider(): return providers.provider_status()

    @app.get('/api/provider/models')
    async def models(provider:str='openrouter'): return await providers.list_models(provider)

    @app.post('/api/projects/{id}/command')
    async def command(id:str,request:Request):
        data=await body(request); p=project(id,data.get('base_revision',-1))
        return director.parse_local_command(p,data.get('prompt',''))

    @app.post('/api/projects/{id}/ai/plan')
    async def ai_plan(id:str,request:Request):
        data=await body(request); p=project(id,data.get('base_revision',-1))
        data.pop('_sampled_frames',None)
        choice=data.get('provider','openrouter')
        if choice not in providers.KEYS: raise ValueError('Choose OpenRouter or OpenCode Zen.')
        if not os.getenv(providers.KEYS[choice]): raise ValueError(f'{choice} is not configured. Set its backend environment key locally; manual editing remains available.')
        consent=data.get('consent')
        if not isinstance(consent,dict) or consent.get('metadata') is not True: raise ValueError('Review and approve the external metadata disclosure first.')
        asset_ids=data.get('asset_ids')
        if not isinstance(asset_ids,list) or not asset_ids or len(asset_ids)>20 or any(not isinstance(aid,str) or aid not in p['assets'] for aid in asset_ids) or len(set(asset_ids))!=len(asset_ids):
            raise ValueError('Explicitly select the assets whose metadata may be sent.')
        with_frames=consent.get('frames') is True
        if with_frames and (choice!='openrouter' or len(asset_ids)>3 or any(p['assets'][aid]['kind']!='video' or p['assets'][aid]['status']!='ready' for aid in asset_ids)):
            raise ValueError('Sampled-frame planning requires OpenRouter and 1–3 ready video assets; Zen image capabilities are unverified.')
        def run(ctx):
            if with_frames:
                frames=[]
                for aid in asset_ids:
                    analysis=media.analyze_asset(resolve_original(aid),asset_dir(aid),p['assets'][aid],ctx)
                    p['analysis']=[a for a in p['analysis'] if a.get('asset_id')!=aid]+[analysis]
                    candidates=[f for f in analysis['frames'] if f.get('image_url')]
                    indices=sorted({round(i*(len(candidates)-1)/3) for i in range(4)}) if candidates else []
                    for index in indices:
                        frame=candidates[index]
                        variant=frame['image_url'].rsplit('/',1)[-1]
                        if not re.fullmatch(r'analysis-[0-5]',variant): raise ValueError('Invalid cached frame reference.')
                        image_path=asset_dir(aid)/(variant+'.jpg')
                        if image_path.is_symlink() or not image_path.resolve().is_relative_to(asset_dir(aid).resolve()):
                            raise ValueError('Cached sample is outside its imported asset directory.')
                        raw=image_path.read_bytes()
                        if len(raw)>256000: raise ValueError('Sample frame is larger than the configured transfer limit.')
                        frames.append({'asset_id':aid,'source_us':frame.get('image_source_us',frame['source_us']),'mime_type':'image/jpeg','data_url':'data:image/jpeg;base64,'+base64.b64encode(raw).decode('ascii')})
                ctx.check_cancelled(); data['_sampled_frames']=frames
            result=asyncio.run(director.plan_edit(p,data,ctx))
            return {**result,'project_id':id,'proposal_job_id':ctx.id}
        return {'job':jobs.submit('ai',run)}

    @app.post('/api/demo')
    def demo():
        p=store.create('Multicam study · synthetic')
        def run(ctx):
            fixture_dir=root/'fixtures'/identifier()
            ctx.progress(None,'Generating labeled synthetic footage locally')
            ctx.run([sys.executable,str(REPO/'scripts'/'generate_fixtures.py'),'--output',str(fixture_dir)])
            manifest=json.loads((fixture_dir/'manifest.json').read_text())
            ids={}
            for index,fixture in enumerate(manifest['files']):
                ctx.progress(index/4,f'Preparing synthetic camera {fixture["camera"]}')
                aid=identifier(); directory=root/'assets'/aid; directory.mkdir(parents=True)
                original=directory/'original.mp4'; shutil.copyfile(fixture_dir/fixture['file'],original)
                ingest(p['id'],original,aid,f'Camera {fixture["camera"]}.mp4',ctx); ids[fixture['camera']]=aid
            current=store.get(p['id'])
            # Demonstration offsets are measured from generated media, not filled from manifest.
            measured=sync.propose_sync(current['assets'],{aid:asset_dir(aid)/'audio.wav' for aid in ids.values()},ids['A'],ctx)
            fields={k:v for k,v in measured.items() if k in {'reference_asset_id','offsets','confidence','anchors','warnings','edges'}}
            operations=[{'type':'set_sync',**fields},{'type':'set_settings','width':1280,'height':720,'fps':{'num':30,'den':1}},
                        {'type':'insert_segment','asset_id':ids['A'],'source_in_us':0,'source_out_us':12_000_000},
                        {'type':'switch_angle','asset_id':ids['B'],'start_us':4_000_000,'end_us':8_000_000},
                        {'type':'switch_angle','asset_id':ids['C'],'start_us':8_000_000,'end_us':11_900_000},
                        {'type':'set_audio_source','asset_id':ids['A']}]
            current=store.transact(p['id'],dict(base_revision=current['revision'],label='Build synthetic multicamera study',actor='manual',operations=operations))
            errors={camera:measured['offsets'][aid]-next(f['event_offset_us'] for f in manifest['files'] if f['camera']==camera) for camera,aid in ids.items()}
            return dict(project_id=p['id'],synthetic=True,measured_sync_error_us=errors,project=current)
        return {'project':p,'job':jobs.submit('demo',run)}

    dist=REPO/'frontend'/'dist'
    if (dist/'assets').is_dir(): app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')

    @app.get('/')
    def index():
        if (dist/'index.html').exists(): return FileResponse(dist/'index.html')
        return HTMLResponse('<title>AngleForge</title><h1>AngleForge backend is ready</h1><p>Build the frontend with <code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code>, then restart the backend.</p>')

    return app
