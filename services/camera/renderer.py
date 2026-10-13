"""CPU-only URDF camera. PyBullet DIRECT + TinyRenderer; no CUDA/OpenGL context.
Kinematic twin states drive links. No dynamic physics or hardware sensor emulation.
"""
import copy, io, math, os, threading, time, tempfile, xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
import pybullet as p
ROOT=Path(__file__).resolve().parents[2]
CAMERAS=('head_left','head_right','left_wrist','right_wrist','overview')
def setting(name,default,lo,hi):
    n=int(os.environ.get(name,default))
    if not lo<=n<=hi:raise ValueError(f'{name}: range {lo}..{hi}')
    return n
class CameraRenderer:
    def __init__(self,twin):
        self.twin=twin;self.width=setting('CAMERA_WIDTH',320,160,1280);self.height=setting('CAMERA_HEIGHT',180,120,720)
        self.fps=setting('CAMERA_FPS',3,1,20);self.quality=setting('CAMERA_JPEG_QUALITY',75,30,95)
        self.lock=threading.RLock();self.cache={};self.frames=0;self.render_ms=0.;self.closed=False
        self.client=p.connect(p.DIRECT)
        try:self._load()
        except Exception:p.disconnect(self.client);raise
    def _load(self):
        model=ROOT/'public/model';tree=ET.parse(model/'g1_d.urdf');root=tree.getroot()
        # Collision geometry is unnecessary for this explicitly kinematic renderer.
        for link in root.findall('link'):
            for collision in link.findall('collision'):link.remove(collision)
        for m in root.iter('mesh'):m.set('filename',str(model/m.get('filename')))
        for m in root.findall('mujoco'):root.remove(m)
        fd,name=tempfile.mkstemp(suffix='.urdf');os.close(fd)
        try:
            tree.write(name)
            self.robot=p.loadURDF(name,useFixedBase=True,flags=p.URDF_USE_INERTIA_FROM_FILE,physicsClientId=self.client)
        finally:Path(name).unlink(missing_ok=True)
        self.joints={};self.links={}
        for i in range(p.getNumJoints(self.robot,physicsClientId=self.client)):
            j=p.getJointInfo(self.robot,i,physicsClientId=self.client);self.joints[j[1].decode()]=i;self.links[j[12].decode()]=i
        # Ground the original STL base and wheels, keeping the official dimensions.
        def min_z(file):
            raw=(model/'meshes'/file).read_bytes();n=int.from_bytes(raw[80:84],'little')
            records=np.frombuffer(raw,offset=84,count=n,dtype=np.dtype([('n','<f4',(3,)),('v','<f4',(3,3)),('a','<u2')]))
            return float(records['v'][:,:,2].min())
        self.lift=-min(min_z('AGV_link.STL'),min_z('Left_Wheel_Link.STL')-.0265,min_z('RIght_Wheel_Link.STL')-.0265)+.004
        for name,i in self.links.items():
            color=[.13,.16,.20,1] if any(s in name for s in ('head','Wheel','hand_','logo','Pitching','Yaw_Link')) else [.76,.79,.83,1]
            p.changeVisualShape(self.robot,i,rgbaColor=color,physicsClientId=self.client)
        self._box([8,8,.02],[0,0,-.025],[.2,.25,.31,1])
        for v in range(-6,7):
            self._box([.004,6,.002],[v,0,0],[.35,.41,.46,1]);self._box([6,.004,.002],[0,v,0],[.35,.41,.46,1])
        self._box([.45,.85,.04],[1.5,0,.70],[.5,.56,.6,1])
        for x in (1.12,1.88):
            for y in (-.76,.76):self._box([.035,.035,.34],[x,y,.34],[.25,.28,.32,1])
        for x,y,color in [(1.4,-.4,[.85,.22,.22,1]),(1.65,0,[.22,.65,.38,1]),(1.4,.4,[.23,.43,.86,1])]:self._box([.09,.09,.09],[x,y,.83],color)
        self._box([.04,3,1.4],[3.5,0,1.4],[.56,.62,.68,1])
        self._box([.03,.6,.4],[3.44,0,1.1],[.15,.2,.25,1])
        self._box([.2,.2,.4],[-1,1.7,.4],[.76,.59,.22,1])
        self.projection=p.computeProjectionMatrixFOV(68,self.width/self.height,.03,25)
    def _box(self,size,pos,color):
        visual=p.createVisualShape(p.GEOM_BOX,halfExtents=size,rgbaColor=color,physicsClientId=self.client)
        return p.createMultiBody(baseMass=0,baseVisualShapeIndex=visual,basePosition=pos,physicsClientId=self.client)
    def _pose(self,r):
        p.resetBasePositionAndOrientation(self.robot,[r['x'],r['y'],self.lift],p.getQuaternionFromEuler([0,0,r['yaw']]),physicsClientId=self.client)
        for name,v in r['joints'].items():
            if name in self.joints:p.resetJointState(self.robot,self.joints[name],v,physicsClientId=self.client)
    def _view(self,name,r):
        if name=='overview':
            return p.computeViewMatrix([r['x']+1.5,r['y']+1.5,1.35],[r['x'],r['y'],.72+r['height']/2],[0,0,1])
        link='head_link' if name.startswith('head_') else name+'_yaw_link'
        state=p.getLinkState(self.robot,self.links[link],computeForwardKinematics=True,physicsClientId=self.client)
        pos,quat=state[4],state[5]
        offset=[.15,.03 if name=='head_left' else -.03,.46] if name.startswith('head_') else [.035,0,.10]
        eye,_=p.multiplyTransforms(pos,quat,offset,[0,0,0,1])
        direction=[1,0,-.25] if name.startswith('head_') else [1,0,-.4]
        target,_=p.multiplyTransforms(eye,quat,direction,[0,0,0,1])
        rot=np.array(p.getMatrixFromQuaternion(quat)).reshape(3,3);up=rot@np.array([0,0,1])
        return p.computeViewMatrix(eye,target,up.tolist())
    def frame(self,name='head_left',force=False):
        if name not in CAMERAS:raise ValueError('Camera: '+', '.join(CAMERAS))
        with self.lock:
            if self.closed:raise RuntimeError('Camera chiusa')
            now=time.monotonic();cached=self.cache.get(name)
            if cached and not force and now-cached[0]<1/self.fps:return cached[1]
            with self.twin.lock:r=copy.deepcopy(self.twin.robot)
            start=time.perf_counter();self._pose(r)
            result=p.getCameraImage(self.width,self.height,self._view(name,r),self.projection,renderer=p.ER_TINY_RENDERER,shadow=0,lightDirection=[-2,-3,5],lightAmbientCoeff=.6,lightDiffuseCoeff=.5,flags=p.ER_NO_SEGMENTATION_MASK,physicsClientId=self.client)
            img=Image.fromarray(np.asarray(result[2],dtype=np.uint8).reshape(self.height,self.width,4)[:,:,:3])
            draw=ImageDraw.Draw(img);draw.rectangle((0,0,self.width,29),fill=(12,18,26));draw.text((8,8),f'G1-D / {name} / CAMERA VIRTUALE CPU',fill=(190,237,121))
            draw.rectangle((0,self.height-22,self.width,self.height),fill=(12,18,26));draw.text((8,self.height-16),f"{r['mode']} | x {r['x']:.2f} y {r['y']:.2f} | {time.strftime('%H:%M:%S',time.gmtime())} UTC",fill=(225,235,245))
            buf=io.BytesIO();img.save(buf,'JPEG',quality=self.quality)
            self.frames+=1;self.render_ms=(time.perf_counter()-start)*1000
            data=buf.getvalue();self.cache[name]=(time.monotonic(),data);return data
    def status(self):
        return {'renderer':'PyBullet DIRECT / TinyRenderer CPU','simulation':'kinematic','cameras':list(CAMERAS),'width':self.width,'height':self.height,'target_fps':self.fps,'frames':self.frames,'last_render_ms':round(self.render_ms,1),'gpu_required':False,'camera_calibration':'virtual lab placement, not hardware calibrated'}
    def close(self):
        with self.lock:
            if not self.closed:p.disconnect(self.client);self.closed=True
