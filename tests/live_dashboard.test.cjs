// Browser failure scenarios without network access or a camera.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const source = fs.readFileSync(require('node:path').join(__dirname, '../src/dashboard/live.html'), 'utf8').split('<script>')[1].split('</script>')[0];
const current = {camera_id:'gate',state:'STREAMING',frame_age_seconds:0.2,stale_after_seconds:1,telemetry:{detections:[]}};
function page() {
  let now=0, drawn=false, closed=0;
  const elements={}, intervals=[], scheduled=[];
  const ctx={clearRect(){drawn=false},drawImage(){drawn=true},strokeRect(){},fillText(){}};
  const element=id=>elements[id]??=(id==='camera'?{width:640,height:480,getContext:()=>ctx}:{textContent:'',value:''});
  const env={document:{getElementById:element,addEventListener(){}},performance:{now:()=>now},
    setInterval:fn=>intervals.push(fn),setTimeout:fn=>scheduled.push(fn),AbortSignal,
    fetch:async path=>({ok:true,json:async()=>[current],blob:async()=>({})}),
    createImageBitmap:async()=>({width:640,height:480,close(){closed++}})};
  vm.createContext(env);vm.runInContext(source,env);
  return {env,element,intervals,scheduled,setTime(t){now=t},drawn:()=>drawn,closed:()=>closed,
    poll:()=>vm.runInContext('poll(0)',env)};
}
test('a hung next request cannot leave the previous camera image or telemetry visible',async()=>{
  const p=page();await p.poll();assert.equal(p.drawn(),true);
  p.env.fetch=()=>new Promise(()=>{});p.scheduled.shift()();
  p.setTime(801);p.intervals[0]();
  assert.equal(p.drawn(),false);assert.match(p.element('status').textContent,/unavailable/);
  assert.equal(p.element('telemetry').textContent,'No current observations.');
});
test('a late image is closed without being shown as current',async()=>{
  const p=page();p.env.createImageBitmap=async()=>{p.setTime(1100);return {close(){p.wasClosed=true}}};
  await p.poll();assert.equal(p.drawn(),false);assert.equal(p.wasClosed,true);
  assert.match(p.element('status').textContent,/stale/);
});
test('reconnecting cannot publish an old in-flight response',async()=>{
  const p=page();let deliver;
  p.env.createImageBitmap=()=>new Promise(resolve=>{deliver=resolve});
  const old=p.poll();while(!deliver)await Promise.resolve();
  p.env.fetch=async()=>({ok:true,json:async()=>[{camera_id:'new',state:'UNAVAILABLE'}]});
  p.element('token').value='new-token';p.element('connect').onclick();
  deliver({width:640,height:480,close(){}});await old;
  for(let i=0;i<5;i++)await Promise.resolve();
  assert.equal(p.drawn(),false);assert.match(p.element('status').textContent,/new.*UNAVAILABLE/);
});
test('requests carry an abort signal and timeout clears current content',async()=>{
  const p=page();await p.poll();
  p.env.fetch=async(path,options)=>{assert.ok(options.signal instanceof AbortSignal);const error=new Error();error.name='TimeoutError';throw error};
  await p.poll();assert.equal(p.drawn(),false);assert.match(p.element('status').textContent,/timed out/);
});
