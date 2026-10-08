const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function harness(){
 const elements=new Map();let allocations=0,draws=0,closed=0;
 const drawing=new Proxy({}, {get:(_,key)=>key==='drawImage'?()=>draws++:()=>{}});
 const element=id=>{if(!elements.has(id))elements.set(id,{value:'',checked:false,hidden:false,style:{},classList:{toggle(){}},addEventListener(){},getContext(){return drawing;},textContent:'',querySelector(){return null;}});return elements.get(id);};
 const sandbox={document:{getElementById:element,querySelectorAll:()=>[],querySelector:()=>null,hidden:false},window:{devicePixelRatio:3},THREE:new Proxy({}, {get(){allocations++;throw new Error('Unexpected WebGL allocation');}}),AbortController,clearTimeout(){},setInterval(){},setTimeout(){},fetch:()=>new Promise(()=>{}),Date,Map,Promise,console};
 vm.createContext(sandbox);
 vm.runInContext(fs.readFileSync('src/dashboard/assets/hub.js','utf8'),sandbox);
 return {sandbox,element,drawing,get allocations(){return allocations;},get draws(){return draws;},get closed(){return closed;},close(){closed++;}};
}

test('Opening the panel does not allocate WebGL, even on high-DPI screens',()=>{
 const h=harness();assert.equal(h.allocations,0);assert.equal(h.element('metric-map').hidden,false);
});

test('Repeated frame identifiers reuse the canvas and close decoded bitmaps',async()=>{
 const h=harness();let resizes=0,width=960,height=540,round=0;
 const canvas={getContext:()=>h.drawing,get width(){return width;},set width(v){resizes++;width=v;},get height(){return height;},set height(v){resizes++;height=v;}};
 h.sandbox.tile={removed:false,canvas,telemetry:null};
 h.sandbox.fetch=async()=>({ok:true,status:200,headers:{get:()=> '9'},body:{cancel:async()=>{}},blob:async()=>({})});
 h.sandbox.createImageBitmap=async()=>({width:960,height:540,close:()=>h.close()});
 h.sandbox.setTimeout=(callback,ms)=>{if(ms<15000){if(++round===2)vm.runInContext('running=false',h.sandbox);queueMicrotask(callback);}return 1;};
 await vm.runInContext("running=true;selected='cam1';paintLoop('cam1',tile)",h.sandbox);
 assert.equal(resizes,0);assert.equal(h.draws,1);assert.equal(h.closed,1);
});
