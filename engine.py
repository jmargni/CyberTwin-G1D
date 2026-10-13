"""Deterministic local cyber twin. No shell execution, DDS or physical robot I/O."""
import copy, json, math, threading, time, uuid, xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parent
LABELS={
 'unauthorized':'Comandi non autorizzati','parameters':'Manipolazione parametri',
 'scope':'Chiamate fuori scope','escalation':'Escalation privilegi',
 'replay':'Replay','spoofing':'Spoofing identity','sequence':'Sequence abuse','bypass':'Bypass del PEP'}
RULES={'unauthorized':'G1D-101','parameters':'G1D-102','scope':'G1D-103','escalation':'G1D-104','replay':'G1D-105','spoofing':'G1D-106','sequence':'G1D-107','bypass':'G1D-108'}
DESCRIPTIONS={
 'unauthorized':'Identità guest tenta un movimento riservato a operator.',
 'parameters':'Velocità AGV oltre il limite della policy del laboratorio.',
 'scope':'Richiesta indirizzata a un robot fuori dallo scope g1d-01.',
 'escalation':'Il payload richiede admin con una sessione operator.',
 'replay':'Riinvio dello stesso request_id dopo una prima richiesta valida.',
 'spoofing':'Identità dichiarata diversa dal principal del test.',
 'sequence':'Movimento durante lo stato disarmed.',
 'bypass':'Tentativo di accesso diretto al virtual endpoint senza passare dal PEP.'}
# Trajectory command (chunks of arm targets from the UnifoLM-WMA postino): lab limits, not Unitree specs.
TRAJ_MAX_POINTS=64
TRAJ_MAX_SPEED=3.0  # rad/s; the G1 "pack camera" training data peaks at about 2.3 rad/s
TRAJ_JOINT_KEYS=('shoulder','elbow','wrist')
# Start and home pose of the arms: the start of the G1 "pack camera" training episode, as robot_client.py and the
# MuJoCo simulator use it (all zeros would put the hands through the simulator's table).
ARM_START=dict(zip(('left_shoulder_pitch_joint','left_shoulder_roll_joint','left_shoulder_yaw_joint','left_elbow_joint','left_wrist_roll_joint','left_wrist_pitch_joint','left_wrist_yaw_joint','right_shoulder_pitch_joint','right_shoulder_roll_joint','right_shoulder_yaw_joint','right_elbow_joint','right_wrist_roll_joint','right_wrist_pitch_joint','right_wrist_yaw_joint'),
 (-0.50163078,0.32051945,0.18178487,0.6838522,-0.085745335,-0.51033354,-0.25062084,-0.40424705,-0.26256371,-0.20069599,0.21123028,0.1912303,-0.20726478,0.28874797)))
def finite(v):return not isinstance(v,bool) and isinstance(v,(float,int)) and math.isfinite(v)
class Twin:
    def __init__(self,log_dir=None):
        self.lock=threading.RLock(); self.log_dir=Path(log_dir) if log_dir else None
        if self.log_dir: self.log_dir.mkdir(parents=True,exist_ok=True)
        self.events=[];self.seen={};self.blocked=set();self.enforce=True
        self.joints={}
        for j in ET.parse(ROOT/'public/model/g1_d.urdf').getroot().findall('joint'):
            if j.get('type')=='fixed': continue
            lim=j.find('limit')
            self.joints[j.get('name')]={'min':float(lim.get('lower','-3.1416')),'max':float(lim.get('upper','3.1416')),'type':j.get('type')}
        self.reset_state()
        self.event('system','START','Twin avviato: virtual endpoint locale',severity='info')
    def reset_state(self):
        self.robot={'id':'g1d-01','mode':'disarmed','x':0.,'y':0.,'yaw':0.,'v':0.,'w':0.,'until':0.,'height':0.,'gripper':'open','joints':{k:ARM_START.get(k,0.) for k in self.joints}}
        self.last_tick=time.monotonic()
    def tick(self):
        now=time.monotonic();dt=min(now-self.last_tick,0.25);self.last_tick=now
        r=self.robot
        if now>=r['until'] or r['mode']!='armed':r['v']=r['w']=0.
        r['yaw']+=r['w']*dt;r['x']+=math.cos(r['yaw'])*r['v']*dt;r['y']+=math.sin(r['yaw'])*r['v']*dt
        for k,sign in [('Left_Wheel_Joint',-1),('Right_Wheel_Joint',1)]:
            r['joints'][k]+=sign*(r['v']+(-1 if sign<0 else 1)*r['w']*.2031)*dt/.1
    def event(self,category,decision,message,request=None,rule=None,severity='info'):
        ev={'timestamp':datetime.now(timezone.utc).isoformat(),'event_id':str(uuid.uuid4()),'source':'g1d-cyber-twin','robot_id':'g1d-01','category':category,'decision':decision,'message':message,'rule_id':rule or 'G1D-000','severity':severity,'simulation':True}
        if request is not None:ev['request']=copy.deepcopy(request)
        self.events.append(ev);self.events=self.events[-2000:]
        if self.log_dir:
            with (self.log_dir/'events.jsonl').open('a') as f:f.write(json.dumps(ev,ensure_ascii=False)+'\n')
        return ev
    def snapshot(self):
        with self.lock:
            self.tick()
            return {'robot':copy.deepcopy(self.robot),'enforce':self.enforce,'blocked':sorted(self.blocked),'events':copy.deepcopy(self.events[-200:]),'counts':{x:sum(e['decision']==x for e in self.events) for x in ['ALLOW','DENY','MONITOR','DEFENSE']},'joint_limits':self.joints,'catalog':[{'id':k,'title':v,'description':DESCRIPTIONS[k],'rule':RULES[k]} for k,v in LABELS.items()]}
    def trajectory_error(self,params):
        """Check a trajectory: ValueError if malformed, a reason string if it breaks a lab limit, None if valid.
        params: joints (arm joint names), points (list of target lists, one value per joint), dt (s between points),
        optional start (measured joint values before the first point) and gripper ('open'/'closed' at the end)."""
        names,points,dt,start=params.get('joints'),params.get('points'),params.get('dt'),params.get('start')
        if not isinstance(names,list) or not names or not all(isinstance(n,str) for n in names) or len(set(names))!=len(names):raise ValueError('joints: lista di nomi distinti richiesta')
        if not isinstance(points,list) or not 1<=len(points)<=TRAJ_MAX_POINTS or not all(isinstance(q,list) and len(q)==len(names) for q in points):raise ValueError(f'points: 1..{TRAJ_MAX_POINTS} liste con un valore per joint')
        if start is not None and not (isinstance(start,list) and len(start)==len(names)):raise ValueError('start: un valore per joint')
        if not all(finite(v) for v in [dt,*(start or []),*(v for q in points for v in q)]):raise ValueError('trajectory: numeri finiti richiesti')
        if params.get('gripper') not in (None,'open','closed'):raise ValueError('gripper: open oppure closed')
        if not 0<dt<=1:return 'Traiettoria: dt fuori da (0, 1] s'
        for n in names:
            if n not in self.joints or not any(k in n for k in TRAJ_JOINT_KEYS):return 'Traiettoria: joint non ammesso '+n
        prev=start if start is not None else [self.robot['joints'][n] for n in names]
        for i,q in enumerate(points):
            for n,v,v0 in zip(names,q,prev):
                if not self.joints[n]['min']<=v<=self.joints[n]['max']:return f'Traiettoria: {n} oltre il limite URDF al punto {i}'
                if abs(v-v0)>TRAJ_MAX_SPEED*dt:return f'Traiettoria: {n} oltre {TRAJ_MAX_SPEED} rad/s al punto {i}'
            prev=q
        return None
    def set_gripper(self,state):
        r=self.robot;r['gripper']=state
        for k,lim in self.joints.items():
            if 'hand_' in k:r['joints'][k]=max(lim['min'],min(lim['max'],.55 if state=='closed' else 0))
    def command(self,payload,principal='operator',category='command'):
        with self.lock:
            self.tick()
            if not isinstance(payload,dict):raise ValueError('Il comando deve essere un oggetto JSON')
            p=copy.deepcopy(payload);p.setdefault('request_id',str(uuid.uuid4()));p.setdefault('timestamp',time.time());p.setdefault('identity',principal);p.setdefault('target','g1d-01');p.setdefault('via','pep')
            action=p.get('action');params=p.get('params',{})
            if not isinstance(action,str) or not isinstance(params,dict):raise ValueError('action stringa e params oggetto richiesti')
            if principal not in ('operator','guest','defender'):raise ValueError('Principal di laboratorio non valido')
            if not isinstance(p['request_id'],str) or not 1<=len(p['request_id'])<=128:raise ValueError('request_id non valido')
            if isinstance(p['timestamp'],bool) or not isinstance(p['timestamp'],(float,int)) or not math.isfinite(p['timestamp']):raise ValueError('timestamp non valido')
            def num(key,default=0):
                v=params.get(key,default)
                if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v):raise ValueError(key+' deve essere un numero finito')
                return v
            known={'arm','disarm','stop','estop','move','height','joint','gripper','home','status','trajectory'}
            violation=None;message='Policy rispettata'
            if p['via']!='pep':violation='bypass';message='Virtual endpoint accetta solo richieste mediate dal PEP'
            elif p['identity']!=principal:violation='spoofing';message='Identità dichiarata non corrisponde al principal del test'
            elif principal in self.blocked or (principal=='guest' and action!='status'):violation='unauthorized';message='Principal senza autorizzazione o revocato'
            elif p.get('role',principal)!=principal:violation='escalation';message='Il ruolo non può essere elevato dal payload'
            elif p['target']!='g1d-01' or action not in known:violation='scope';message='Target o azione fuori scope'
            elif p['request_id'] in self.seen or abs(time.time()-p['timestamp'])>30:violation='replay';message='Request ID duplicato o timestamp scaduto'
            # Validate every numeric action even in monitor mode.
            invalid=False;detail=None
            if action=='move':invalid=abs(num('v'))>.5 or abs(num('w'))>1 or not 0<num('duration',1)<=5
            elif action=='height':invalid=not 0<=num('value')<=.42
            elif action=='joint':
                j=params.get('name');v=num('value')
                invalid=j not in self.joints or (j in self.joints and not self.joints[j]['min']<=v<=self.joints[j]['max'])
            elif action=='gripper':invalid=params.get('state') not in ('open','closed')
            elif action=='trajectory':detail=self.trajectory_error(params);invalid=detail is not None
            if not violation and invalid:violation='parameters';message=detail or 'Parametri oltre i limiti del lab o del modello URDF'
            if not violation and (action in ('move','height','joint','gripper','home','trajectory') and self.robot['mode']!='armed'):
                violation='sequence';message='Sequenza non valida: arm richiesto prima del movimento'
            if not violation and self.robot['mode']=='estop' and action not in ('status','stop','estop'):
                violation='sequence';message='E-stop attivo: ripristino richiesto dalla console Defense'
            self.seen[p['request_id']]=time.time()
            self.seen={k:v for k,v in self.seen.items() if time.time()-v<=60}
            if violation:
                # Monitor records a would-deny decision but never applies a failed action.
                return self.event(category,'DENY' if self.enforce else 'MONITOR',message,p,RULES[violation],'high')
            r=self.robot
            if action=='arm':r['mode']='armed'
            elif action=='disarm':r['mode']='disarmed';r['v']=r['w']=0
            elif action in ('stop','estop'):
                r['v']=r['w']=0
                if action=='estop':r['mode']='estop'
            elif action=='move':r['v']=num('v');r['w']=num('w');r['until']=time.monotonic()+num('duration',1)
            elif action=='height':
                r['height']=num('value');r['joints']['LZ_mt_Joint']=r['joints']['LZ_it_Joint']=r['height']/2
            elif action=='joint':
                r['joints'][params['name']]=num('value')
                r['height']=r['joints']['LZ_mt_Joint']+r['joints']['LZ_it_Joint']
            elif action=='gripper':self.set_gripper(params['state'])
            elif action=='trajectory':
                # The twin mirrors the end of the validated chunk; the physical simulator executes it.
                for n,v in zip(params['joints'],params['points'][-1]):r['joints'][n]=float(v)
                if params.get('gripper'):self.set_gripper(params['gripper'])
            elif action=='home':
                for k in r['joints']:r['joints'][k]=ARM_START.get(k,0.)
                r['height']=0.;r['gripper']='open'
            return self.event(category,'ALLOW','Eseguito: '+action,p,'G1D-200')
    def attack(self,kind):
        with self.lock:
            if kind not in LABELS:raise ValueError('Scenario sconosciuto')
            p={'action':'move','params':{'v':.2,'duration':1}};principal='operator'
            # Each fixture isolates the requested policy dimension; setup has no motion.
            if kind=='unauthorized':principal='guest'
            elif kind=='parameters':p['params']['v']=5
            elif kind=='scope':p['target']='g1d-02'
            elif kind=='escalation':p['role']='admin'
            elif kind=='spoofing':p['identity']='defender'
            elif kind=='bypass':p['via']='direct-dds'
            elif kind=='replay':
                p={'action':'status','request_id':str(uuid.uuid4())};self.command(p,category='replay-setup')
            elif kind=='sequence':
                # The fixture explicitly disarms; both consoles see the change.
                self.robot['mode']='disarmed';self.robot['v']=self.robot['w']=0
                self.event('scenario-setup','INFO','Sequence abuse: twin portato in disarmed')
            return self.command(p,principal,kind)
    def defense(self,action,value=None):
        with self.lock:
            if action=='estop':self.robot['mode']='estop';self.robot['v']=self.robot['w']=0
            elif action=='reset':self.reset_state()
            elif action=='enforce':
                if not isinstance(value,bool):raise ValueError('value booleano richiesto')
                self.enforce=value
            elif action=='revoke':
                if value not in ('operator','guest'):raise ValueError('Identità non valida')
                self.blocked.add(value)
            elif action=='restore':self.blocked.clear()
            else:raise ValueError('Azione Defense sconosciuta')
            return self.event('defense','DEFENSE',action+': '+str(value),severity='medium')
    def suite(self):
        with self.lock:
            saved=copy.deepcopy(self.robot);blocked=self.blocked.copy();enforce=self.enforce
            self.blocked.clear();self.enforce=True;results=[]
            try:
                for k in LABELS:
                    self.reset_state();ev=self.attack(k)
                    results.append({'scenario':k,'expected':RULES[k],'actual':ev['rule_id'],'passed':ev['rule_id']==RULES[k] and ev['decision']=='DENY'})
                self.reset_state();ev=self.command({'action':'arm'})
                results.append({'scenario':'authorized','expected':'ALLOW','actual':ev['decision'],'passed':ev['decision']=='ALLOW'})
            finally:
                self.robot=saved;self.robot['v']=self.robot['w']=0;self.blocked=blocked;self.enforce=enforce
            self.event('test-suite','INFO',f"Test completati: {sum(r['passed'] for r in results)}/{len(results)}")
            return results
