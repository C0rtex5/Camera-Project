'use strict';
const el=id=>document.getElementById(id), canvas=el('vision-canvas'),ctx=canvas.getContext('2d');
let epoch=0,source='demo',catalog={cameras:[],demos:[]},index=0,playing=false,speed=1,timer=null,current=null,bitmap=null,deadline=0;
// Measures what the pipeline actually delivers, so a smooth-looking player can
// never hide a server that is falling behind.
const flow={frames:0,since:0};
function resetFlow(){flow.frames=0;flow.since=0;}
function trackFlow(){
 const now=performance.now();
 if(!flow.since){flow.since=now;flow.frames=0;return;}
 flow.frames++;
 const elapsed=now-flow.since;
 if(elapsed>=1000){
  const rate=1000*flow.frames/elapsed,node=el('flow-rate');
  if(node)node.textContent=source==='live'?'':rate.toFixed(0)+' fps served';
  flow.frames=0;flow.since=now;
 }
}

const STATE_TAG_COLORS={'IN ZONE':'#f59e0b','IN ZONE \u00b7 MOVING':'#ef4444','MOVING':'#60a5fa','STATIONARY':'#9ca3af'};
function renderEventLog(packet){
 const node=el('event-log');if(!node)return;
 const events=packet.zone_events||[];
 if(!events.length){node.textContent='';return;}
 // One entry per line. Joining four entries into a single line overflowed the
 // strip and the browser clipped the start, so the oldest events were unreadable.
 if(node.style)node.style.whiteSpace='pre-line';
 node.textContent=events.slice(-4).reverse()
  .map(e=>e.timestamp.toFixed(1)+'s  '+e.event_type+'  worker '+e.track_id
          +(e.dwell_seconds>0?'  ('+e.dwell_seconds.toFixed(1)+'s)':'')
          +(e.reason?'  '+e.reason:''))
  .join('\n');
}
function updateRiskBadges(packet,demo){
 const state=demo?(packet.business_state||'SAFE'):'UNAVAILABLE';
 const badge=el('risk-state'),stamp=el('frame-timestamp');
 if(badge){badge.textContent=state;badge.hidden=false;
  badge.className='badge '+(state==='RISK'?'badge-danger':state==='ATTENTION'?'badge-warn':state==='SAFE'?'badge-ok':'');}
 if(stamp){
  // The card requires a timestamp on the alert. For recorded playback this is
  // the position in the recording, which is what a viewer can act on.
  const seconds=Number(packet.captured_at)||0;
  stamp.textContent=packet.timestamp_label||(seconds?('T+'+seconds.toFixed(2)+'s'):'');
 }
}

const twin=createTwin();
async function api(path,options={}){
 const response=await fetch(path,{...options,headers:{'Content-Type':'application/json'},cache:'no-store',signal:AbortSignal.timeout(options.timeout||5000)});

 if(!response.ok){const value=await response.json().catch(()=>({}));throw Error(typeof value.detail==='string'?value.detail:'Service unavailable ('+response.status+')');}
 return response.json();
}
function clear(message){current=null;deadline=0;if(bitmap){bitmap.close();bitmap=null;}ctx.clearRect(0,0,canvas.width,canvas.height);twin.clear();el('empty-message').textContent=message;el('empty-message').hidden=false;el('system-alert-badge').textContent='UNAVAILABLE';el('system-alert-badge').className='badge';for(const id of ['metric-ttc','metric-pcol','metric-latency'])el(id).textContent='—';el('gnn-status').textContent='Unavailable';el('forecast-label').textContent='Forecast unavailable';el('gnn-verdict').textContent='No current observations';el('comparison-context').textContent='No current observations';el('live-age').textContent='Unavailable';el('cam-telemetry-tag').textContent='No observations';document.querySelectorAll('.review').forEach(b=>b.disabled=true);}
function resize(){canvas.width=Math.max(1,canvas.parentElement.clientWidth);canvas.height=Math.max(1,canvas.parentElement.clientHeight);if(current&&bitmap)draw(current,bitmap);twin.resize();}
window.addEventListener('resize',resize);
function draw(packet,image){
 ctx.clearRect(0,0,canvas.width,canvas.height);
 const scale=Math.min(canvas.width/packet.frame_width,canvas.height/packet.frame_height),x=(canvas.width-packet.frame_width*scale)/2,y=(canvas.height-packet.frame_height*scale)/2;
 ctx.drawImage(image,x,y,packet.frame_width*scale,packet.frame_height*scale);
 const PROXIMITY_COLORS={DANGER:'#ef4444',NEAR:'#f59e0b',SAFE:'#9ca3af',NO_MACHINE:'#6b7280'};
 const NEUTRAL='#9ca3af';
 // A worker can sit entirely inside a machinery box - the cab operator is a
 // real person, not part of the machine. Nesting is detected so that both boxes
 // and both sets of notes stay legible, never so that one can be dropped.
 // Overlay boxes are laid out in free space rather than at a fixed offset, so
 // two labels, two note chips, or a label and a chip can never overprint. Each
 // drawn panel claims a rectangle and the next one is stepped until it finds a
 // free slot that also stays inside the canvas.
 function makePacker(bounds){
  const taken=[];
  const free=(r)=>!taken.some(t=>r.x<t.x+t.w&&r.x+r.w>t.x&&r.y<t.y+t.h&&r.y+r.h>t.y);
  const clampX=(x,w)=>Math.max(bounds.x,Math.min(x,bounds.x+bounds.w-w));
  return function place(w,h,preferred,step){
   const fits=(y)=>{
    const r={x:clampX(preferred.x,w),y,w,h};
    if(r.y<bounds.y||r.y+h>bounds.y+bounds.h)return null;
    return free(r)?r:null;
   };
   // Walk away from the preferred slot in both directions, near steps first, so
   // an annotation ends up beside its box rather than at the far end of the
   // canvas. A single-direction scan gives up on crowded frames.
   const room=bounds.h-h, stride=Math.max(1,step||4);
   const offsets=[0];
   for(let k=stride;k<=room;k+=stride)offsets.push(k,-k);
   for(const offset of offsets){
    const found=fits(preferred.y+offset);
    if(found){taken.push(found);return found;}
   }
   // Still crowded: sweep a coarse grid in both axes, so a frame carrying many
   // workers still finds a slot instead of stacking panels on top of each other.
   const stepY=Math.max(8,Math.floor(h/2)),stepX=Math.max(8,Math.floor(w/2));
   for(let gy=bounds.y;gy<=bounds.y+bounds.h-h;gy+=stepY){
    for(let gx=clampX(preferred.x,w);gx>=bounds.x;gx-=stepX){
     const r={x:gx,y:gy,w,h};
     if(free(r)){taken.push(r);return r;}
    }
    for(let gx=clampX(preferred.x,w)+stepX;gx<=bounds.x+bounds.w-w;gx+=stepX){
     const r={x:gx,y:gy,w,h};
     if(free(r)){taken.push(r);return r;}
    }
   }
   const fallback={x:clampX(preferred.x,w),y:bounds.y,w,h};
   taken.push(fallback);return fallback;
  };
 }
 function nesting(detections,d){
  const area=Math.max(0,d[2]-d[0])*Math.max(0,d[3]-d[1]);if(!area)return null;
  for(const other of detections){
   if(other===d||other[5]<2)continue;
   const w=Math.min(d[2],other[2])-Math.max(d[0],other[0]),h=Math.min(d[3],other[3])-Math.max(d[1],other[1]);
   if(w<=0||h<=0)continue;
   if(w*h/area>0.6)return other;
  }
  return null;
 }
 // The packer covers the drawn image area, not the whole canvas: overlay text
 // placed in the letterbox would be unreadable anyway.
 const place=makePacker({x:0,y:0,w:canvas.width,h:canvas.height});
 (packet.detections||[]).forEach(d=>{
  const [x1,y1,x2,y2,confidence,cls]=d,worker=cls<2,color=worker?'#3b82f6':'#f59e0b';
  const left=x+x1*scale,top=y+y1*scale,w=(x2-x1)*scale,h=(y2-y1)*scale;
  const host=nesting(packet.detections,d);
  ctx.strokeStyle=color;
  // A worker inside a machinery box is drawn thinner but still in full, so the
  // person is never lost behind the machine.
  ctx.lineWidth=host?2:3;ctx.setLineDash(host?[6,4]:[]);
  ctx.strokeRect(left,top,w,h);ctx.setLineDash([]);
  ctx.lineWidth=4;ctx.beginPath();
  ctx.moveTo(left,top+12);ctx.lineTo(left,top);ctx.lineTo(left+12,top);
  ctx.moveTo(left+w-12,top+h);ctx.lineTo(left+w,top+h);ctx.lineTo(left+w,top+h-12);
  ctx.stroke();
  ctx.font='bold 11px monospace';
  const caption=(worker?'WORKER':cls===2?'HEAVY EQUIPMENT':'LIGHT VEHICLE')+' ['+Math.round(confidence*100)+'%]';
  const barW=Math.min(200,canvas.width);
  const bar=place(barW,20,{x:left,y:Math.max(0,top-21)},21);
  ctx.fillStyle=color;ctx.fillRect(bar.x,bar.y,barW,20);
  ctx.fillStyle='white';
  ctx.fillText(caption,bar.x+4,bar.y+14);
  if(!worker)return;
  const state=(packet.worker_states||[]).find(v=>v.track_id===d[6]);
  const ppe=(packet.ppe||[]).find(p=>p.bbox?.every((v,i)=>Math.abs(v-d[i])<1));
  const notes=[];
  // The original repository's PPE notes, including its wording and colours.
  // UNKNOWN when the worker was too small or blurred to judge, so a hardhat that
  // was never measured is never reported as missing.
  if(ppe){
   notes.push({text:ppe.measured?('HARDHAT: '+(ppe.hardhat?'OK':'NO HARDHAT')):'HARDHAT: UNKNOWN',
               color:ppe.measured?(ppe.hardhat?'#10b981':'#ef4444'):NEUTRAL});
   notes.push({text:ppe.measured?('VEST: '+(ppe.vest?'OK':'NO VEST')):'VEST: UNKNOWN',
               color:ppe.measured?(ppe.vest?'#10b981':'#ef4444'):NEUTRAL});
  }
  if(state&&state.proximity_state){
   const metres=state.nearest_machinery_metres;
   notes.push({text:state.proximity_state==='NO_MACHINE'?'NO MACHINERY IN FRAME'
    :state.proximity_state+' · '+(metres==null?'—':metres.toFixed(1)+' m'),
    color:PROXIMITY_COLORS[state.proximity_state]||NEUTRAL});
  }
  if(state)notes.push({text:state.label,color:STATE_TAG_COLORS[state.label]||NEUTRAL});
  if(!notes.length)return;
  // A nested worker's notes move clear of the host box so the two never overprint.
  // A nested worker's notes start clear of the host box, then the packer steps
  // them until they find free space that also fits inside the canvas.
  const width=Math.min(184,canvas.width);
  const chipH=19*notes.length;
  const chip=place(width,chipH,
    {x:host?left+Math.min(w,width+8):left,
     y:host?(top-2-chipH>y?top-2-chipH:top+h+2):top+h+2},
    chipH+2);
  ctx.fillStyle='#101215df';ctx.fillRect(chip.x,chip.y,width,chipH);
  ctx.font='10px monospace';
  notes.forEach((note,i)=>{ctx.fillStyle=note.color;ctx.fillText(note.text,chip.x+4,chip.y+14+19*i);});
 });
}
function display(packet,image,expires){
 if(bitmap)bitmap.close();bitmap=image;current=packet;deadline=expires;el('empty-message').hidden=true;draw(packet,image);twin.update(packet);
 const demo=packet.source==='demo',risk=HubCore.risk(packet),alarm=packet.debounced_alarm||'UNAVAILABLE';
 const restored=String(packet.scenario_id||'').startsWith('original:');
 el('device').textContent=packet.device;el('source-label').textContent=demo?(restored?'ORIGINAL RECORDING · NOT LIVE':'RECORDED DEMO · NOT LIVE'):'LIVE CAMERA';el('cam-telemetry-tag').textContent=packet.frame_width+'×'+packet.frame_height+(demo?' · Recorded':' · Live');
 // Some restored stock clips ship with a third party's own PPE labels burned
 // into the pixels - flat salmon boxes reading "helmet 0.89". They are part of
 // the footage and not this system's output, so the panel says so rather than
 // letting them be mistaken for our detections.
 const note=el('source-note');
 note.textContent=packet.source_annotations
  ?'Source recording already contains third-party detection labels. They are not this system\'s output.'
  :'';
 note.hidden=!packet.source_annotations;
 el('forecast-label').textContent=demo?'Illustrative linear paths':packet.forecast_status==='AVAILABLE'?'GATv2 ST-GNN · '+packet.forecast_horizon_seconds.toFixed(1)+' s':'Forecast unavailable';
 el('metric-ttc').textContent=risk.ttc==null?(demo?'Not measured':'No predicted breach'):risk.ttc.toFixed(2)+' s';el('metric-pcol').textContent=risk.probability==null?'Not measured':(risk.probability*100).toFixed(1)+' %';
 el('metric-latency').textContent=Number.isFinite(packet.cycle_latency_ms)?packet.cycle_latency_ms.toFixed(1)+' ms':'Not measured';
 el('system-alert-badge').textContent=demo?'DEMO PLAYBACK':alarm==='NORMAL_LEVEL_0'?'NO CURRENT ALERT':alarm.replaceAll('_',' ');
 el('system-alert-badge').className='badge '+(demo?'':alarm.includes('CRITICAL')?'badge-alert-critical':alarm.includes('WARNING')?'badge-alert-warning':alarm.includes('ADVISORY')?'badge-alert-advisory':'badge-alert-normal');
 updateRiskBadges(packet,demo);
 renderEventLog(packet);
 el('gnn-status').textContent=demo?'Not evaluated':packet.forecast_status==='AVAILABLE'?alarm:'Unavailable';el('gnn-verdict').textContent=demo?'Illustrative playback; no model validation':'Current camera model output';el('comparison-context').textContent=demo?'RECORDED DEMONSTRATION':'CURRENT LIVE FRAME';el('provenance').textContent=demo?packet.provenance:'Mode mass is not a calibrated incident probability. Lead time and false-alarm rates require site validation.';
 document.querySelectorAll('.review').forEach(b=>b.disabled=false);
 if(demo){el('frame-counter').textContent=(index+1)+' / '+selectedDemo().frames;el('scrubber').value=index;}
}
function selectedDemo(){return catalog.demos.find(d=>d.id===el('scenario').value);}
function refreshFormatTag(){const demo=selectedDemo();const tag=el('format-tag');
 if(!tag)return;
 if(source==='live'||!demo){tag.textContent='LIVE';tag.className='badge';return;}
 tag.textContent=(demo.fps||30)+' fps · '+(demo.width||1920)+'×'+(demo.height||1080);
 tag.className='badge badge-gpu';}
async function loadFrame(session=epoch){
 const started=performance.now();
 try{
  if(document.hidden)return;
  const demo=selectedDemo();const camera=catalog.cameras[0];
  if(source==='demo'&&!demo){clear('No demo assets found. Add a curated recording bundle.');return;}
  if(source==='live'&&!camera){clear('No live camera configured. Open Camera setup; demo playback remains available.');return;}
  const path=source==='live'?'/live/'+encodeURIComponent(camera.camera_id)+'/snapshot':'/demo/'+encodeURIComponent(demo.id)+'/frames/'+index;
  const packet=await api('/api/v1/hub'+path);const expires=HubCore.expires(packet,started);
  if(!HubCore.valid(packet,session,epoch,expires,performance.now())){if(session===epoch)clear('Camera frame is stale — monitoring unavailable');return;}
  const bytes=Uint8Array.from(atob(packet.jpeg),c=>c.charCodeAt(0));const image=await createImageBitmap(new Blob([bytes],{type:'image/jpeg'}));
  if(!HubCore.valid(packet,session,epoch,expires,performance.now())){image.close();if(session===epoch)clear('Camera frame is stale — monitoring unavailable');return;}
  display(packet,image,expires);
  trackFlow();
 }catch(error){if(session===epoch){clear(error.message);}}
 finally{if(session===epoch&&(source==='live'||playing)){const rate=source==='live'?5:(selectedDemo()?.fps||5)*speed;timer=setTimeout(()=>{if(source==='demo'&&playing&&!document.hidden)index=(index+1)%selectedDemo().frames;loadFrame(session);},Math.max(0,1000/rate-(performance.now()-started)));}}
}
function switchSource(){epoch++;clearTimeout(timer);resetFlow();source=el('source').value;playing=false;index=0;el('btn-play').textContent='Play';el('playback').hidden=source==='live';el('live-strip').hidden=source!=='live';el('scenario').hidden=source==='live';el('scene-title').textContent=source==='demo'?(selectedDemo()?.title||'Recorded demonstration'):catalog.cameras[0]?.camera_id||'Live camera';el('cam-title-tag').textContent=source==='demo'?'Recorded construction site footage':'Live security camera';refreshFormatTag();el('review-status').textContent='Reviews reference the displayed frame.';clear('Loading '+source+' source…');el('scrubber').max=(selectedDemo()?.frames||1)-1;loadFrame(epoch);}
async function refresh(initial=false,preferDemo=false){catalog=await api('/api/v1/hub/sources');const previous=el('scenario').value;el('scenario').replaceChildren();for(const item of catalog.demos){const option=document.createElement('option');option.value=item.id;option.textContent=item.title;if(item.kind==='original')option.dataset.kind='original';el('scenario').appendChild(option);}if(catalog.demos.some(d=>d.id===previous))el('scenario').value=previous;if(initial)el('source').value=preferDemo?'demo':HubCore.initial(catalog);switchSource();}
el('source').onchange=switchSource;el('scenario').onchange=switchSource;el('rescan').onclick=()=>refresh().catch(e=>clear(e.message));
function playPause(){if(source!=='demo')return;playing=!playing;el('btn-play').textContent=playing?'Pause':'Play';epoch++;clearTimeout(timer);if(playing)loadFrame(epoch);}
function step(delta){if(source!=='demo'||!selectedDemo())return;playing=false;el('btn-play').textContent='Play';epoch++;clearTimeout(timer);index=Math.max(0,Math.min(selectedDemo().frames-1,index+delta));loadFrame(epoch);}
el('btn-play').onclick=playPause;el('step-back').onclick=()=>step(-1);el('step-forward').onclick=()=>step(1);el('speed').onchange=()=>{speed=Number(el('speed').value);};el('scrubber').oninput=()=>{const target=Number(el('scrubber').value);step(target-index);};
async function review(verdict){if(!current)return;const captured={source:current.source,frame_id:current.frame_id,verdict};el('review-status').textContent='Saving review of '+captured.frame_id+'…';try{const r=await api('/api/v1/hub/reviews',{method:'POST',body:JSON.stringify(captured)});el('review-status').textContent=(r.source==='demo'?'Demo review':'Live review')+' saved for frame '+r.frame_id;}catch(e){el('review-status').textContent=e.message;}}
document.querySelectorAll('.review').forEach(b=>b.onclick=()=>review(b.dataset.verdict));
el('supervisor').onclick=()=>{el('agent').hidden=false;el('agent-context').textContent=source==='demo'?'Demo playback is isolated. Select Live camera for site supervision.':catalog.agent_configured?'Agent provider configured. Review recommendations against site conditions.':'Agent provider is not configured. Live camera monitoring remains available.';el('ask').disabled=source==='demo'||!catalog.agent_configured;};el('close-agent').onclick=()=>{el('agent').hidden=true;};el('ask').onclick=async()=>{if(source!=='live')return;el('agent-answer').textContent='Working…';try{const r=await api('/api/v1/agent/supervisor/chat',{method:'POST',timeout:60000,body:JSON.stringify({message:el('agent-question').value,site_id:current?.site_id||'SITE-01'})});el('agent-answer').textContent=r.response;}catch(e){el('agent-answer').textContent=e.message;}};
window.addEventListener('keydown',e=>{if(['INPUT','TEXTAREA','SELECT','BUTTON'].includes(document.activeElement?.tagName)||!el('agent').hidden)return;if(e.key===' '){e.preventDefault();playPause();}if(e.key==='.')step(1);if(e.key===',')step(-1);const verdict={t:'TRUE_POSITIVE',f:'FALSE_POSITIVE',c:'CONTROLLED_WORK'}[e.key.toLowerCase()];if(verdict)review(verdict);});
function freshness(){if(source==='live'&&current){const remaining=deadline-performance.now();if(remaining<=0)clear('Connection stale — monitoring unavailable');else el('live-age').textContent='Frame age '+Math.max(0,current.stale_after_seconds-remaining/1000).toFixed(1)+' s';}}
setInterval(freshness,100);document.addEventListener('visibilitychange',()=>{freshness();if(!document.hidden){resize();if(source==='demo'&&!playing)loadFrame(epoch);}});resize();clear('Loading recorded demo…');window.addEventListener('DOMContentLoaded',()=>refresh(true,true).catch(e=>clear(e.message)));

function createTwin(){
 const container=el('webgl-wrapper');let renderer,scene,camera,grid;const view={target:{x:0,y:4},radius:42,azimuth:-Math.PI/2,elevation:.85,last:0,drag:null};let ringUntil=0;const agentMeshes={};const trailLines=new Map();let conflictRing,trenchMesh;
 // Pose easing: how far a polygon may turn in one frame, and how much of
// the remaining gap it closes. Caps the snap without freezing direction.
 const POSE_MAX_STEP=0.18,POSE_EASE=0.18;
 // Framing: the camera used to sit a fixed 42 m back while the workers occupy
 // about 8 m, so most of the view was empty grid and the agents were specks in
 // the middle. The view now frames what is actually on the ground plane. The
 // centre eases so it does not jump, and a wheel-zoom by the user is respected
 // from then on.
 const FRAME_MIN_RADIUS=16,FRAME_PADDING=2.2,FRAME_EASE=0.08;
 let framedByUser=false;
 function dispose(object){object.traverse?.(part=>{part.geometry?.dispose();if(Array.isArray(part.material))part.material.forEach(m=>m.dispose());else part.material?.dispose();});}
 function clear(){if(!scene)return;for(const line of trailLines.values()){scene.remove(line);dispose(line);}trailLines.clear();for(const id of Object.keys(agentMeshes)){const m=agentMeshes[id];scene.remove(m);dispose(m);delete agentMeshes[id];}if(conflictRing)conflictRing.material.opacity=0;ringUntil=0;}
  function getOrCreateAgentMesh(id,className){
   if(agentMeshes[id])return agentMeshes[id];
   const grp=new THREE.Group();
   if(className==='WORKER'){
    // A straight-edged footprint, not a circle: the 3D equivalent of the
    // worker's 2D detection frame, sized from that frame's measured width on the
    // ground plane and turned to the direction the worker is facing.
    const foot=new THREE.Mesh(new THREE.PlaneGeometry(1,1),new THREE.MeshBasicMaterial({color:0x3b82f6,side:THREE.DoubleSide}));
    foot.position.z=0.02;grp.add(foot);grp.userData.footprint=foot;
    // The original post: the direction indicator.
    const cyl=new THREE.Mesh(new THREE.CylinderGeometry(0.3,0.3,1.8,16),new THREE.MeshBasicMaterial({color:0x60a5fa}));
    // The original stood this post upright. A cylinder's axis is Y and the
    // scene's up is Z, so without this rotation a 1.8 m post lies flat on the
    // ground as a long bar with rounded ends. Restored with the original
    // half-height offset.
    cyl.rotation.x=Math.PI/2;cyl.position.z=0.9;grp.add(cyl);
    grp.userData.body=cyl;grp.userData.pose='heading';
   }else{
    // The original hull and cab, scaled to the measured footprint rather than a
    // fixed 2.6 x 5.0 m. A unit box keeps the original straight edges and the
    // original 2.6:5.0:1.8 proportion while the size comes from the detection.
    // See-through so a worker beside the machine stays visible.
    const body=new THREE.Mesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshBasicMaterial({color:0xf59e0b,transparent:true,opacity:0.55,depthWrite:false}));
    body.position.z=0.9;grp.add(body);grp.userData.hull=body;
    const cab=new THREE.Mesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshBasicMaterial({color:0x2563eb,transparent:true,opacity:0.7,depthWrite:false}));
    cab.position.set(0,0.5,2.2);grp.add(cab);grp.userData.cab=cab;
    grp.userData.body=body;grp.userData.pose='group';
   }
   grp.renderOrder=className==='WORKER'?2:1;
   scene.add(grp);agentMeshes[id]=grp;return grp;
  }
  function resize(){if(!renderer)return;const w=Math.max(1,container.clientWidth),h=Math.max(1,container.clientHeight);renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();}
 try{
  if(typeof THREE==='undefined')throw Error();
  scene=new THREE.Scene();
  // One trail per track. A single shared line was overwritten by whichever
  // agent was drawn last, so only one person's path was ever visible.

  conflictRing=new THREE.Mesh(new THREE.RingGeometry(1.0,1.4,32),new THREE.MeshBasicMaterial({color:0xef4444,side:THREE.DoubleSide,transparent:true,opacity:0}));scene.background=new THREE.Color(0x0a0c0e);camera=new THREE.PerspectiveCamera(45,1,.1,2000);camera.up.set(0,0,1);renderer=new THREE.WebGLRenderer({antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));container.appendChild(renderer.domElement);grid=new THREE.GridHelper(50,50,0x282f37,0x161a1e);grid.rotation.x=Math.PI/2;scene.add(grid);
  // The original 20x7 trench shape. Its position and extent are applied per
  // frame from packet.zone below, so the polygon drawn here and the one the
  // occupancy test uses are always the same rectangle.
  // Declared outside this block: update() positions it from packet.zone, and a
  // const inside the try would be invisible there, throwing on every frame.
  trenchMesh=new THREE.Mesh(new THREE.PlaneGeometry(20,7),new THREE.MeshBasicMaterial({color:0xef4444,transparent:true,opacity:0.15,side:THREE.DoubleSide}));
  trenchMesh.position.set(2,6.5,0.05);scene.add(trenchMesh);
  scene.add(conflictRing);resize();
  renderer.domElement.addEventListener('webglcontextlost',event=>{event.preventDefault();container.setAttribute('data-unavailable','3D view unavailable. Camera monitoring continues.');});
  container.onpointerdown=e=>{drag=[e.clientX,e.clientY];container.setPointerCapture(e.pointerId);};container.onpointerup=()=>drag=null;container.onpointermove=e=>{if(!drag)return;view.azimuth-=(e.clientX-drag[0])*.008;view.elevation=Math.max(.15,Math.min(1.5,view.elevation+(e.clientY-drag[1])*.005));drag=[e.clientX,e.clientY];};container.addEventListener('wheel',e=>{e.preventDefault();view.radius=Math.max(FRAME_MIN_RADIUS,Math.min(150,view.radius+e.deltaY*.03));framedByUser=true;},{passive:false});
  function render(t){requestAnimationFrame(render);if(document.hidden||t-view.last<1000/30)return;view.last=t;camera.position.set(view.target.x+view.radius*Math.cos(view.elevation)*Math.cos(view.azimuth),view.target.y+view.radius*Math.cos(view.elevation)*Math.sin(view.azimuth),view.radius*Math.sin(view.elevation));camera.lookAt(view.target.x,view.target.y,0);renderer.render(scene,camera);}requestAnimationFrame(render);
 }catch(e){container.textContent='3D view unavailable in this browser. Camera monitoring and reviews remain available.';return {update(){},clear(){},resize(){}};}
 function update(packet){
   const tracks=packet.tracks_3d||[],currentIds=new Set();
   // Frame the ground the agents are actually on. Without this the view was a
   // fixed 42 m back and 50 m of grid, with the whole action a small cluster in
   // the middle of it - the scene read as clutter around a few specks.
   if(tracks.length){
    let fx0=Infinity,fx1=-Infinity,fy0=Infinity,fy1=-Infinity;
    for(const t of tracks){
     // Each object occupies its own footprint, so the view has to allow for the
     // size of the shapes and not only for the spread of their centres.
     const f=t.footprint||{},hw=(Number(f.width_m)||0)/2,hd=(Number(f.depth_m)||0)/2;
     const r=Math.hypot(hw,hd);
     fx0=Math.min(fx0,t.position[0]-r);fx1=Math.max(fx1,t.position[0]+r);
     fy0=Math.min(fy0,t.position[1]-r);fy1=Math.max(fy1,t.position[1]+r);}
    const cx=(fx0+fx1)/2,cy=(fy0+fy1)/2;
    // Half the extent of everything that has to be visible, shapes included.
    const far=Math.max(Math.hypot(fx1-fx0,fy1-fy0)/2,1e-3);
    if(!framedByUser){
     const want=Math.max(FRAME_MIN_RADIUS,far*FRAME_PADDING);
     view.radius+=(want-view.radius)*FRAME_EASE;              // ease, so the view does not pump
    }
    view.target.x+=(cx-view.target.x)*FRAME_EASE;
    view.target.y+=(cy-view.target.y)*FRAME_EASE;
   }
  for(const trk of tracks){
   currentIds.add(trk.track_id);
   const mesh=getOrCreateAgentMesh(trk.track_id,trk.class_name);
   mesh.position.set(trk.position[0],trk.position[1],0);
   // Orientation follows the measured heading only while the agent is moving.
   // A stationary agent's heading is derived from pixel noise at the footpoint,
   // which on a parked excavator swung the hull through 180 degrees. Holding
   // the last good rotation keeps parked plant stable. The measured heading is
   // still carried on the packet and is not altered.
  // The whole group turns, so the footprint polygon and the direction post share
  // one angle: the polygon lies along the way the agent is walking.
  //
  // The heading is eased and the turn capped. A single bad frame - a
  // mis-association, or a footpoint that lands across the body - swings the
  // measured heading by up to 175 degrees, and the polygons visibly snapped about
  // like scattered plates. The reading stays faithful; the picture stays legible.
  // The measured heading itself is untouched on the packet.
  const target=(Number(trk.heading)||0)-Math.PI/2;
  if(!mesh.userData.posed){
   // First sight: one stable angle rather than whatever this frame produced.
   mesh.userData.poseHeading=target;mesh.userData.posed=true;
  }else if(trk.moving){
   // Only while the agent is actually moving does the pose follow the heading.
   // A stationary agent's heading is footpoint noise, so it holds its angle.
   let delta=target-mesh.userData.poseHeading;
   delta-=Math.round(delta/(Math.PI*2))*(Math.PI*2);        // the short way round
   mesh.userData.poseHeading+=Math.max(-POSE_MAX_STEP,Math.min(POSE_MAX_STEP,delta*POSE_EASE));
  }
  mesh.rotation.z=mesh.userData.poseHeading;
   // Size the polygon from the measured footprint on the packet: the 3D shape is
   // the object that was actually detected, not a fixed size.
   const fp=trk.footprint;
   if(fp&&fp.width_m>0&&fp.depth_m>0){
    if(mesh.userData.footprint){
     mesh.userData.footprint.scale.set(fp.width_m,fp.depth_m,1);
     // The post is the direction indicator the original drew. Its diameter
     // follows the measured width, so a small person does not carry a post wider
     // than they are; the 1.8 m height is the original's.
     if(mesh.userData.body&&mesh.userData.body.scale)
      mesh.userData.body.scale.set(fp.width_m/0.6,1,fp.width_m/0.6);
    }else if(mesh.userData.hull){
     const height=1.8*(fp.width_m/2.6);
     mesh.userData.hull.scale.set(fp.width_m,fp.depth_m,height);
     if(mesh.userData.cab)mesh.userData.cab.scale.set(fp.width_m*0.69,fp.depth_m*0.4,height*1.11);
    }
   }
   if(mesh.userData.hull){
    // Push the hull forward by half its 5.0 m length so it occupies the ground
    // behind the machine rather than the ground it stands on. The hull's long
    // axis is the group's local +Y, which already points along the heading, so
    // this is a local offset and stays correct at any rotation.
    // Half the measured hull length, so the hull sits behind the machine rather
    // than on the ground its footpoint stands on, at any machine size.
    const half=(mesh.userData.hull.scale.y||1)/2;
    mesh.userData.hull.position.set(0,half,mesh.userData.hull.scale.z/2);
   }
      // Two separate lines per agent, as the original twin drew them: the trail
      // behind (history, grey) and the path ahead (forecast, worker cyan or
      // machinery amber). Each track owns its own pair, so no agent overwrites
      // another's path. Lifted to z=0.15 so neither is hidden by the trench
      // plane at 0.05 or by the footprint at 0.02.
      for(const kind of ['trail','path']){
       const points=kind==='trail'?trk.history:trk.forecast_trajectory;
       const key=trk.track_id+':'+kind;
       let line=trailLines.get(key);
       if(!line){
        line=new THREE.Line(new THREE.BufferGeometry(),new THREE.LineBasicMaterial(
         {color:kind==='trail'?0x64748b:(trk.class_name==='WORKER'?0x38bdf8:0xfbbf24)}));
        line.renderOrder=2;scene.add(line);trailLines.set(key,line);
       }
       line.visible=!!(points&&points.length>1);
       if(line.visible)line.geometry.setFromPoints(points.map(p=>new THREE.Vector3(p[0],p[1],0.15)));
      }
   }
  for(const id of Object.keys(agentMeshes)){
   if(!currentIds.has(parseInt(id)))agentMeshes[id].position.set(999,999,999);
  }
  // The drawn trench is the calibrated zone rectangle, taken from the same
  // packet field the occupancy test reads, so the two can never disagree.
  const zone=packet.zone;
  if(zone&&Number.isFinite(zone.min_x)&&Number.isFinite(zone.max_y)){
   trenchMesh.position.set((zone.min_x+zone.max_x)/2,(zone.min_y+zone.max_y)/2,0.05);
   trenchMesh.scale.set((zone.max_x-zone.min_x)/20,(zone.max_y-zone.min_y)/7,1);
  }
  // Exit feedback reuses the original conflict ring: an agent leaving the zone
  // pulses it at the point it left, then it fades again.
  const exit=(packet.zone_exits||[])[0];
  if(exit){conflictRing.position.set(exit.position[0],exit.position[1],0.1);conflictRing.material.opacity=0.9;ringUntil=performance.now()+1500;}
  else if(ringUntil&&performance.now()>ringUntil){conflictRing.material.opacity=0;ringUntil=0;}
  else if(!exit&&!ringUntil)conflictRing.material.opacity=0;
 }
  return {update,clear,resize};
}
