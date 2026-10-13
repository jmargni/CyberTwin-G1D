let pc=null;
const $=id=>document.getElementById(id);
for(const [id,port] of [['head',60001],['left',60002],['right',60003],['twin',3000]]){const u=new URL(location.href);u.port=port;u.pathname='/';u.protocol=id==='twin'?'http:':'https:';$(id).href=u.href;}
function stop(){if(pc){pc.close();pc=null;}$('video').srcObject=null;$('start').disabled=false;$('stop').disabled=true;}
$('stop').onclick=()=>{stop();$('status').textContent='Stream fermato';};
$('start').onclick=async()=>{
 stop();$('start').disabled=true;$('status').textContent='Connessione WebRTC…';
 const conn=new RTCPeerConnection({iceServers:[]});pc=conn;
 conn.ontrack=e=>{$('video').srcObject=e.streams[0]||new MediaStream([e.track]);};
 conn.onconnectionstatechange=()=>{$('status').textContent='WebRTC: '+conn.connectionState;if(['failed','disconnected'].includes(conn.connectionState))stop();};
 try{
  conn.addTransceiver('video',{direction:'recvonly'});
  await conn.setLocalDescription(await conn.createOffer());
  if(conn.iceGatheringState!=='complete')await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('ICE timeout')),10000);conn.addEventListener('icegatheringstatechange',()=>{if(conn.iceGatheringState==='complete'){clearTimeout(timer);resolve();}});});
  const r=await fetch('/offer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:conn.localDescription.type,sdp:conn.localDescription.sdp})});
  const answer=await r.json();if(!r.ok)throw Error(answer.error||'Stream non disponibile');
  await conn.setRemoteDescription(answer);$('stop').disabled=false;
 }catch(e){stop();$('status').textContent=e.message;}
};
window.addEventListener('pagehide',stop);
setInterval(async()=>{if(!pc)return;try{const r=await fetch('/healthz');if(!r.ok){stop();$('status').textContent='Twin o frame non disponibili: stream fermato.';}}catch{stop();$('status').textContent='Camera non raggiungibile.';}},3000);
