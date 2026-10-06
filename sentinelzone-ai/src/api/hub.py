"""Display adapters. Demo playback never constructs an inference worker."""
import base64
import logging
from collections import OrderedDict
import copy
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api import detection_ghosts
from src.api import media
from src.api import media_detection
from src.api import media_tracks

ROOT = Path(__file__).resolve().parents[1] / 'demo_assets'
LOCK = threading.RLock()
SNAPSHOTS = OrderedDict()


def remember(packet):
    key = (packet['source'],packet['frame_id'])
    with LOCK:
        SNAPSHOTS[key] = (time.monotonic(),copy.deepcopy({k:v for k,v in packet.items() if k!='jpeg'}))
        SNAPSHOTS.move_to_end(key)
        while len(SNAPSHOTS)>128:SNAPSHOTS.popitem(last=False)
    return packet


@lru_cache(maxsize=4)
def read_manifest(path, modified, size):
    return json.loads(Path(path).read_text())


def curated_demos():
    """Bundled demonstration bundles. Kept addressable, but not listed as scenarios."""
    result = {}
    roots = [ROOT]
    if os.getenv('SENTINEL_DEMO_DIR'): roots.append(Path(os.environ['SENTINEL_DEMO_DIR']))
    for root in roots:
        if not root.exists():continue
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',folder.name):continue
            manifest = folder/'manifest.json'
            if not manifest.is_file() or manifest.stat().st_size>20_000_000:continue
            try:
                data=read_manifest(str(manifest.resolve()),manifest.stat().st_mtime_ns,manifest.stat().st_size)
                if not isinstance(data['frames'],list) or not 0<len(data['frames'])<=1000:continue
                result[folder.name]=(folder,data)
            except (ValueError,KeyError,TypeError):continue
    return result


def demos():
    """Scenario catalog. The UI lists restored original recordings only."""
    return original_demos()


ORIGINAL_PREFIX = 'original:'

ORIGINAL_PROVENANCE = ('Restored original repository recording. Frame is decoded from the original file; '
                       'no detections, tracks, forecast or benchmark values are attached.')


def original_demos():
    """Expose every playable restored video as a hub scenario entry."""
    result = {}
    for item in media.unique_catalog():
        if not item['playable'] or not item.get('frame_count'): continue
        result[ORIGINAL_PREFIX + item['id']] = (Path(item['path']), {
            'title': item['title'] + ' (original)',
            'fps': item.get('fps') or 5,
            'original_media_id': item['id'],
            'width': item.get('width') or 0,
            'height': item.get('height') or 0,
            'source_width': item.get('source_width'),
            'source_height': item.get('source_height'),
            'source_fps': item.get('source_fps'),
            'frames': item['frame_count'],
            'group': item.get('group', ''),
        })
    return result


def original_frame(media_id, index):
    """Build a display packet for a restored recording decoded on demand."""
    entry = media.media_index().get(media_id)
    if entry is None or not entry['playable']: raise HTTPException(404,'Demo not found')
    total = entry.get('frame_count') or 0
    if index < 0 or (total and index >= total): raise HTTPException(404,'Frame not found')
    # Reuse a pre-encoded frame when the clip is warm: no decode, no encode.
    key = media.frame_key(media_id, index)
    cached = media.cached_frame(key)
    if cached is not None:
        jpeg = cached
        path = Path(entry["path"])
        mtime_ns = key[1]
        # The frame itself is cached, but its detection may have been evicted.
        # The detector must then run on the pixels this frame actually has, so the
        # decode is repeated rather than letting it re-read through the shared
        # reader: under concurrent playback that read is a different frame, and the
        # boxes would belong to a frame the panel is not showing.
        if media_detection.cached_detection(media_id, index, mtime_ns):
            frame = None
        else:
            frame, path, mtime_ns = media.decode_frame(media_id, index)
    else:
        frame, path, mtime_ns = media.decode_frame(media_id, index)
        jpeg, _key = media.encode_jpeg(frame, key), key
    raw_boxes, calibration, ppe = media_detection.detect_for_media(
        media_id, str(path), index, mtime_ns, frame=frame)
    width = entry.get('width') or 1920
    height = entry.get('height') or 1080
    # The ground plane is calibrated once per recording from a fixed scan of its own
    # frames, so it does not depend on where playback started.
    plane = media_tracks.plane_for_media(
        media_id, width, height, mtime_ns,
        lambda index: media_detection.detect_for_media(
            media_id, str(path), index, mtime_ns)[0])

    # Plant detected as several parts is one machine, and a person inside a machine
    # is that machine's operator. Both are cleaned here, before they become tracks,
    # so the ground plane never carries a phantom agent. The operator's box stays on
    # the camera frame with their PPE notes.
    boxes, operators = detection_ghosts.suppress_ghosts(raw_boxes)

    # One object, one box. The detections are deduped on the calibrated ground
    # plane, so a second box on the same object is not served and no ghost frame is
    # drawn. A crowd is not collapsed - see drop_duplicate_detections.
    boxes, operator_map = media_tracks.drop_duplicate_detections(boxes, plane)
    operators = [operator_map[i] for i in operators if i in operator_map]

    packet = {
        'frame_width': entry.get('width') or 0,
        'frame_height': entry.get('height') or 0,
        'captured_at': float(index) / (entry.get('fps') or 5),
        'detections': boxes, 'ppe': [], 'tracks': [],
        'forecast_horizon_seconds': None,
        'cycle_latency_ms': calibration.get('inference_ms'),
        'debounced_alarm': 'UNAVAILABLE', 'evaluated_pairs': [], 'hazards': [],
        'frame_index': index,
        'detection_calibration': calibration,
        'ppe': ppe,
    }
    # The recording is tracked once, in order, so every frame carries a real trail
    # from the frames before it. Stepping on demand cannot do that: the tracker
    # caches an empty snapshot for every frame it skips and never rewinds, so
    # asking for an earlier frame returned no agents at all, and the trail and the
    # forecast path existed only for frames the user happened to play in order.
    # Detections come from the same cache the current frame already used.
    # Every frame of the recording is stepped with its OWN detections, including
    # the frames a jump skips over. Filling a skipped frame with nothing cached an
    # empty snapshot for it permanently, so asking for an earlier frame returned no
    # agents at all and the trail and forecast path were missing unless the clip
    # had been played straight through. Detections come from the cache the current
    # frame already used, so this costs no extra inference after prewarm.
    tracker = media_tracks.tracker_for(media_id, width, height, total, mtime_ns,
                                      plane=plane)
    def _frames_at(frame_index):
        # The same two cleanups the current frame gets, or the tracker would carry
        # a different set of objects from one frame to the next.
        frame_boxes = media_detection.detect_for_media(
            media_id, str(path), frame_index, mtime_ns)[0]
        kept, operator_indices = detection_ghosts.suppress_ghosts(frame_boxes)
        kept, index_map = media_tracks.drop_duplicate_detections(kept, plane)
        return kept, [index_map[i] for i in operator_indices if i in index_map]

    source_index = media_detection.source_index_for(media_id, index)
    snapshot = tracker.step(source_index, boxes, float(source_index) / 30.0,
                          operators=frozenset(operators), frames_at=_frames_at)
    # Attach the assigned track id to each detection so a drawn box can be
    # paired with its zone state on the frame.
    ids = snapshot.get('detection_track_ids') or []
    # A detection row is [x1,y1,x2,y2,confidence,class] then, when known, the
    # track id at index 7 and the role at index 8. Appending the track id blindly
    # overwrote the operator marker, so a cab operator reached the panel looking
    # like an ordinary tracked worker.
    # A served detection is [x1,y1,x2,y2,confidence,class,track_id,role]. Both
    # trailing columns are always present so the client's indices cannot shift:
    # a role written positionally had displaced the track id.
    rows = []
    for i, box in enumerate(boxes):
        row = list(box[:6])
        row.append(ids[i] if i < len(ids) else None)
        row.append(detection_ghosts.ROLE_OPERATOR if i in operators else None)
        rows.append(row)
    packet['detections'] = rows
    packet['tracks_3d'] = snapshot['tracks_3d']
    packet['worker_states'] = snapshot['worker_states']
    packet['zone_events'] = snapshot['zone_events']
    packet['zone_exits'] = snapshot['zone_exits']
    packet['ground_plane'] = snapshot['ground_plane']
    packet['zone'] = snapshot['zone']
    packet['business_state'] = 'SAFE' if not boxes else 'ATTENTION'
    packet['camera_id'] = f"RECORDED::{media_id}"
    packet.update(source='demo', scenario_id=ORIGINAL_PREFIX + media_id,
                  frame_id=f'original-{media_id}-{index}',
                  device='Recorded playback', jpeg=base64.b64encode(jpeg).decode('ascii'),
                  provenance=ORIGINAL_PROVENANCE, forecast_status='ILLUSTRATIVE_DEMO',
                  # Some restored stock clips carry a third party's own PPE labels
                  # burned into the pixels. They are part of the media, not this
                  # system's output, and the panel says so.
                  source_annotations=media.baked_in_annotations(media_id, path, total))
    return packet


def live_packet(worker):
    with worker.lock:
        state=worker.snapshot()
        if state['state']!='STREAMING' or not worker.jpeg or not worker.frame_id:
            raise HTTPException(503,'Camera monitoring unavailable')
        data=copy.deepcopy(worker.telemetry)
        data.update(source='live',camera_id=worker.config.camera_id,site_id=worker.config.site_id,
                    device=state['device'],frame_age_seconds=state['frame_age_seconds'],
                    stale_after_seconds=worker.config.stale_after_seconds,
                    jpeg=base64.b64encode(worker.jpeg).decode('ascii'))
    return remember(data)


class Review(BaseModel):
    source: str = Field(pattern=r'^(live|demo)$')
    frame_id: str = Field(max_length=128)
    verdict: str = Field(pattern=r'^(TRUE_POSITIVE|FALSE_POSITIVE|CONTROLLED_WORK)$')


def demo_entry(key, data):
    """Uniform catalog row for curated bundles and restored originals alike."""
    if 'original_media_id' in data:
        # Restored recordings are always presented as 1920x1080 @ 30 fps.
        return {'id': key, 'title': data['title'], 'frames': data['frames'],
                'fps': data.get('fps', 30), 'kind': 'original', 'group': data.get('group', ''),
                'width': data.get('width', 1920), 'height': data.get('height', 1080),
                'source_width': data.get('source_width'), 'source_height': data.get('source_height'),
                'source_fps': data.get('source_fps'), 'normalized': True}
    return {'id': key, 'title': data['title'], 'frames': len(data['frames']),
            'fps': data.get('fps', 5), 'kind': 'curated', 'group': ''}


def router(get_live):
    r=APIRouter(prefix='/api/v1/hub')

    @r.get('/sources')
    def sources():
        service=get_live()
        return {'agent_configured':bool(os.getenv('SENTINEL_LLM_MODEL') and (os.getenv('OPENAI_API_KEY') or os.getenv('ANTHROPIC_API_KEY'))),
                'cameras':service.status() if service else [],
                'demos':[demo_entry(key,data) for key,(_,data) in sorted(demos().items(),key=lambda item:item[0])],
                'original_media':media.summary(),
                'datasets':len(media.datasets())}

    @r.get('/live/{camera_id}/snapshot')
    def snapshot(camera_id:str):
        service=get_live()
        worker=service.workers.get(camera_id) if service else None
        if worker is None:raise HTTPException(404,'Camera not configured')
        return live_packet(worker)

    def demo_frame(scenario:str,index:int, *, allow_curated=True):
        """Resolve a scenario id.

        Restored originals are the listed scenarios and are served from both the
        private and public showcase. Curated bundles are unlisted; they stay
        addressable on the private route only.
        """
        if scenario.startswith(ORIGINAL_PREFIX):
            return remember(original_frame(scenario[len(ORIGINAL_PREFIX):], index))
        item=demos().get(scenario) or (curated_demos().get(scenario) if allow_curated else None)
        if not item:raise HTTPException(404,'Demo not found')
        folder,data=item
        if 'original_media_id' in data:
            return remember(original_frame(data['original_media_id'], index))
        if index<0 or index>=len(data['frames']):raise HTTPException(404,'Frame not found')
        packet=copy.deepcopy(data['frames'][index])
        path=folder/f'{index:04d}.jpg'
        if not path.is_file():raise HTTPException(404,'Demo image unavailable')
        packet.update(source='demo',scenario_id=scenario,frame_id=f'{scenario}-{index}-{(folder / "manifest.json").stat().st_mtime_ns}',
                      device='Recorded playback',jpeg=base64.b64encode(path.read_bytes()).decode('ascii'),
                      provenance=data['provenance'],forecast_status='ILLUSTRATIVE_DEMO')
        return packet if allow_curated else remember(packet)

    @r.get('/demo/{scenario}/frames/{index}')
    def private_demo(scenario:str,index:int):
        return demo_frame(scenario,index)

    @r.post('/reviews')
    def review(req:Review):
        with LOCK:
            record=SNAPSHOTS.get((req.source,req.frame_id))
            if not record or time.monotonic()-record[0]>300:
                raise HTTPException(409,'Review frame expired. Capture a current frame and try again.')
            snapshot=copy.deepcopy(record[1])
        event_id=('DEMO-' if req.source=='demo' else 'LIVE-')+uuid.uuid4().hex
        target=Path('data/demo_reviews' if req.source=='demo' else 'data/adjudications')
        target.mkdir(parents=True,exist_ok=True)
        record={'event_id':event_id,'source':req.source,'verdict':req.verdict,'snapshot':snapshot,'reviewed_at':time.time()}
        tmp=target/f'.{event_id}.tmp'
        tmp.write_text(json.dumps(record,allow_nan=False))
        tmp.replace(target/f'{event_id}.json')
        return {'event_id':event_id,'status':'REVIEW_STORED','frame_id':req.frame_id,'source':req.source}
    public=APIRouter(prefix='/api/v1/showcase')
    @public.get('/sources')
    def public_sources():
        return {'agent_configured':False,'cameras':[],
                'demos':[demo_entry(key,data) for key,(_,data) in sorted(demos().items(),key=lambda item:item[0])],
                'original_media':media.summary(),
                'datasets':len(media.datasets())}
    @public.get('/demo/{scenario}/frames/{index}')
    def public_frame(scenario:str,index:int):
        return demo_frame(scenario,index,allow_curated=False)
    combined=APIRouter()
    combined.include_router(r)
    combined.include_router(public)
    return combined
