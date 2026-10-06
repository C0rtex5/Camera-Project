'use strict';
/* Browser checks after the 3D canvas was restored to the original application.
   The zone overlay, the fabricated machine box and the state-driven colouring are
   gone; the original geometry, legend and the worker state tag are in their
   place. */
const test=require('node:test');
const assert=require('node:assert');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const ROOT=path.resolve(__dirname,'..');
const SOURCE=fs.readFileSync(path.join(ROOT,'src','dashboard','assets','hub.js'),'utf8');
const HTML=fs.readFileSync(path.join(ROOT,'src','dashboard','index.html'),'utf8');

const PACKET={
  frame_width:1920,frame_height:1080,captured_at:2.0,business_state:'ATTENTION',
  detections:[[300,662,491,1073,0.88,0.0,4]],
  tracks_3d:[
    {track_id:4,class_name:'WORKER',position:[-0.56,1.41],velocity:[0.3,0.1],heading:0.32,
     history:[[-0.6,1.4],[-0.58,1.41],[-0.56,1.41]],forecast_trajectory:[],visible:true},
    {track_id:1,class_name:'HEAVY_EQUIPMENT',position:[-1.5,30.0],velocity:[0,0],heading:0,
     history:[[-1.5,30.0]],forecast_trajectory:[],visible:true},
  ],
  worker_states:[{track_id:4,label:'MOVING',in_zone:false,moving:true,speed_mps:0.52}],
  zone_events:[{event_type:'ZONE_ENTRY',track_id:4,timestamp:1.2,dwell_seconds:0},
               {event_type:'ZONE_EXIT',track_id:4,timestamp:2.4,dwell_seconds:1.2}],
  zone_exits:[{event_type:'ZONE_EXIT',track_id:4,position:[1.0,2.0],dwell_seconds:1.2}],
  ppe:[],tracks:[],hazards:[],evaluated_pairs:[],
};

function makeSandbox(){
  const elements={};
  const calls=[];
  const el=id=>elements[id]||(elements[id]={
    id,textContent:'',value:'',hidden:false,className:'',
    addEventListener(){},appendChild(c){return c;}});
  const context2d=new Proxy({measureText:t=>({width:String(t).length*7})},{
    get(target,key){
      if(typeof key==='symbol')return undefined;
      if(key==='canvas')return {width:1920,height:1080};
      if(key in target)return target[key];
      return (...args)=>{calls.push([key,...args]);};
    },
    set(){return true;},
  });
  const canvas={id:'vision-canvas',width:1920,height:1080,style:{},
                parentElement:{clientWidth:1920,clientHeight:1080},getContext:()=>context2d};
  elements['vision-canvas']=canvas;
  elements['webgl-wrapper']={appendChild(){},clientWidth:800,clientHeight:400};
  const world={
    el,ctx:context2d,THREE:undefined,
    document:{getElementById:el,createElement:()=>({getContext:()=>({}),width:0,height:0}),
              addEventListener(){},querySelector:()=>null,querySelectorAll:()=>[]},
    window:{addEventListener(){},devicePixelRatio:1,location:{origin:'http://x'}},
    fetch:async()=>({ok:true,json:async()=>({demos:[],cameras:[]})}),
    Image:function(){},btoa:s=>s,atob:s=>s,
    console:{log(){},warn(){},error(){}},
    setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},
    performance:{now:()=>0},Date,Math,JSON,Uint8Array,Float32Array,ArrayBuffer,
    TextDecoder,Request,Response,Headers,AbortController,
    location:{origin:'http://x'},HubCore:{ppe:()=>'n/a',risk:()=>null},
  };
  world.globalThis=world;
  vm.createContext(world);
  // The panel bootstraps on load and needs a real DOM; the helper functions
  // under test are declarations, so they are hoisted and remain callable
  // even when the bootstrap throws.
  try{vm.runInContext(SOURCE,world);}catch(error){/* bootstrap only */}
  return {elements,el,world,run:expr=>vm.runInContext(expr,world)};
}

function makeElements(){
  const elements={};
  const el=id=>elements[id]||(elements[id]={id,textContent:'',value:'',hidden:false,className:'',style:{}});
  return {elements,el};
}

function extract(name){
  const start=SOURCE.indexOf(`function ${name}`);
  assert.ok(start>0,`${name} not found in hub.js`);
  const end=SOURCE.indexOf('\n}',start)+2;
  return SOURCE.slice(start,end);
}

test('the panel no longer has the zone or machine badges',()=>{
  assert.ok(!HTML.includes('id="zone-tag"'),'#zone-tag must be gone');
  assert.ok(!HTML.includes('id="machine-id"'),'#machine-id must be gone');
  assert.ok(HTML.includes('id="risk-state"'),'#risk-state stays');
  assert.ok(HTML.includes('id="frame-timestamp"'),'#frame-timestamp stays');
});

test('the event log element exists',()=>{
  assert.ok(HTML.includes('id="event-log"'));
});

test('the original legend is restored',()=>{
  for(const label of ['Metric Ground Plane Twin (EPSG:3857 Geodetic Datum)',
                      '■ BLUE: Worker Tracks','■ AMBER: Heavy Machinery',
                      '■ RED: BIM Trench Hazard']){
    assert.ok(HTML.includes(label),`missing original legend label: ${label}`);
  }
  assert.ok(!HTML.includes('RED: Configured Hazard'),'the invented legend label must be gone');
  assert.ok(!HTML.includes('AMBER: Equipment</span>'),'the shortened label must be restored');
});

test('none of the removed additions remain in the canvas',()=>{
  for(const gone of ['buildMachine','labelSprite','machineGroup','STATE_COLORS',
                     'drawZones','zone_overlays','hazardGroup']){
    assert.ok(!SOURCE.includes(gone),`${gone} must be gone from the 3D canvas`);
  }
});

test('the original geometry is restored',()=>{
  // Straight-edged polygons sized at runtime from the measured footprint. The
  // worker post, the trench plane, the grid and the ring keep the original's.
  for(const geometry of ['new THREE.PlaneGeometry(1,1)','CylinderGeometry(0.3,0.3,1.8,16)',
                         'new THREE.BoxGeometry(1,1,1)','new THREE.PlaneGeometry(20,7)',
                         'GridHelper(50,50','RingGeometry(1.0,1.4,32)',
                         '0x38bdf8','0xfbbf24']){
    assert.ok(SOURCE.includes(geometry),`missing original geometry: ${geometry}`);
  }
});

test('the twin consumes the original tracks_3d contract',()=>{
  assert.ok(SOURCE.includes('packet.tracks_3d'),'the twin must read tracks_3d');
  assert.ok(SOURCE.includes('packet.zone_exits'),'the twin must read zone_exits for the ring');
  assert.ok(SOURCE.includes('getOrCreateAgentMesh'),'the original agent mesh factory is used');
  assert.ok(SOURCE.includes("userData.pose='heading'"),'workers are posed along their heading');
  assert.ok(SOURCE.includes("userData.pose='group'"),'machinery are posed along their heading');
});

test('the event log renders the zone transitions',()=>{
  const box=makeSandbox();
  box.run(`renderEventLog(${JSON.stringify(PACKET)})`);
  const text=box.elements['event-log'].textContent;
  assert.ok(text.includes('ZONE_ENTRY'),`log missing the entry, got: ${text}`);
  assert.ok(text.includes('ZONE_EXIT'),`log missing the exit, got: ${text}`);
  assert.ok(text.includes('worker 4'),`the log must name the worker, got: ${text}`);
  assert.ok(text.includes('1.2s'),`the log must carry a timestamp, got: ${text}`);
});

test('an empty event log clears the element',()=>{
  const box=makeSandbox();
  const empty=Object.assign({},PACKET,{zone_events:[]});
  box.run(`renderEventLog(${JSON.stringify(empty)})`);
  assert.equal(box.elements['event-log'].textContent,'');
});

test('the worker state tag reads worker_states',()=>{
  assert.ok(SOURCE.includes('worker_states'),'the panel reads worker_states');
  for(const label of ["'IN ZONE'",'MOVING','STATIONARY']){
    assert.ok(SOURCE.includes(label),`missing worker state label: ${label}`);
  }
});

test('the risk and timestamp badges still work',()=>{
  const box=makeSandbox();
  box.run(`updateRiskBadges(${JSON.stringify(PACKET)}, true)`);
  assert.equal(box.elements['risk-state'].textContent,'ATTENTION');
  assert.ok(box.elements['risk-state'].className.includes('badge-warn'));
  assert.ok(/T\+2\.00s/.test(box.elements['frame-timestamp'].textContent));
});

test('with no live camera the state is not claimed',()=>{
  const box=makeSandbox();
  box.run(`updateRiskBadges(${JSON.stringify(PACKET)}, false)`);
  assert.equal(box.elements['risk-state'].textContent,'UNAVAILABLE');
});

test('the proximity ladder is stated in the legend',()=>{
  assert.ok(/DANGER .* 3\.3 m/.test(HTML),'the legend must state the DANGER distance');
  assert.ok(/NEAR .* 8\.0 m/.test(HTML),'the legend must state the NEAR distance');
});

test('the panel draws the original PPE notes',()=>{
  // Plain text only: the emoji that used to prefix these notes rendered as
  // platform-specific glyphs. The wording and colours are the original's.
  for(const note of ['HARDHAT: ','VEST: ','NO HARDHAT','NO VEST']){
    assert.ok(SOURCE.includes(note),`the PPE note ${note} must be drawn`);
  }
  assert.ok(SOURCE.includes("'#10b981'"),'the original ok colour must be used');
});

test('an unmeasured worker is never reported as missing a hardhat',()=>{
  assert.ok(SOURCE.includes('HARDHAT: UNKNOWN'),'an unmeasured worker must read UNKNOWN');
  assert.ok(SOURCE.includes('VEST: UNKNOWN'),'an unmeasured vest must read UNKNOWN');
  // The missing verdict may only come from a measured record.
  const tag=SOURCE.slice(SOURCE.indexOf('ppe.measured?'));
  assert.ok(tag.includes("ppe.hardhat?'OK':'NO HARDHAT'"),
            'NO HARDHAT may only be shown when the record was measured');
});

test('proximity is coloured by grade and shown with its distance',()=>{
  assert.ok(SOURCE.includes("PROXIMITY_COLORS={DANGER:'#ef4444',NEAR:'#f59e0b'"),'grades need colours');
  assert.ok(SOURCE.includes("+' · '+(metres==null?'—':metres.toFixed(1)+' m')"),
            'the grade must be shown with its measured distance');
  assert.ok(SOURCE.includes('NO MACHINERY IN FRAME'),
            'a worker with no machinery must say so rather than read SAFE');
});

test('a worker inside a machinery box is still drawn',()=>{
  assert.ok(SOURCE.includes('function nesting('),'nesting must be detected');
  // Nesting may only change weight and dash, never remove the box.
  const block=SOURCE.slice(SOURCE.indexOf('function nesting('),SOURCE.indexOf('(packet.detections||[]).forEach'));
  assert.ok(!block.includes('return false')&&!block.includes('.splice('),
            'nesting must not drop a detection');
  assert.ok(SOURCE.includes('ctx.strokeRect(left,top,w,h)'),'the worker box is always stroked');
  assert.ok(SOURCE.includes('const host=nesting('),'the box must consult the nesting test');
});

test('a nested worker notes move clear of the host box',()=>{
  assert.ok(SOURCE.includes('host?left+Math.min(w,width+8):left'),
            'a nested worker chip must move clear of the machine chip');
  assert.ok(SOURCE.includes('Math.min(184,canvas.width)'),
            'the chip must be wide enough for the notes and bounded by the canvas');
});

test('the person is never hidden by the machine in the 3D canvas',()=>{
  assert.ok(SOURCE.includes('opacity:0.55'),'the machinery hull must be see-through');
  assert.ok(SOURCE.includes('depthWrite:false'),'a transparent hull must not write depth');
  assert.ok(SOURCE.includes("grp.renderOrder=className==='WORKER'?2:1"),
            'workers must draw after machinery');
  // Separation must not add geometry beyond the two straight-edged polygons.
  for(const geometry of ['new THREE.PlaneGeometry(1,1)','new THREE.BoxGeometry(1,1,1)',
                         'CylinderGeometry(0.3,0.3,1.8,16)']){
    assert.ok(SOURCE.includes(geometry),`the agent geometry ${geometry} must stay`);
  }
});

test('a worker with no machinery is graded NO_MACHINE, never SAFE',()=>{
  assert.ok(SOURCE.includes("state.proximity_state==='NO_MACHINE'"),
            'NO_MACHINE must be rendered distinctly from SAFE');
  assert.ok(!SOURCE.includes("'SAFE':PROXIMITY_COLORS"),'SAFE must come from the server grade');
});

test('the drawn trench follows the calibrated zone, not a fixed literal',()=>{
  assert.ok(SOURCE.includes('const zone=packet.zone;'),'the mesh must read the zone from the packet');
  assert.ok(/trenchMesh\.scale\.set\(\(zone\.max_x-zone\.min_x\)\/20/.test(SOURCE),
            'the mesh must scale to the zone extent');
  const update=SOURCE.slice(SOURCE.indexOf('const zone=packet.zone;'));
  assert.ok(!update.includes('trenchMesh.position.set(2,6.5,0.05)'),
            'the update path must not re-apply a hardcoded placement');
});

test('a stationary agent keeps a stable pose',()=>{
  assert.ok(SOURCE.includes('}else if(trk.moving){'),
            'the pose must follow the heading only while the agent is moving');
  assert.ok(SOURCE.includes('POSE_MAX_STEP'),'the turn must be capped so it cannot snap');
  assert.ok(SOURCE.includes('POSE_EASE'),'the pose must ease toward the measured heading');
});

test('the machinery hull is anchored off its own footpoint',()=>{
  assert.ok(SOURCE.includes('const half=(mesh.userData.hull.scale.y||1)/2;'),
            'the hull must be pushed forward by half its measured length');
  assert.ok(SOURCE.includes('grp.userData.hull=body'),'the hull must be recorded on the group');
});

/* Behavioural: the pose gate, the hull anchor and the packet-driven trench are
   checked against the real update loop with a fake mesh, not just by reading
   the source. */
function extractUpdate(){
  const body=SOURCE.slice(SOURCE.indexOf('function update(packet){'),
                          SOURCE.indexOf('// Exit feedback reuses'));
  const agentMeshes={};
  const trenchMesh={position:{x:null,y:null,set(x,y,z){this.x=x;this.y=y;}},
                    scale:{scaleX:null,scaleY:null,set(x,y){this.scaleX=x;this.scaleY=y;}}};
  const meshFor=cls=>({userData:{pose:cls==='WORKER'?'heading':'group',hull:null,body:{rotation:{z:0}}},
    rotation:{z:0},position:{set(){}}});
  const factory=id=>agentMeshes[id]||(agentMeshes[id]=meshFor(id===7?'HEAVY_EQUIPMENT':'WORKER'));
  const frame={target:{x:0,y:4},radius:42};
  const scene={add(){},remove(){}};
  const update=new Function('getOrCreateAgentMesh','agentMeshes','trenchMesh',
    'POSE_MAX_STEP','POSE_EASE','FRAME_MIN_RADIUS','FRAME_PADDING','FRAME_EASE',
    'view','framedByUser','scene','trailLines','THREE','return ('+body+'\n})')(
    factory,agentMeshes,trenchMesh,0.18,0.18,8,2.4,0.08,frame,false,scene,new Map(),
    {Vector3:function(x,y,z){return {x,y,z};},
     Line:function(){return {geometry:{setFromPoints(){},dispose(){}},material:{},
                               visible:true,renderOrder:0};},
     BufferGeometry:function(){return {setFromPoints(){},dispose(){}};},
     LineBasicMaterial:function(){return {};}});
  const machine=(heading,moving)=>({frame_width:1920,frame_height:1080,
    tracks_3d:[{track_id:7,class_name:'HEAVY_EQUIPMENT',position:[0,5],heading,moving,history:[[0,5]]},
                {track_id:9,class_name:'WORKER',position:[2,1.2],heading:0,moving:false,history:[[2,1.2]]}],
    worker_states:[],zone_exits:[],zone:{min_x:-8,max_x:12,min_y:1.5,max_y:8.5}});
  return {update,agentMeshes,trenchMesh,machine};
}

test('a parked machine holds one rotation however noisy its heading is',()=>{
  const {update,agentMeshes,machine}=extractUpdate();
  const seen=new Set();
  for(let k=0;k<12;k++){update(machine(k*0.9,false),agentMeshes);seen.add(agentMeshes[7].rotation.z);}
  assert.equal(seen.size,1,'a stationary machine must render at exactly one angle');
  assert.ok(Math.abs([...seen][0]-(-Math.PI/2))<1e-9,'it holds the heading it was first given');
});

test('a moving machine still follows its heading',()=>{
  const {update,agentMeshes,machine}=extractUpdate();
  const seen=new Set();
  for(let k=0;k<5;k++){update(machine(k*0.5,true),agentMeshes);seen.add(agentMeshes[7].rotation.z);}
  assert.equal(seen.size,5,'a moving machine must rotate with its heading');
});

test('the hull is pushed off the footpoint the worker stands on',()=>{
  const {update,agentMeshes,machine}=extractUpdate();
  // The agent mesh is created by the first update, so attach the hull after it.
  update(machine(0,false),agentMeshes);
  const bodyMesh={position:{x:null,y:null,z:null,set(x,y,z){this.x=x;this.y=y;this.z=z;}},
                  scale:{x:1,y:1,z:1,set(x,y,z){this.x=x;this.y=y;this.z=z;}}};
  agentMeshes[7].userData.hull=bodyMesh;
  bodyMesh.scale.set(3.0,6.0,2.0);          // a measured 3 x 6 m footprint
  update(machine(0,false),agentMeshes);
  assert.equal(bodyMesh.position.x,0);
  assert.equal(bodyMesh.position.y,3.0,'half the measured 6.0 m hull length forward');
  assert.equal(bodyMesh.position.z,1.0);
});

test('the drawn trench takes its centre and extent from the packet zone',()=>{
  const {update,trenchMesh,machine}=extractUpdate();
  update(machine(0,false),{});
  assert.equal(trenchMesh.position.x,2,'centre x of x[-8,12]');
  assert.equal(trenchMesh.position.y,5,'centre y of y[1.5,8.5]');
  assert.equal(trenchMesh.scale.scaleX,1,'20 m across the original 20 m plane');
  assert.equal(trenchMesh.scale.scaleY,1,'7 m deep the original 7 m plane');
});

/* The overlay packer: annotations must never overprint each other and never
   leave the drawn image. Replayed against the real packet shapes. */
function extractPacker(){
  const start=SOURCE.indexOf('function makePacker');
  const end=SOURCE.indexOf('function nesting');
  return new Function('return ('+SOURCE.slice(start,end)+')')();
}
function hostBox(dets,d){
  const area=Math.max(0,d[2]-d[0])*Math.max(0,d[3]-d[1]);if(!area)return null;
  for(const o of dets){
    if(o===d||o[5]<2)continue;
    const w=Math.min(d[2],o[2])-Math.max(d[0],o[0]),h=Math.min(d[3],o[3])-Math.max(d[1],o[1]);
    if(w>0&&h>0&&w*h/area>0.6)return o;
  }
  return null;
}
function packFrame(makePacker,packet,W,H,scale,y){
  const place=makePacker({x:0,y:0,w:W,h:H});
  const labels=[],chips=[];
  for(const d of packet.detections||[]){
    const [x1,y1,x2,y2,conf,cls]=d,worker=cls<2;
    const left=x1*scale,top=y+y1*scale,w=(x2-x1)*scale,h=(y2-y1)*scale;
    labels.push(place(200,20,{x:left,y:Math.max(0,top-21)},21));
    if(!worker)continue;
    const st=(packet.worker_states||[]).find(v=>v.track_id===d[6]);
    const ppe=(packet.ppe||[]).find(q=>q.bbox&&q.bbox.every((v,i)=>Math.abs(v-d[i])<1));
    const n=(ppe?2:0)+(st&&st.proximity_state?1:0)+(st?1:0);
    if(!n)continue;
    const host=hostBox(packet.detections||[],d),cw=Math.min(184,W),chh=19*n;
    chips.push(place(cw,chh,
      {x:host?left+Math.min(w,cw+8):left,
       y:host?((top-2-chh>y)?top-2-chh:top+h+2):top+h+2},chh+2));
  }
  return {labels,chips};
}
const overlaps=(a,b)=>Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x)>0 &&
                      Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y)>0;
// a crowded frame: seven workers, three of them nested in one machine
const CROWDED={frame_width:1920,frame_height:1080,
  detections:[[300,600,420,1000,0.9,0.0,1],[420,600,540,1000,0.9,0.0,2],
              [600,600,720,1000,0.9,0.0,3],[760,590,880,1000,0.9,0.0,4],
              [900,600,1020,1000,0.9,0.0,5],[1100,600,1220,1000,0.9,0.0,6],
              [1500,100,1900,700,0.5,2.0,7],[1600,200,1700,400,0.3,0.0,8]],
  worker_states:[1,2,3,4,5,6,8].map(id=>({track_id:id,label:'MOVING',
    proximity_state:'NO_MACHINE',nearest_machinery_metres:null})),
  ppe:[{bbox:[300,600,420,1000],hardhat:true,vest:true,measured:true}]};

test('no two detection labels ever overprint',()=>{
  const makePacker=extractPacker();
  const {labels}=packFrame(makePacker,CROWDED,996,560,0.2916,88);
  for(let i=0;i<labels.length;i++)for(let j=i+1;j<labels.length;j++)
    assert.ok(!overlaps(labels[i],labels[j]),'two labels overlap on a crowded frame');
});

test('no two worker note chips ever overprint',()=>{
  const makePacker=extractPacker();
  const {chips}=packFrame(makePacker,CROWDED,996,560,0.2916,88);
  assert.ok(chips.length>=6,'this frame must produce several chips');
  for(let i=0;i<chips.length;i++)for(let j=i+1;j<chips.length;j++)
    assert.ok(!overlaps(chips[i],chips[j]),'two note chips overlap on a crowded frame');
});

test('a label never lands on a note chip',()=>{
  const makePacker=extractPacker();
  const {labels,chips}=packFrame(makePacker,CROWDED,996,560,0.2916,88);
  for(const l of labels)for(const c of chips)
    assert.ok(!overlaps(l,c),'a label overprints a note chip');
});

test('no annotation is pushed outside the drawn image',()=>{
  const makePacker=extractPacker();
  const W=996,H=560;
  const {labels,chips}=packFrame(makePacker,CROWDED,W,H,0.2916,88);
  for(const r of [...labels,...chips]){
    assert.ok(r.x>=0&&r.y>=0,'annotation starts outside the image');
    assert.ok(r.x+r.w<=W&&r.y+r.h<=H,'annotation runs off the image');
  }
});

test('a box at the very edge still keeps its label on screen',()=>{
  const makePacker=extractPacker();
  const W=996,H=560;
  const edge={frame_width:1920,frame_height:1080,
    detections:[[1700,20,1919,1000,0.9,0.0,1]],worker_states:[],ppe:[]};
  const {labels}=packFrame(makePacker,edge,W,H,W/1920,88);
  assert.equal(labels.length,1);
  assert.ok(labels[0].x+labels[0].w<=W,'the label was pushed off the right edge');
});

test('the zone event log is one entry per line, not one clipped line',()=>{
  assert.ok(SOURCE.includes("node.style.whiteSpace='pre-line'"),
            'the log must wrap by line rather than overflow');
  assert.ok(SOURCE.includes(".join('\\n')"),'entries must be newline separated');
  assert.ok(!SOURCE.includes(".join('   |   ')"),'the joined single line must be gone');
  const box={el:makeElements().el,HTML:HTML};
  assert.ok(HTML.includes('id="event-log"'));
});

/* The objective: a straight person polygon aligned to the person frame, a
   per-agent trail, and a calibrated machinery polygon. Replayed against the
   real update loop, not asserted from the source text. */
function twinHarness(){
  const body=SOURCE.slice(SOURCE.indexOf('function update(packet){'),
                          SOURCE.indexOf('// Exit feedback reuses'));
  const added=[];
  const scene={add:o=>added.push(o),remove(){}};
  const conflictRing={material:{opacity:0},position:{set(){}}};
  const trenchMesh={position:{set(x,y){this.x=x;this.y=y;}},scale:{set(x,y){this.x=x;this.y=y;}}};
  const trailLines=new Map();
  const agentMeshes={};
  const node=()=>({rotation:{z:0},userData:{},renderOrder:0,
    position:{x:0,y:0,z:0,set(x,y,z){this.x=x;this.y=y;this.z=z;}},
    scale:{x:1,y:1,z:1,set(x,y,z){this.x=x;this.y=y;this.z=z===undefined?1:z;}},
    material:{opacity:1},geometry:{setFromPoints(){},dispose(){}}});
  const worker=node(); worker.userData.footprint=node(); worker.userData.body=node();
  agentMeshes[1]=worker;
  const machine=node(); machine.userData.hull=node(); machine.userData.cab=node();
  machine.userData.body=machine.userData.hull; agentMeshes[2]=machine;
  const THREE={Vector3:function(x,y,z){return {x,y,z};},Line:function(){return node();},
    BufferGeometry:function(){return {setFromPoints(){},dispose(){}};},
    LineBasicMaterial:function(){return {};}};
  const update=new Function('getOrCreateAgentMesh','agentMeshes','trenchMesh','scene',
    'conflictRing','trailLines','THREE','POSE_MAX_STEP','POSE_EASE',
    'FRAME_MIN_RADIUS','FRAME_PADDING','FRAME_EASE','view','framedByUser',
    'return ('+body+'\n})')(
    id=>agentMeshes[id],agentMeshes,trenchMesh,scene,conflictRing,trailLines,THREE,
    0.18,0.18,8,2.4,0.08,{target:{x:0,y:4},radius:42},false);
  return {update,worker,machine,agentMeshes,trailLines,added,trenchMesh};
}
const TWO_AGENTS={frame_width:1920,frame_height:1080,
  tracks_3d:[
    {track_id:1,class_name:'WORKER',position:[1,6],heading:0.6,moving:true,
     history:[[0,5],[0.5,5.5],[1,6]],footprint:{width_m:0.62,depth_m:0.34}},
    {track_id:2,class_name:'HEAVY_EQUIPMENT',position:[-3,9],heading:2.0,moving:true,
     history:[[-4,8],[-3.5,8.5],[-3,9]],footprint:{width_m:3.1,depth_m:5.96}},
  ],
  worker_states:[],zone_exits:[],zone:{min_x:-8,max_x:12,min_y:4,max_y:11}};

test('the person polygon is sized from the measured person frame',()=>{
  const h=twinHarness(); h.update(TWO_AGENTS);
  assert.equal(h.worker.userData.footprint.scale.x,0.62,'width from the packet');
  assert.equal(h.worker.userData.footprint.scale.y,0.34,'depth from the packet');
});

test('the person polygon is straight-edged, not a circle',()=>{
  assert.ok(SOURCE.includes('new THREE.PlaneGeometry(1,1)'),
            'the worker footprint must be a straight-edged plane');
  assert.ok(!/CircleGeometry\(0\.8/.test(SOURCE),'the circular disc is gone');
});

test('the polygon and the direction post share the walking direction',()=>{
  const h=twinHarness(); h.update(TWO_AGENTS);
  const expected=0.6-Math.PI/2;
  assert.ok(Math.abs(h.worker.rotation.z-expected)<1e-9,
            'the group turns to the heading, so the polygon lies along the walk');
});

test('each agent keeps its own trail and its own path',()=>{
  const h=twinHarness(); h.update(TWO_AGENTS);
  // The original drew two lines per agent: the trail behind and the path ahead.
  // Two agents therefore own four lines, none of them shared.
  assert.equal(h.trailLines.size,4,'a trail and a path for each of two agents');
  assert.deepEqual([...h.trailLines.keys()].sort(),
                   ['1:path','1:trail','2:path','2:trail']);
  const geometries=new Set([...h.trailLines.values()].map(l=>l.geometry));
  assert.equal(geometries.size,4,'no two lines may share a geometry');
});

test('the trail and the path use the original line colours',()=>{
  assert.ok(SOURCE.includes("kind==='trail'?0x64748b"),'the trail is the original grey');
  assert.ok(SOURCE.includes('0x38bdf8'),'a worker path is the original blue');
  assert.ok(SOURCE.includes('0xfbbf24'),'a machinery path is the original amber');
});

test('both lines sit above the trench plane and the footprint',()=>{
  assert.ok(SOURCE.includes('new THREE.Vector3(p[0],p[1],0.15)'),
            'lifted to 0.15, above the trench at 0.05 and the footprint at 0.02');
});

test('the person post stands upright instead of lying flat',()=>{
  // A cylinder's axis is Y and this scene's up is Z. The original code had both
  // of these lines; losing them laid the 1.8 m post on the ground as a bar with
  // rounded ends, which is what made the marker read as a blob rather than a
  // straight polygon with a direction.
  assert.ok(SOURCE.includes('cyl.rotation.x=Math.PI/2'),
            'the post must be rotated upright');
  assert.ok(SOURCE.includes('cyl.position.z=0.9'),
            'and offset to half its height, as the original did');
});

test('the machinery polygon is sized from the measured footprint',()=>{
  const h=twinHarness(); h.update(TWO_AGENTS);
  const hull=h.machine.userData.hull;
  assert.equal(hull.scale.x,3.1,'width from the packet');
  assert.equal(hull.scale.y,5.96,'depth from the packet');
  assert.ok(Math.abs(hull.scale.z-1.8*(3.1/2.6))<1e-9,'height keeps the original proportion');
});

test('the original worker post and trench are still the original geometry',()=>{
  assert.ok(SOURCE.includes('CylinderGeometry(0.3,0.3,1.8,16)'),'the direction post stays');
  assert.ok(SOURCE.includes('new THREE.PlaneGeometry(20,7)'),'the trench plane stays');
});

/* The twin must actually run. Earlier the update loop was tested by extracting it
   and injecting `trenchMesh` as a parameter, which hid the fact that it was
   declared inside the try block where update() cannot see it: every frame threw
   "trenchMesh is not defined" and the panel went blank. This builds the real
   module in a sandbox and drives it, so a scoping mistake cannot pass again. */
function threeStub(){
  const made=[];
  const node=function(){
    const m={rotation:{x:0,y:0,z:0},scale:{x:1,y:1,z:1,set(a,b,c){this.x=a;this.y=b;this.z=c===undefined?1:c;}},
      position:{x:0,y:0,z:0,set(a,b,c){this.x=a;this.y=b;this.z=c;}},
      up:{set(){}},aspect:1,
      lookAt(){},updateProjectionMatrix(){},setPixelRatio(){},setSize(){},setClearColor(){},render(){},
      domElement:{addEventListener(){}},
      material:{opacity:1,color:null},geometry:{setFromPoints(){},dispose(){},x:0},
      userData:{},renderOrder:0,visible:true,add(){},remove(){}};
    made.push(m);return m;
  };
  const mk=()=>function(){return node();};
  return {made,THREE:{
    Scene:mk(),Color:mk(),PerspectiveCamera:mk(),Group:mk(),Mesh:mk(),Line:mk(),
    CircleGeometry:mk(),PlaneGeometry:mk(),BoxGeometry:mk(),CylinderGeometry:mk(),
    RingGeometry:mk(),GridHelper:mk(),MeshBasicMaterial:mk(),LineBasicMaterial:mk(),
    BufferGeometry:mk(),Vector3:mk(),
    WebGLRenderer:mk()}};
}

test('the real twin builds and updates a packet without throwing',()=>{
  const {THREE,made}=threeStub();
  const els={};
  const ctx=new Proxy({},{get:(t,k)=>k==='canvas'?{width:1920,height:1080}:()=>{}});
  const el=id=>els[id]||(els[id]={id,textContent:'',hidden:false,className:'',
    clientWidth:800,clientHeight:400,appendChild(){},addEventListener(){},
    setAttribute(){},style:{},getContext:()=>ctx,
    parentElement:{clientWidth:800,clientHeight:400}});
  els['vision-canvas']={id:'vision-canvas',width:1920,height:1080,style:{},getContext:()=>ctx,
    parentElement:{clientWidth:800,clientHeight:400}};
  const world={el,ctx,THREE,devicePixelRatio:1,
    document:{getElementById:el,createElement:()=>({getContext:()=>ctx}),
      addEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],hidden:false},
    window:{addEventListener(){}},performance:{now:()=>0},console:{log(){},warn(){},error(){}},
    requestAnimationFrame(){return 0;},cancelAnimationFrame(){},
    setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},
    Math,JSON,Date,Number,Array,Object};
  world.globalThis=world;
  vm.createContext(world);
  vm.runInContext(SOURCE,world);

  const container=el('webgl-wrapper');
  assert.equal(container.textContent,'','the twin must not have fallen back to unavailable');
  const twin=world.createTwin();
  assert.equal(typeof twin.update,'function');

  const packet={frame_width:1920,frame_height:1080,
    detections:[[300,600,420,1000,0.9,0.0,1],[500,200,1200,700,0.5,2.0,2]],
    tracks_3d:[
      {track_id:1,class_name:'WORKER',position:[1,6],heading:0.6,moving:true,
       history:[[0,5],[0.5,5.5],[1,6]],footprint:{width_m:0.62,depth_m:0.34}},
      {track_id:2,class_name:'HEAVY_EQUIPMENT',position:[-3,9],heading:2.0,moving:true,
       history:[[-4,8],[-3.5,8.5],[-3,9]],footprint:{width_m:3.1,depth_m:5.96}}],
    worker_states:[],zone_exits:[],zone:{min_x:-8,max_x:12,min_y:4,max_y:11}};

  // This is the assertion that was impossible before: the real closure, not a
  // parameter list assembled by the test.
  assert.doesNotThrow(()=>twin.update(packet),
    'update() must not reference a name it cannot see');
  assert.doesNotThrow(()=>twin.update(packet),'and it must be repeatable');
  assert.doesNotThrow(()=>twin.clear());
  assert.doesNotThrow(()=>twin.resize());
});

/* The view used to sit a fixed 42 m back while the workers occupy about 8 m, so
   most of the frame was empty grid and the agents were specks in the middle. */
function cameraAfter(agents,frames){
  const made=[];
  const node=()=>{const m={rotation:{z:0},userData:{},renderOrder:0,
    position:{x:0,y:0,z:0,set(a,b,c){this.x=a;this.y=b;this.z=c;}},
    scale:{x:1,y:1,z:1,set(a,b,c){this.x=a;this.y=b;this.z=c===undefined?1:c;}},
    material:{opacity:1},geometry:{setFromPoints(){},dispose(){}}};made.push(m);return m;};
  const agentMeshes={};
  agents.forEach((a,i)=>{const m=node();m.userData.footprint=node();m.userData.body=node();
    m.userData.poseHeading=0;m.userData.posed=true;agentMeshes[i+1]=m;});
  const frame={target:{x:0,y:4},radius:42};
  const trenchMesh={position:{set(x,y){this.x=x;this.y=y;}},scale:{set(x,y){this.x=x;this.y=y;}}};
  const THREE={Vector3:function(x,y,z){return{x,y,z};},Line:function(){return node();},
    Mesh:function(){return node();},
    BufferGeometry:function(){return{setFromPoints(){},dispose(){}};},
    LineBasicMaterial:function(){return{};}};
  const body=SOURCE.slice(SOURCE.indexOf('function update(packet){'),
                          SOURCE.indexOf('// Exit feedback reuses'));
  const update=new Function('getOrCreateAgentMesh','agentMeshes','trenchMesh','scene',
    'conflictRing','trailLines','THREE','POSE_MAX_STEP','POSE_EASE',
    'FRAME_MIN_RADIUS','FRAME_PADDING','FRAME_EASE','view','framedByUser',
    'return ('+body+'\n})')(id=>agentMeshes[id],agentMeshes,trenchMesh,{add(){}},
    {material:{opacity:0},position:{set(){}}},new Map(),THREE,0.18,0.18,8,2.4,0.08,
    frame,false);
  for(let k=0;k<frames;k++){
    update({frame_width:1920,frame_height:1080,
      tracks_3d:agents.map((a,i)=>({track_id:i+1,class_name:'WORKER',position:a,
        heading:0,moving:false,history:[a],footprint:{width_m:0.6,depth_m:0.35}})),
      worker_states:[],zone_exits:[],zone:{min_x:-8,max_x:12,min_y:4,max_y:11}});
  }
  return frame;
}

test('the view closes in on the agents instead of holding 42 m of empty grid',()=>{
  const before=cameraAfter([[0,5]],1);
  const after=cameraAfter([[0,5]],80);
  assert.ok(after.radius<before.radius,
            `the view must move in: ${after.radius} vs ${before.radius}`);
  assert.ok(after.radius>=8,'but never closer than the minimum');
});

test('the framing settles rather than pumping frame to frame',()=>{
  const a=cameraAfter([[0,5],[6,9]],40);
  const b=cameraAfter([[0,5],[6,9]],41);
  assert.ok(Math.abs(a.radius-b.radius)<0.5,'the radius must ease, not jump');
});

test('the framing centres on the agents',()=>{
  const f=cameraAfter([[0,5],[6,9]],120);
  assert.ok(Math.abs(f.target.x-3)<1.0,`target.x ${f.target.x} should sit near 3`);
  assert.ok(Math.abs(f.target.y-7)<1.0,`target.y ${f.target.y} should sit near 7`);
});

test('a user zoom is respected once they take the wheel',()=>{
  assert.ok(SOURCE.includes('framedByUser=true'),'the wheel must claim the framing');
  assert.ok(SOURCE.includes('if(!framedByUser){'),
            'and the view must stop overriding the radius afterwards');
});

test('the direction post is no wider than the person it marks',()=>{
  const h=twinHarness(); h.update(TWO_AGENTS);
  const body=h.worker.userData.body;
  // The post starts a fixed 0.6 m across; it must follow the measured width so a
  // small person is not carrying a post wider than they are.
  assert.ok(body.scale, 'the post must be scalable');
  assert.ok(SOURCE.includes('mesh.userData.body.scale.set(fp.width_m/0.6,1,fp.width_m/0.6)'),
            'the post diameter must follow the measured footprint width');
});

test('no emoji anywhere in the shipped interface',()=>{
  // This is a production surface. Pictographic glyphs render differently on every
  // platform - the worker emoji came out as a pink flesh-toned blob beside
  // HARDHAT - so the interface carries plain text only.
  const {execSync}=require('node:child_process');
  const files=['src/dashboard/assets/hub.js','src/dashboard/index.html','src/dashboard/setup.html'];
  const emoji=/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2190}-\u{21FF}\u{2B00}-\u{2BFF}\u{FE0F}]/u;
  for(const file of files){
    const text=require('node:fs').readFileSync(file,'utf8');
    const hit=text.match(emoji);
    assert.equal(hit,null,`${file} must not contain ${hit&&hit[0]}`);
  }
});

test('the panel discloses a source recording that carries its own labels',()=>{
  // The pink "helmet 0.89" boxes are burned into some of the stock footage. They
  // are not this system's detections, and the panel now says so.
  assert.ok(SOURCE.includes("el('source-note')"),'the note element must be wired');
  assert.ok(SOURCE.includes('packet.source_annotations'),'it must read the packet flag');
  const html=require('node:fs').readFileSync('src/dashboard/index.html','utf8');
  assert.ok(html.includes('id="source-note"'),'the element must exist in the page');
  const css=require('node:fs').readFileSync('src/dashboard/assets/dashboard.css','utf8');
  assert.ok(css.includes('#source-note{'),'and be styled so it is readable');
});
