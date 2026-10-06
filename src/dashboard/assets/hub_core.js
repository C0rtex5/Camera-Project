/* Pure display rules shared with regression tests. */
(function(root){
const core={
 expires(packet,started){return packet.source==='live'?started+1000*(packet.stale_after_seconds-packet.frame_age_seconds):Infinity},
 valid(packet,epoch,currentEpoch,deadline,now){return epoch===currentEpoch&&(packet.source==='demo'||Number.isFinite(deadline)&&now<deadline)},
 risk(packet){const pairs=packet.evaluated_pairs||[];const times=pairs.map(x=>x.risk.min_ttc).filter(x=>Number.isFinite(x)&&x>=0);return {ttc:times.length?Math.min(...times):null,probability:pairs.length?Math.max(...pairs.map(x=>x.risk.p_col)):null}},
 initial(sources){return sources.cameras.length?'live':'demo'},
 ppe(value){return value===true?'OK':value===false?'MISSING':'UNKNOWN'}
};root.HubCore=core;if(typeof module!=='undefined')module.exports=core;
})(typeof window!=='undefined'?window:globalThis);
