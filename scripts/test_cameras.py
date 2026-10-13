"""Full local camera check: real CPU rendering, HTTPS, WebRTC decoded video and ZMQ.
Run with Python 3.11 and requirements-camera.txt. No DGX or real robot used.
"""
import asyncio,io,json,os,socket,subprocess,sys,tempfile,time
from pathlib import Path
import aiohttp,numpy as np,requests,zmq
from PIL import Image
from aiortc import RTCPeerConnection,RTCSessionDescription,RTCConfiguration
requests.packages.urllib3.disable_warnings()
ROOT=Path(__file__).resolve().parents[1]
SKIP_ZMQ=os.getenv('SKIP_ZMQ')=='1'
SKIP_WEBRTC=os.getenv('SKIP_WEBRTC')=='1'
def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
async def check():
    import yaml
    with tempfile.TemporaryDirectory() as temp:
        tp,sp=port(),port();config=yaml.safe_load((ROOT/'config/teleimager_server.yaml').read_text())
        config['config_port']=port()
        for c in config['cameras'].values():c['webrtc_port']=port();c['zmq_port']=port()
        cfg=Path(temp)/'config.yaml';cfg.write_text(yaml.safe_dump(config))
        env={**os.environ,'PYTHONPATH':str(ROOT),'CAMERA_CONFIG':str(cfg),'CAMERA_CERT_DIR':temp,'CAMERA_HOST_IP':'10.32.55.9','CAMERA_TWIN_URL':f'http://127.0.0.1:{tp}','CAMERA_WIDTH':'320','CAMERA_HEIGHT':'180','CAMERA_FPS':'3','CAMERA_ENABLE_ZMQ':'false' if SKIP_ZMQ else 'true'}
        log=open(Path(temp)/'runtime.log','w')
        twin=subprocess.Popen([sys.executable,'server.py','--port-3d',str(tp),'--port-security',str(sp),'--data',temp],cwd=ROOT,env=env,stdout=log,stderr=log)
        cam=subprocess.Popen([sys.executable,'-m','services.camera.app'],cwd=ROOT,env=env,stdout=log,stderr=log)
        peers=[];context=zmq.Context();sockets=[]
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                urls={n:f"https://127.0.0.1:{c['webrtc_port']}" for n,c in config['cameras'].items()}
                for _ in range(100):
                    try:
                        async with session.get(urls['head']+'/healthz') as r:
                            if r.status==200:break
                    except (aiohttp.ClientError,OSError):pass
                    if cam.poll() is not None:raise RuntimeError(Path(log.name).read_text())
                    await asyncio.sleep(.1)
                else:raise RuntimeError('Camera startup timeout: '+Path(log.name).read_text())
                from cryptography import x509
                cert=x509.load_pem_x509_certificate((Path(temp)/'cert.pem').read_bytes())
                self_ips=cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                assert '10.32.55.9' in str(self_ips)
                for name,url in urls.items():
                    async with session.get(url) as r:assert r.status==200 and 'Start' in await r.text()
                    async with session.get(url+'/snapshot.jpg') as r:raw=await r.read();assert r.status==200
                    im=Image.open(io.BytesIO(raw));assert im.size==((640,180) if name=='head' else (320,180))
                    if name=='head':
                        arr=np.array(im);assert np.mean(abs(arr[:,0:320].astype(float)-arr[:,320:640]))>1
                    print('PASS HTTPS + snapshot:',name,flush=True)
                    if not SKIP_WEBRTC:
                        pc=RTCPeerConnection(RTCConfiguration(iceServers=[]));peers.append(pc);track_future=asyncio.get_running_loop().create_future()
                        @pc.on('track')
                        def track(t):
                            if not track_future.done():track_future.set_result(t)
                        pc.addTransceiver('video',direction='recvonly');await pc.setLocalDescription(await pc.createOffer())
                        async with session.post(url+'/offer',json={'type':pc.localDescription.type,'sdp':pc.localDescription.sdp}) as r:
                            answer=await r.json();assert r.status==200,answer
                        await pc.setRemoteDescription(RTCSessionDescription(**answer))
                        t=await asyncio.wait_for(track_future,5)
                        for _ in range(3):frame=await asyncio.wait_for(t.recv(),10)
                        assert (frame.width,frame.height)==im.size
                        await pc.close()
                        print('PASS WebRTC decoded frames:',name,flush=True)
                    else:print('SKIPPED WebRTC transport (SKIP_WEBRTC=1)',flush=True)
                if not SKIP_ZMQ:
                    req=context.socket(zmq.REQ);sockets.append(req);req.connect(f"tcp://127.0.0.1:{config['config_port']}");req.send(b'config');assert req.poll(5000);roster=req.recv_json();assert set(roster)==set(urls)
                    for name,c in roster.items():
                        sub=context.socket(zmq.SUB);sockets.append(sub);sub.setsockopt(zmq.SUBSCRIBE,b'');sub.connect(f"tcp://127.0.0.1:{c['zmq_port']}")
                        assert sub.poll(5000);im=Image.open(io.BytesIO(sub.recv()));assert list(im.size)==list(reversed(c['image_shape']))
                    print('PASS ZMQ config + JPEG PUB/SUB, all cameras',flush=True)
                else:print('SKIPPED ZMQ network checks (SKIP_ZMQ=1)',flush=True)
                async with session.post(urls['head']+'/offer',json={'type':'invalid'}) as r:assert r.status==400
                async with session.get(urls['head']+'/snapshot.jpg') as r:before=np.array(Image.open(io.BytesIO(await r.read())))
                requests.post(f'http://127.0.0.1:{tp}/api/command',json={'payload':{'action':'arm'}},timeout=3).raise_for_status()
                requests.post(f'http://127.0.0.1:{tp}/api/command',json={'payload':{'action':'height','params':{'value':.35}}},timeout=3).raise_for_status()
                await asyncio.sleep(2)
                async with session.get(urls['head']+'/snapshot.jpg') as r:after=np.array(Image.open(io.BytesIO(await r.read())))
                assert np.mean(abs(before[35:-25].astype(float)-after[35:-25]))>1
                print('PASS scene follows authoritative twin height',flush=True)
                twin.terminate();twin.wait(timeout=5)
                for _ in range(60):
                    async with session.get(urls['head']+'/healthz') as r:
                        if r.status==503:break
                    await asyncio.sleep(.1)
                else:raise AssertionError('Missing stale indication')
                async with session.get(urls['head']+'/snapshot.jpg') as r:assert r.status==503
                print('PASS invalid offer + no stale snapshots after twin loss',flush=True)
        finally:
            for pc in peers:await pc.close()
            for p in (cam,twin):
                if p.poll() is None:p.terminate();p.wait(timeout=10)
            for s in sockets:s.close(0)
            context.term();log.close()
    print('CAMERA CHECKS PASSED; transport checks skipped' if SKIP_ZMQ or SKIP_WEBRTC else 'ALL CAMERA CHECKS PASSED')
if __name__=='__main__':asyncio.run(check())
