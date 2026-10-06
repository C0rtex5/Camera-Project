'use strict';
/* Browser checks for the restored original-asset panel (src/dashboard/assets/media.js). */
const test=require('node:test');
const assert=require('node:assert');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const ROOT=path.resolve(__dirname,'..');
const SOURCE=fs.readFileSync(path.join(ROOT,'src','dashboard','assets','media.js'),'utf8');
const HTML=fs.readFileSync(path.join(ROOT,'src','dashboard','index.html'),'utf8');

function makeElement(){
  const element={
    id:'',
    className:'',
    textContent:'',
    value:'',
    hidden:false,
    disabled:false,
    href:'',
    src:'',
    children:[],
    listeners:{},
    dataset:{},
    attributes:{},
    appendChild(child){this.children.push(child);return child;},
    replaceChildren(...children){this.children=children;},
    removeAttribute(name){delete this.attributes[name];},
    setAttribute(name,value){this.attributes[name]=value;},
  };
  return element;
}

function page({media,datasets,manifest}={}){
  const elements=new Map();
  const get=id=>{
    if(!elements.has(id)){const element=makeElement();element.id=id;elements.set(id,element);}
    return elements.get(id);
  };
  const document={
    hidden:false,
    getElementById:get,
    createElement:tag=>{const element=makeElement();element.tag=tag;return element;},
    addEventListener(){},
  };
  const urls=new Map();
  const context={
    document,
    fetch:async url=>{
      urls.set(String(url),true);
      if(String(url).includes('/media')&&!String(url).includes('/datasets')){
        return {ok:true,json:async()=>media};
      }
      if(String(url).includes('/datasets')&&!String(url).includes('manifest')){
        return {ok:true,json:async()=>datasets};
      }
      return {ok:true,text:async()=>manifest||'names:\n- Hardhat\n'};
    },
    setTimeout:(fn)=>{context.__timer=fn;return 1;},
    clearTimeout(){context.__timer=null;},
    AbortSignal:{timeout(){return{}}},
    encodeURIComponent,
    Math,
    JSON,
  };
  context.globalThis=context;
  vm.createContext(context);
  vm.runInContext(SOURCE,context);
  return {context,get,urls};
}

const MEDIA={
  count:2,
  playable:2,
  media:[
    {id:'real_videos_blind',group:'real_videos',filename:'Worker_in_excavator_blind_spot.mp4',title:'Worker In Excavator Blind Spot',width:1920,height:1080,frame_count:120,duration_seconds:4,fps:25,size_bytes:9437184,playable:true,lfs_oid:'21ae7678af7cb4e4e13e231afcda8c339',showcase_scenario_id:'scenario_worker_in_excavator_blind_spot',note:''},
    {id:'test_videos_sample',group:'test_videos',filename:'construction_ppe_sample.mp4',title:'Construction Ppe Sample',width:null,height:null,frame_count:null,duration_seconds:null,fps:null,size_bytes:258,playable:false,lfs_oid:null,showcase_scenario_id:null,note:'upstream asset smaller than 10 KiB; retained for provenance only'},
  ],
};

const DATASETS={
  count:1,
  datasets:[
    {id:'roboflow_downloaded',layout:'split_first',total_images:398,total_labels:398,class_count:17,license:'CC BY 4.0',source:'https://universe.roboflow.com/x',splits:[{name:'train',images:307,labels:307}]},
  ],
};

test('the panel markup is present in the dashboard',()=>{
  for(const id of ['media-panel','media-list','dataset-list','media-frame','media-play','media-open','media-summary']){
    assert.ok(HTML.includes(`id="${id}"`),`missing #${id}`);
  }
  assert.ok(HTML.includes('/assets/media.js'),'media.js is not loaded');
});

test('loading renders every restored video and the dataset',async()=>{
  const p=page({media:MEDIA,datasets:DATASETS});
  await p.get('media-refresh').onclick();
  const summary=p.get('media-summary').textContent;
  assert.match(summary,/2 original videos/);
  assert.match(summary,/1 datasets/);
  assert.equal(p.get('media-list').children.length,2);
  assert.equal(p.get('dataset-list').children.length,1);
  const dataset=p.get('dataset-list').children[0];
  assert.ok(JSON.stringify(dataset).includes('roboflow_downloaded'));
  assert.ok(JSON.stringify(dataset).includes('CC BY 4.0'));
});

test('a showcase video is auto-selected and previewed',async()=>{
  const p=page({media:MEDIA,datasets:DATASETS});
  await p.get('media-refresh').onclick();
  assert.equal(p.get('media-title').textContent,'Worker_in_excavator_blind_spot.mp4');
  assert.match(p.get('media-frame').src,/real_videos_blind\/frames\/0/);
  assert.equal(p.get('media-play').disabled,false);
});

test('a provenance-only asset is marked and cannot play',async()=>{
  const p=page({media:MEDIA,datasets:DATASETS});
  await p.get('media-refresh').onclick();
  const row=p.get('media-list').children[1];
  assert.ok(JSON.stringify(row).includes('NOT PLAYABLE'));
  await row.children[row.children.length-1].onclick();
  assert.match(p.get('media-status').textContent,/provenance only/i);
  assert.equal(p.get('media-play').disabled,true);
  assert.equal(p.get('media-link').hidden,true);
});

test('playback advances through frames and stops on pause',async()=>{
  const p=page({media:MEDIA,datasets:DATASETS});
  await p.get('media-refresh').onclick();
  await p.get('media-play').onclick();
  assert.equal(p.get('media-play').textContent,'Pause');
  const tick=p.context.__timer;
  assert.ok(typeof tick==='function');
  tick();
  assert.match(p.get('media-status').textContent,/Frame 2 \/ 120/);
  await p.get('media-play').onclick();
  assert.equal(p.get('media-play').textContent,'Play');
});

test('the dataset manifest is fetched on demand',async()=>{
  const p=page({media:MEDIA,datasets:DATASETS,manifest:'names:\n- Hardhat\n'});
  await p.get('media-refresh').onclick();
  const card=p.get('dataset-list').children[0];
  const manifestButton=card.children[card.children.length-1];
  await manifestButton.onclick();
  assert.match(p.get('dataset-manifest').textContent,/Hardhat/);
});

test('a failing catalog reports the error instead of breaking the hub',async()=>{
  const p=page();
  p.context.fetch=async()=>({ok:false,status:503});
  await p.get('media-refresh').onclick();
  assert.match(p.get('media-summary').textContent,/unavailable/i);
});
