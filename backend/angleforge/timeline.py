"""Pure canonical timeline transactions. All intervals are half-open microseconds.

No IO, models, shell commands or provider data can bypass this module.
"""
from __future__ import annotations

import copy
import math
from datetime import datetime, timezone
from uuid import uuid4

TIMEBASE = 1_000_000
MAX_TIME = 24 * 3600 * TIMEBASE


class TimelineError(ValueError):
    pass


class RevisionConflict(TimelineError):
    pass


def identifier():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc).isoformat()


def new_project(name='Untitled edit'):
    return dict(schema_version=1, id=identifier(), name=_name(name), revision=0,
                timebase=TIMEBASE, settings=dict(width=1920, height=1080, fps=dict(num=30, den=1)),
                assets={}, sync=dict(reference_asset_id=None, offsets={}, confidence={}, anchors=[], warnings=[], edges=[]),
                timeline=dict(video=[], audio=[], captions=[], master_audio_asset_id=None, music=[],
                              audio_settings=dict(gain=1.0, muted=False, fade_in_us=0, fade_out_us=0), audio_warnings=[]),
                analysis=[], history=[], can_undo=False, can_redo=False)


def _int(value, label, minimum=0, maximum=MAX_TIME):
    if type(value) is not int or not minimum <= value <= maximum:
        raise TimelineError(f'{label} must be an integer between {minimum} and {maximum}.')
    return value


def _number(value, label, minimum=0, maximum=1):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise TimelineError(f'{label} must be a finite number between {minimum} and {maximum}.')
    return value


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise TimelineError('Name must contain 1–200 characters.')
    return value.strip()


def _asset(p, id, video=False, audio=False):
    if not isinstance(id, str) or id not in p['assets']:
        raise TimelineError('Unknown asset ID.')
    asset = p['assets'][id]
    if asset['status'] != 'ready':
        raise TimelineError('The source is not ready or its media is missing.')
    if video and asset.get('kind') != 'video':
        raise TimelineError('A video source is required.')
    if audio and not asset.get('has_audio'):
        raise TimelineError('This source has no audio stream.')
    return asset


def _range(asset, start, end):
    _int(start, 'Source in'); _int(end, 'Source out')
    if not start < end <= asset['duration_us']:
        raise TimelineError('Source range is empty or outside available footage.')


def duration_us(p):
    return sum(s['source_out_us'] - s['source_in_us'] for s in p['timeline']['video'])


def _segment(p, id):
    for s in p['timeline']['video']:
        if s['id'] == id:
            return s
    raise TimelineError('Unknown video segment.')


def _reflow(p):
    position = 0
    for s in p['timeline']['video']:
        s['output_in_us'] = position
        position += s['source_out_us'] - s['source_in_us']
    if position > MAX_TIME:
        raise TimelineError('The edit exceeds the 24 hour limit.')


def _rebuild_audio(p):
    tl = p['timeline']; tl['audio'] = []; tl['audio_warnings'] = []
    id = tl.get('master_audio_asset_id')
    if id is None:
        return
    a = _asset(p, id, audio=True)
    offset = p['sync']['offsets'].get(id, 0)
    settings = tl.setdefault('audio_settings', dict(gain=1., muted=False, fade_in_us=0, fade_out_us=0))
    for s in tl['video']:
        length = s['source_out_us']-s['source_in_us']
        begin = s['event_in_us'] - offset
        lo, hi = max(0, begin), min(a['duration_us'], begin+length)
        if lo != begin or hi != begin+length:
            tl['audio_warnings'].append(f'Audio has no coverage for part of segment {s["id"]}; that interval is silent.')
        if hi > lo:
            tl['audio'].append(dict(id='audio-'+s['id'], asset_id=id, source_in_us=lo, source_out_us=hi,
                                   output_in_us=s['output_in_us']+lo-begin, gain=settings['gain'], muted=settings['muted'],
                                   fade_in_us=min(settings['fade_in_us'], (hi-lo)//2),
                                   fade_out_us=min(settings['fade_out_us'], (hi-lo)//2)))


def _crop(crop):
    if set(crop) != {'mode','x','y'} or crop['mode'] not in ('fit','fill'):
        raise TimelineError('Only fit/fill crop modes are supported.')
    _number(crop['x'],'Crop X'); _number(crop['y'],'Crop Y')


def _settings(settings):
    if set(settings) != {'width','height','fps'}:
        raise TimelineError('Unsupported output setting.')
    for axis in ('width','height'):
        _int(settings[axis], axis, 64, 4096)
        if settings[axis] % 2: raise TimelineError('Output dimensions must be even.')
    fps = settings['fps']
    if not isinstance(fps,dict) or set(fps) != {'num','den'}:
        raise TimelineError('FPS requires num and den.')
    _int(fps['num'],'FPS numerator',1,240000); _int(fps['den'],'FPS denominator',1,10000)
    if not 1 <= fps['num']/fps['den'] <= 120:
        raise TimelineError('Output frame rate must be between 1 and 120 fps.')


def validate_project(p):
    if p.get('schema_version') != 1 or p.get('timebase') != TIMEBASE:
        raise TimelineError('Unsupported project schema or time base.')
    _settings(p['settings'])
    position=0; ids=set()
    if len(p['timeline']['video'])>2000: raise TimelineError('Maximum 2000 video segments.')
    for s in p['timeline']['video']:
        if s['id'] in ids: raise TimelineError('Duplicate segment ID.')
        ids.add(s['id']); a=_asset(p,s['asset_id'],video=True)
        _range(a,s['source_in_us'],s['source_out_us'])
        if s['output_in_us'] != position: raise TimelineError('Video segments must be contiguous.')
        _int(s['event_in_us'],'Event time',-MAX_TIME,MAX_TIME)
        _crop(s['crop']); position+=s['source_out_us']-s['source_in_us']
    for s in p['timeline']['audio']+p['timeline']['music']:
        _range(_asset(p,s['asset_id'],audio=True),s['source_in_us'],s['source_out_us'])
        _int(s['output_in_us'],'Audio output position'); _number(s['gain'],'Audio gain',0,4)
        if type(s.get('muted',False)) is not bool: raise TimelineError('Mute must be boolean.')
        length=s['source_out_us']-s['source_in_us']
        _int(s.get('fade_in_us',0),'Fade in',0,length); _int(s.get('fade_out_us',0),'Fade out',0,length)
    for c in p['timeline']['captions']:
        _int(c['start_us'],'Caption start'); _int(c['end_us'],'Caption end')
        if not c['start_us']<c['end_us']<=position: raise TimelineError('Caption lies outside output.')
        if not isinstance(c['text'],str) or not c['text'].strip() or len(c['text'])>500:
            raise TimelineError('Caption text must contain 1–500 characters.')
    return p


# Required keys, optional keys. Reject unknown inputs instead of forwarding them.
OP_FIELDS={
 'insert_segment':({'asset_id','source_in_us','source_out_us'},{'index'}),
 'split_segment':({'segment_id','at_us'},set()),
 'trim_segment':({'segment_id','source_in_us','source_out_us'},set()),
 'remove_segment':({'segment_id','ripple'},set()),
 'reorder_segments':({'segment_ids'},set()),
 'switch_angle':({'asset_id','start_us','end_us'},set()),
 'set_audio_source':({'asset_id'},set()),
 'set_audio_gain':({'gain'},{'muted','fade_in_us','fade_out_us'}),
 'set_crop':({'segment_id','mode','x','y'},set()),
 'set_settings':({'width','height','fps'},set()),
 'rename_project':({'name'},set()),
 'set_sync':({'reference_asset_id','offsets'},{'confidence','anchors','warnings','edges'}),
 'add_music':({'asset_id','source_in_us','source_out_us','output_in_us','gain'},set()),
 'remove_music':({'id'},set()),
 'add_caption':({'start_us','end_us','text'},set()),
 'remove_caption':({'id'},set()),
}


def _split(p,s,at):
    start=s['output_in_us']; length=s['source_out_us']-s['source_in_us']
    if not start<at<start+length: raise TimelineError('Split must lie inside the selected segment.')
    right=copy.deepcopy(s); right['id']=identifier()
    delta=at-start
    right.update(source_in_us=s['source_in_us']+delta, event_in_us=s['event_in_us']+delta, output_in_us=at)
    s['source_out_us']=right['source_in_us']
    p['timeline']['video'].insert(p['timeline']['video'].index(s)+1,right)


def _op(p,op):
    if not isinstance(op,dict) or op.get('type') not in OP_FIELDS:
        raise TimelineError('Unsupported editing operation.')
    kind=op['type']; required,optional=OP_FIELDS[kind]
    keys=set(op)-{'type'}
    if not required<=keys or keys-required-optional:
        raise TimelineError(f'Invalid arguments for {kind}.')
    tl=p['timeline']; video=tl['video']; offsets=p['sync']['offsets']
    if kind=='insert_segment':
        a=_asset(p,op['asset_id'],video=True); _range(a,op['source_in_us'],op['source_out_us'])
        index=_int(op.get('index',len(video)),'Insert index',0,len(video))
        video.insert(index,dict(id=identifier(),asset_id=a['id'],source_in_us=op['source_in_us'],source_out_us=op['source_out_us'],output_in_us=0,
                               event_in_us=op['source_in_us']+offsets.get(a['id'],0),crop=dict(mode='fit',x=.5,y=.5)))
        if len(video)==1 and tl['master_audio_asset_id'] is None and a['has_audio']:
            tl['master_audio_asset_id']=a['id']
    elif kind=='split_segment':
        _int(op['at_us'],'Split position'); _split(p,_segment(p,op['segment_id']),op['at_us'])
    elif kind=='trim_segment':
        s=_segment(p,op['segment_id']); _range(_asset(p,s['asset_id']),op['source_in_us'],op['source_out_us'])
        s['event_in_us']+=op['source_in_us']-s['source_in_us']
        s.update(source_in_us=op['source_in_us'],source_out_us=op['source_out_us'])
    elif kind=='remove_segment':
        if op['ripple'] is not True: raise TimelineError('Only explicit ripple delete is supported.')
        video.remove(_segment(p,op['segment_id']))
    elif kind=='reorder_segments':
        ids=op['segment_ids']
        if not isinstance(ids,list) or any(not isinstance(id,str) for id in ids) or len(ids)!=len(video) or set(ids)!={s['id'] for s in video}:
            raise TimelineError('Reorder requires every segment ID exactly once.')
        lookup={s['id']:s for s in video}; tl['video']=[lookup[id] for id in ids]
    elif kind=='switch_angle':
        a=_asset(p,op['asset_id'],video=True)
        if a['id'] not in offsets: raise TimelineError('Align this angle first using sync or a manual offset.')
        lo=_int(op['start_us'],'Switch start'); hi=_int(op['end_us'],'Switch end')
        if not lo<hi<=duration_us(p): raise TimelineError('Switch range is outside the edit.')
        for boundary in (lo,hi):
            for s in list(video):
                if s['output_in_us']<boundary<s['output_in_us']+s['source_out_us']-s['source_in_us']:
                    _split(p,s,boundary); break
        for s in video:
            if lo<=s['output_in_us']<hi:
                begin=s['event_in_us']-offsets[a['id']]; end=begin+s['source_out_us']-s['source_in_us']
                _range(a,begin,end)
                s.update(asset_id=a['id'],source_in_us=begin,source_out_us=end)
    elif kind=='set_audio_source':
        id=op['asset_id']
        if id is not None:
            _asset(p,id,audio=True)
            if id not in offsets and id!=tl.get('master_audio_asset_id'):
                raise TimelineError('Align the audio source before selecting it as master.')
        tl['master_audio_asset_id']=id
    elif kind=='set_audio_gain':
        settings=tl['audio_settings']; _number(op['gain'],'Audio gain',0,4)
        settings['gain']=op['gain']
        if 'muted' in op:
            if type(op['muted']) is not bool: raise TimelineError('Mute must be boolean.')
            settings['muted']=op['muted']
        for key in ('fade_in_us','fade_out_us'):
            if key in op: settings[key]=_int(op[key],key,0,5*TIMEBASE)
    elif kind=='set_crop':
        c={k:op[k] for k in ('mode','x','y')}; _crop(c); _segment(p,op['segment_id'])['crop']=c
    elif kind=='set_settings':
        s={k:op[k] for k in ('width','height','fps')}; _settings(s); p['settings']=s
    elif kind=='rename_project': p['name']=_name(op['name'])
    elif kind=='set_sync':
        ref=op['reference_asset_id']; _asset(p,ref)
        given=op['offsets']
        if not isinstance(given,dict) or ref not in given: raise TimelineError('Sync offsets need the reference asset.')
        for id,value in given.items():
            _asset(p,id); _int(value,'Sync offset',-MAX_TIME,MAX_TIME)
        confidence=op.get('confidence',{})
        if not isinstance(confidence,dict): raise TimelineError('Invalid confidence mapping.')
        for id,value in confidence.items():
            _asset(p,id); _number(value,'Confidence')
        anchors=op.get('anchors',[])
        if not isinstance(anchors,list) or len(anchors)>100: raise TimelineError('Invalid manual anchors.')
        for anchor in anchors:
            if not isinstance(anchor,dict) or set(anchor)!={'asset_id','source_us','event_us'}:
                raise TimelineError('Anchor requires asset_id, source_us and event_us.')
            a=_asset(p,anchor['asset_id']); _int(anchor['source_us'],'Anchor source',0,a['duration_us']); _int(anchor['event_us'],'Anchor event',-MAX_TIME,MAX_TIME)
            if given.get(a['id']) != anchor['event_us']-anchor['source_us']:
                raise TimelineError('Manual anchor does not match its proposed offset.')
        warnings=op.get('warnings',[]); edges=op.get('edges',[])
        if not isinstance(warnings,list) or any(not isinstance(w,str) or len(w)>1000 for w in warnings): raise TimelineError('Invalid sync warnings.')
        if not isinstance(edges,list) or len(edges)>100: raise TimelineError('Invalid sync edges.')
        p['sync']=dict(reference_asset_id=ref,offsets=copy.deepcopy(given),confidence=copy.deepcopy(confidence),anchors=copy.deepcopy(anchors),warnings=warnings,edges=edges)
        for s in video: s['event_in_us']=s['source_in_us']+given.get(s['asset_id'],0)
    elif kind=='add_music':
        a=_asset(p,op['asset_id'],audio=True); _range(a,op['source_in_us'],op['source_out_us'])
        _int(op['output_in_us'],'Music position'); _number(op['gain'],'Music gain',0,4)
        tl['music'].append(dict(id=identifier(),**{k:v for k,v in op.items() if k!='type'},muted=False,fade_in_us=0,fade_out_us=0))
    elif kind=='add_caption':
        tl['captions'].append(dict(id=identifier(),**{k:v for k,v in op.items() if k!='type'}))
    elif kind in ('remove_music','remove_caption'):
        items=tl['music' if kind=='remove_music' else 'captions']
        found=next((x for x in items if x['id']==op['id']),None)
        if found is None: raise TimelineError('Unknown item ID.')
        items.remove(found)
    _reflow(p)


def apply_transaction(project, transaction):
    if not isinstance(transaction,dict) or set(transaction)-{'base_revision','label','actor','operations'}:
        raise TimelineError('Invalid transaction envelope.')
    if type(transaction.get('base_revision')) is not int or transaction['base_revision']!=project['revision']:
        raise RevisionConflict(f'Project revision changed (current {project["revision"]}). Refresh and review the edit again.')
    ops=transaction.get('operations')
    if not isinstance(ops,list) or not 1<=len(ops)<=100: raise TimelineError('A transaction requires 1–100 operations.')
    if transaction.get('actor','manual') not in ('manual','ai'): raise TimelineError('Invalid edit actor.')
    _name(transaction.get('label','Edit'))
    p=copy.deepcopy(project)
    try:
        for op in ops: _op(p,op)
        _rebuild_audio(p); validate_project(p)
    except (KeyError,TypeError,AttributeError) as e:
        raise TimelineError('Malformed operation arguments.') from e
    p['revision']+=1
    return p
