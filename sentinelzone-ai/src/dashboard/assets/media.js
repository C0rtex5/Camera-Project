'use strict';
/* Restored original-media and dataset browser.
 * Reads the read-only showcase catalog served by src/api/media.py.
 * The detection hub above is untouched: this is an additional panel. */
const el=id=>document.getElementById(id);

const state={media:[],datasets:[],current:null,playing:false,timer:null,index:0};

async function api(path,timeout=15000){
  const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(timeout)});
  if(!response.ok)throw Error('Asset catalog unavailable ('+response.status+')');
  return response.json();
}

function badge(text,kind){
  const span=document.createElement('span');
  span.className='badge'+(kind?' '+kind:'');
  span.textContent=text;
  return span;
}

function mediaRow(item){
  const row=document.createElement('li');
  row.className='media-row';
  row.appendChild(badge(item.group==='real_videos'?'PRIMARY':'TEST',item.group==='real_videos'?'badge-gpu':''));
  const name=document.createElement('span');
  name.className='media-name';
  name.textContent=item.filename;
  row.appendChild(name);
  const meta=document.createElement('span');
  meta.className='media-meta';
  meta.textContent=(item.width&&item.height?item.width+'×'+item.height:'—')
    +' · '+(item.frame_count?item.frame_count+' frames':'provenance only')
    +(item.duration_seconds?' · '+item.duration_seconds+' s':'')
    +' · '+(item.size_bytes/1048576).toFixed(1)+' MB';
  row.appendChild(meta);
  if(item.showcase_scenario_id)row.appendChild(badge('SHOWCASE','badge-alert-advisory'));
  if(!item.playable)row.appendChild(badge('NOT PLAYABLE','badge-alert-warning'));
  const open=document.createElement('button');
  open.className='ctrl-btn';
  open.type='button';
  open.textContent=item.playable?'Preview':'Details';
  open.onclick=()=>select(item.id);
  row.appendChild(open);
  return row;
}

function select(mediaId){
  const item=state.media.find(entry=>entry.id===mediaId);
  if(!item)return;
  stop();
  state.current=item;
  state.index=0;
  el('media-title').textContent=item.filename;
  el('media-detail').textContent=(item.width&&item.height?item.width+'×'+item.height+' · ':'')
    +item.title+(item.lfs_oid?' · LFS '+item.lfs_oid.slice(0,12):'');
  el('media-status').textContent=item.playable?'Loading frame…':(item.note||'Not playable');
  const frame=el('media-frame');
  if(item.playable)frame.src='/api/v1/showcase/media/'+encodeURIComponent(item.id)+'/frames/0';
  else{frame.removeAttribute('src');el('media-status').textContent=item.note||'This asset is retained for provenance only.';}
  el('media-play').disabled=!item.playable;
  el('media-link').href=item.playable?'/api/v1/showcase/media/'+encodeURIComponent(item.id)+'/file':'';
  el('media-link').textContent=item.playable?'Open original file':'';
  el('media-link').hidden=!item.playable;
}

function step(){
  const item=state.current;
  if(!item||!item.playable)return;
  const total=item.frame_count||1;
  state.index=(state.index+1)%total;
  el('media-frame').src='/api/v1/showcase/media/'+encodeURIComponent(item.id)+'/frames/'+state.index;
  el('media-status').textContent='Frame '+(state.index+1)+' / '+total;
}

function stop(){state.playing=false;clearTimeout(state.timer);el('media-play').textContent='Play';}

function play(){
  const item=state.current;
  if(!item||!item.playable)return;
  state.playing=!state.playing;
  el('media-play').textContent=state.playing?'Pause':'Play';
  clearTimeout(state.timer);
  if(!state.playing)return;
  const tick=()=>{
    if(!state.playing||document.hidden)return;
    step();
    state.timer=setTimeout(tick,1000/(item.fps||5));
  };
  state.timer=setTimeout(tick,1000/(item.fps||5));
}

function renderDatasets(){
  const list=el('dataset-list');
  list.replaceChildren();
  if(!state.datasets.length){list.textContent='No datasets found.';return;}
  for(const item of state.datasets){
    const card=document.createElement('div');
    card.className='dataset-card';
    const head=document.createElement('div');
    head.className='dataset-head';
    head.appendChild(badge(item.layout==='images_first'?'YOLO':'ROBOFLOW','badge-alert-advisory'));
    const name=document.createElement('strong');
    name.textContent=item.id;
    head.appendChild(name);
    if(item.license)head.appendChild(badge(item.license));
    card.appendChild(head);
    const meta=document.createElement('div');
    meta.className='media-meta';
    meta.textContent=item.total_images+' images · '+item.total_labels+' labels · '+item.class_count+' classes';
    card.appendChild(meta);
    const splits=document.createElement('div');
    splits.className='media-meta';
    splits.textContent=item.splits.map(split=>split.name+' '+split.images+'/'+split.labels).join(' · ');
    card.appendChild(splits);
    if(item.source){
      const link=document.createElement('a');
      link.href=item.source;
      link.rel='noreferrer noopener';
      link.target='_blank';
      link.className='media-meta';
      link.textContent=item.source;
      card.appendChild(link);
    }
    const manifest=document.createElement('button');
    manifest.className='ctrl-btn';
    manifest.type='button';
    manifest.textContent='data.yaml';
    manifest.onclick=async()=>{
      el('dataset-manifest').textContent='Loading…';
      const response=await fetch('/api/v1/showcase/datasets/'+encodeURIComponent(item.id)+'/manifest',{cache:'no-store'});
      el('dataset-manifest').textContent=response.ok?await response.text():'Manifest unavailable.';
    };
    card.appendChild(manifest);
    list.appendChild(card);
  }
}

async function load(){
  try{
    const [media,datasets]=await Promise.all([api('/api/v1/showcase/media'),api('/api/v1/showcase/datasets')]);
    state.media=media.media;
    state.datasets=datasets.datasets;
    el('media-summary').textContent=media.count+' original videos · '+media.playable+' playable · '+datasets.count+' datasets';
    const list=el('media-list');
    list.replaceChildren();
    for(const item of state.media)list.appendChild(mediaRow(item));
    renderDatasets();
    if(state.media.length)select(state.media.find(item=>item.showcase_scenario_id)?.id||state.media[0].id);
  }catch(error){
    el('media-summary').textContent=error.message;
  }
}

el('media-play').onclick=play;
el('media-refresh').onclick=load;
el('media-close').onclick=()=>{el('media-panel').hidden=true;};
el('media-open').onclick=()=>{el('media-panel').hidden=false;load();};
document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
