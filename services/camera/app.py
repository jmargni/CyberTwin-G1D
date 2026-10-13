"""CPU virtual camera publisher: HTTPS/WebRTC and Teleimager-style ZMQ JPEG.
Independent laboratory implementation, not the Unitree capture server.
"""
import asyncio, contextlib, io, json, os, ssl, threading, time
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
import aiohttp
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from av import VideoFrame
from PIL import Image
import numpy as np
import requests
import yaml
import zmq
from services.camera.renderer import CameraRenderer

ROOT=Path(__file__).resolve().parents[2]
TWIN_URL=os.getenv('CAMERA_TWIN_URL','http://127.0.0.1:3000').rstrip('/')
CONFIG=Path(os.getenv('CAMERA_CONFIG',str(ROOT/'config/teleimager_server.yaml')))
STALE_SECONDS=3

class Feed:
    def __init__(self):
        self.config=yaml.safe_load(CONFIG.read_text())
        self.roster=self.config['cameras']
        self.zmq_enabled=os.getenv('CAMERA_ENABLE_ZMQ','true').lower()=='true'
        for c in self.roster.values():c['enable_zmq']=self.zmq_enabled
        self.width=int(os.getenv('CAMERA_WIDTH','320'));self.height=int(os.getenv('CAMERA_HEIGHT','180'));self.fps=int(os.getenv('CAMERA_FPS','3'))
        for name,c in self.roster.items():c['image_shape']=[self.height,self.width*(2 if name=='head' else 1)];c['fps']=self.fps
        self.frames={};self.updated=0.;self.error='In attesa del twin';self.lock=threading.Lock();self.stop=threading.Event();self.thread=None
    def get(self,name):
        with self.lock:
            if name not in self.frames or time.monotonic()-self.updated>STALE_SECONDS:raise RuntimeError('Stato twin o immagini non disponibili')
            return self.frames[name]
    def status(self):
        with self.lock:
            age=time.monotonic()-self.updated if self.updated else None
            return {'ok':age is not None and age<STALE_SECONDS,'simulation':'kinematic CPU','error':self.error,'frame_age_seconds':round(age,2) if age is not None else None,'cameras':self.roster,'calibration':'virtual, not hardware calibrated'}
    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def run(self):
        context=zmq.Context();sockets=[];renderer=None
        try:
            rep=context.socket(zmq.REP) if self.zmq_enabled else None
            if rep is not None:rep.setsockopt(zmq.LINGER,0);rep.bind(f"tcp://0.0.0.0:{self.config['config_port']}");sockets.append(rep)
            pubs={}
            for name,c in (self.roster.items() if self.zmq_enabled else []):
                sock=context.socket(zmq.PUB);sock.setsockopt(zmq.LINGER,0);sock.setsockopt(zmq.SNDHWM,1);sock.bind(f"tcp://0.0.0.0:{c['zmq_port']}");pubs[name]=sock;sockets.append(sock)
            twin=SimpleNamespace(lock=threading.RLock(),robot={})
            renderer=CameraRenderer(twin)
            while not self.stop.is_set():
                started=time.monotonic()
                if rep is not None and rep.poll(0):rep.recv();rep.send_json(self.roster)
                try:
                    r=requests.get(TWIN_URL+'/api/state',timeout=2);r.raise_for_status()
                    with twin.lock:twin.robot=r.json()['robot']
                    eyes=[Image.open(io.BytesIO(renderer.frame(n,True))) for n in ('head_left','head_right')]
                    stereo=Image.new('RGB',(self.width*2,self.height));stereo.paste(eyes[0],(0,0));stereo.paste(eyes[1],(self.width,0))
                    buf=io.BytesIO();stereo.save(buf,'JPEG',quality=75)
                    frames={'head':buf.getvalue(),**{n:renderer.frame(n,True) for n in ('left_wrist','right_wrist')}}
                    with self.lock:self.frames=frames;self.updated=time.monotonic();self.error=None
                    for name,pub in pubs.items():pub.send(frames[name])
                except Exception as exc:
                    with self.lock:self.error=str(exc);self.updated=0
                # Respond to config requests even between rendering cycles.
                deadline=started+1/self.fps
                while not self.stop.is_set() and time.monotonic()<deadline:
                    if rep is not None and rep.poll(20):rep.recv();rep.send_json(self.roster)
        except Exception as exc:
            with self.lock:self.error=str(exc);self.updated=0
        finally:
            if renderer:renderer.close()
            for s in sockets:s.close()
            context.term()
    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=10)

class Track(VideoStreamTrack):
    def __init__(self,feed,name):super().__init__();self.feed=feed;self.name=name;self.started=time.monotonic();self.previous=0
    async def recv(self):
        await asyncio.sleep(max(0,1/self.feed.fps-(time.monotonic()-self.previous)))
        self.previous=time.monotonic()
        try:raw=self.feed.get(self.name)
        except RuntimeError:raise MediaStreamError
        frame=VideoFrame.from_ndarray(np.asarray(Image.open(io.BytesIO(raw)).convert('RGB')),format='rgb24')
        frame.pts=int((time.monotonic()-self.started)*90000);frame.time_base=Fraction(1,90000)
        return frame

def certificate(folder,host):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from datetime import datetime,timedelta,timezone
    import ipaddress
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    cert,key=folder/'cert.pem',folder/'key.pem'
    if not (cert.exists() and key.exists()):
        private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'G1D virtual cameras')])
        now=datetime.now(timezone.utc)
        names=[x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1')),x509.IPAddress(ipaddress.ip_address(host))]
        public=x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(private.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365)).add_extension(x509.SubjectAlternativeName(names),False).sign(private,hashes.SHA256())
        key.write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));key.chmod(0o600)
        cert.write_bytes(public.public_bytes(serialization.Encoding.PEM))
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key);return ctx

def make_app(feed,name):
    peers=set()
    async def index(request):return web.Response(text=(ROOT/'services/camera/index.html').read_text().replace('__CAMERA__',name),content_type='text/html')
    async def script(request):return web.FileResponse(ROOT/'services/camera/client.js')
    async def health(request):
        s=feed.status();return web.json_response(s,status=200 if s['ok'] else 503)
    async def snapshot(request):
        try:return web.Response(body=feed.get(name),content_type='image/jpeg',headers={'Cache-Control':'no-store'})
        except RuntimeError as exc:return web.json_response({'error':str(exc)},status=503)
    async def offer(request):
        origin=request.headers.get('Origin')
        if origin and origin!='https://'+request.host:return web.json_response({'error':'Origin non ammessa'},status=403)
        if len(peers)>=4:return web.json_response({'error':'Massimo 4 sessioni per camera'},status=429)
        try:
            feed.get(name);data=await request.json()
            if not isinstance(data,dict) or data.get('type')!='offer' or not isinstance(data.get('sdp'),str):raise ValueError('SDP offer richiesto')
        except RuntimeError as exc:return web.json_response({'error':str(exc)},status=503)
        except (ValueError,TypeError):return web.json_response({'error':'Offer non valida'},status=400)
        pc=RTCPeerConnection(RTCConfiguration(iceServers=[]));peers.add(pc)
        @pc.on('connectionstatechange')
        async def changed():
            if pc.connectionState in ('failed','closed','disconnected'):
                peers.discard(pc);await pc.close()
        async def expire():
            await asyncio.sleep(20)
            if pc.connectionState!='connected':peers.discard(pc);await pc.close()
        timer=asyncio.create_task(expire())
        @pc.on('connectionstatechange')
        async def stop_timer():
            if pc.connectionState in ('connected','closed','failed'):timer.cancel()
        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=data['sdp'],type=data['type']))
            pc.addTrack(Track(feed,name));await pc.setLocalDescription(await pc.createAnswer())
            return web.json_response({'sdp':pc.localDescription.sdp,'type':pc.localDescription.type})
        except Exception:
            timer.cancel();peers.discard(pc);await pc.close();return web.json_response({'error':'Negoziazione SDP fallita'},status=400)
    async def cleanup(app):await asyncio.gather(*(pc.close() for pc in list(peers)))
    app=web.Application(client_max_size=65536)
    app.router.add_get('/',index);app.router.add_get('/client.js',script);app.router.add_get('/healthz',health);app.router.add_get('/snapshot.jpg',snapshot);app.router.add_post('/offer',offer)
    app.on_cleanup.append(cleanup);return app

async def main():
    feed=Feed();ssl_ctx=certificate(os.getenv('CAMERA_CERT_DIR','/certs'),os.getenv('CAMERA_HOST_IP','10.32.55.9'));runners=[]
    feed.start()
    try:
        for name,c in feed.roster.items():
            runner=web.AppRunner(make_app(feed,name));await runner.setup();runners.append(runner)
            await web.TCPSite(runner,'0.0.0.0',c['webrtc_port'],ssl_context=ssl_ctx).start()
        await asyncio.Event().wait()
    finally:
        for r in runners:await r.cleanup()
        await asyncio.to_thread(feed.close)
if __name__=='__main__':
    try:asyncio.run(main())
    except KeyboardInterrupt:pass
