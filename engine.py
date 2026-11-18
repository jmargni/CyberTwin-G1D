"""Deterministic local cyber twin. No shell execution, DDS or physical robot I/O."""
import copy, hashlib, json, math, os, threading, time, uuid, xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parent
LABELS={
 'unauthorized':'Comandi non autorizzati','parameters':'Manipolazione parametri',
 'scope':'Chiamate fuori scope','escalation':'Escalation privilegi',
 'replay':'Replay','spoofing':'Spoofing identity','sequence':'Sequence abuse','bypass':'Bypass del PEP',
 'geofence':'Geofence / zone interdette','rate':'Flooding / DoS','lockout':'Brute force / probing'}
DESCRIPTIONS={
 'unauthorized':'Identità guest tenta un movimento riservato a operator.',
 'parameters':'Velocità AGV oltre il limite della policy del laboratorio.',
 'scope':'Richiesta indirizzata a un robot fuori dallo scope g1d-01.',
 'escalation':'Il payload richiede admin con una sessione operator.',
 'replay':'Riinvio dello stesso request_id dopo una prima richiesta valida.',
 'spoofing':'Identità dichiarata diversa dal principal del test.',
 'sequence':'Movimento durante lo stato disarmed.',
 'bypass':'Tentativo di accesso diretto al virtual endpoint senza passare dal PEP.',
 'geofence':'Comando move che porterebbe la base fuori dal geofence o dentro una zona interdetta.',
 'rate':'Raffica di comandi oltre il limite al secondo (DoS sul canale comandi). Stop/E-stop mai limitati.',
 'lockout':'Violazioni di sicurezza ripetute: identità messa in quarantena (revocata). Richiede Restore dalla console Defense.'}
# Best-effort MITRE ATT&CK mapping (ICS T0xxx / Enterprise T1xxx), for the consoles. See docs/POLICIES.md.
ATTACK={
 'unauthorized':'T0855 Unauthorized Command Message','parameters':'T0836 Modify Parameter',
 'scope':'T0855 Unauthorized Command Message','escalation':'T1548 Abuse Elevation Control Mechanism',
 'replay':'T0830 Adversary-in-the-Middle','spoofing':'T1036 Masquerading',
 'sequence':'T0831 Manipulation of Control','bypass':'T1562 Impair Defenses',
 'geofence':'T0831 Manipulation of Control','rate':'T0814 Denial of Service','lockout':'T1110 Brute Force'}
# Version stamps for the correlated audit trail (RFI 2.8-C). Stubbed in sim; real values come from the robot.
MODEL_VERSION='unifolm-wma (sim)'
FIRMWARE_VERSION='g1d-sim'
# PDP output: the limits/allow-lists the local PEP enforces. Loaded from policy.json (central governance) if
# present, else these built-in defaults — "policy defined centrally, enforced locally" (RFI 2.7 / method D2).
# Risk-raising defense actions: they clear an E-stop and move the twin back to start (reset), switch the guardrail
# to monitor (enforce) or give back revoked identities (restore), so they pass the PEP like any command and only the
# defender may issue them. Risk-reducing ones (estop, revoke) stay on the direct defense channel.
PRIVILEGED=('reset','enforce','restore')
# One entry per rule: its id and parameters. The rule logic and the evaluation order live in Twin.command().
# Trajectory limits are lab limits, not Unitree specs (the G1 "pack camera" training data peaks near 2.3 rad/s).
# Geofence is base motion only: workspace box in metres from origin, plus named keep-out zones (zone interdette).
DEFAULT_POLICY={'version':'builtin','principals':['operator','guest','defender'],'rules':{
 'bypass':{'id':'G1D-108','required_via':'pep'},
 'spoofing':{'id':'G1D-106'},
 'unauthorized':{'id':'G1D-101','guest_actions':['status'],'privileged_actions':list(PRIVILEGED),'privileged_principal':'defender'},
 'escalation':{'id':'G1D-104'},
 'scope':{'id':'G1D-103','targets':['g1d-01'],
  'actions':['arm','disarm','stop','estop','move','height','joint','gripper','home','status','trajectory',*PRIVILEGED]},
 'replay':{'id':'G1D-105','max_age_s':30,'motion_max_age_s':5,'window_s':60},
 'parameters':{'id':'G1D-102','move':{'v_max':0.5,'w_max':1.0,'duration_max':5.0},'height_max':0.42,
  'trajectory':{'max_points':64,'max_speed':3.0,'max_dt':1.0,'joint_keys':['shoulder','elbow','wrist']}},
 'sequence':{'id':'G1D-107','motion_actions':['move','height','joint','gripper','home','trajectory'],
  'estop_allowed':['status','stop','estop',*PRIVILEGED]},
 'geofence':{'id':'G1D-109','box':{'xmin':-2.0,'xmax':2.0,'ymin':-2.0,'ymax':2.0},
  'keepout':[{'name':'area-tecnica','xmin':0.5,'xmax':1.5,'ymin':-0.5,'ymax':0.5}]},
 # Availability: commands per principal per second; risk-reducing actions are never rate-limited.
 'rate':{'id':'G1D-110','max_per_s':20,'exempt_actions':['stop','estop']},
 # Quarantine: N security violations (not safety ones) in a window revoke the principal until a defender Restore.
 'lockout':{'id':'G1D-111','max_violations':5,'window_s':60,
  'counted_rules':['bypass','spoofing','unauthorized','escalation','scope','replay','rate'],'exempt_principals':['defender']}}}
RULES={k:v['id'] for k,v in DEFAULT_POLICY['rules'].items()}  # built-in ids; a running Twin uses self.R
def load_policy(path=None):
    """Built-in defaults overlaid, rule by rule, with policy.json (or $TWIN_POLICY, e.g. a lab policy).
    An unknown rule name is an error (typo guard)."""
    path=Path(path or os.getenv('TWIN_POLICY') or ROOT/'policy.json')
    p=json.loads(path.read_text()) if path.exists() else {}
    merged=copy.deepcopy(DEFAULT_POLICY);rules=p.pop('rules',{});merged.update(p)
    for k,v in rules.items():
        if k not in merged['rules']:raise ValueError('policy.json: regola sconosciuta '+k)
        merged['rules'][k].update(v)
    return merged
# Start and home pose of the arms: the start of the G1 "pack camera" training episode, as robot_client.py and the
# MuJoCo simulator use it (all zeros would put the hands through the simulator's table).
ARM_START=dict(zip(('left_shoulder_pitch_joint','left_shoulder_roll_joint','left_shoulder_yaw_joint','left_elbow_joint','left_wrist_roll_joint','left_wrist_pitch_joint','left_wrist_yaw_joint','right_shoulder_pitch_joint','right_shoulder_roll_joint','right_shoulder_yaw_joint','right_elbow_joint','right_wrist_roll_joint','right_wrist_pitch_joint','right_wrist_yaw_joint'),
 (-0.50163078,0.32051945,0.18178487,0.6838522,-0.085745335,-0.51033354,-0.25062084,-0.40424705,-0.26256371,-0.20069599,0.21123028,0.1912303,-0.20726478,0.28874797)))
GENESIS='0'*64
def event_hash(ev):return hashlib.sha256(json.dumps({k:v for k,v in ev.items() if k!='hash'},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def last_hash(path):
    try:
        with open(path,'rb') as f:
            f.seek(0,2);f.seek(max(0,f.tell()-262144));return json.loads(f.read().splitlines()[-1]).get('hash',GENESIS)
    except (OSError,IndexError,ValueError):return GENESIS
def verify_log(path):
    """Check the events.jsonl hash chain. Returns (events checked, None) or (line number, reason) at the first break."""
    prev=None;n=0
    for i,line in enumerate(Path(path).read_text().splitlines(),1):
        if not line.strip():continue
        ev=json.loads(line)
        if 'hash' not in ev:continue  # written before the chain existed
        if event_hash(ev)!=ev['hash']:return i,'evento modificato'
        if prev is not None and ev['prev_hash']!=prev:return i,'catena interrotta (evento rimosso o inserito)'
        prev=ev['hash'];n+=1
    return n,None
def finite(v):return not isinstance(v,bool) and isinstance(v,(float,int)) and math.isfinite(v)
class Twin:
    def __init__(self,log_dir=None):
        self.lock=threading.RLock(); self.log_dir=Path(log_dir) if log_dir else None
        if self.log_dir: self.log_dir.mkdir(parents=True,exist_ok=True)
        self.events=[];self.seen={};self.blocked=set();self.enforce=True;self.rate={};self.strikes={}
        self.session_id=str(uuid.uuid4());self.policy=load_policy()
        # Evidence of tampering: the effective policy's digest goes on every event, and events form a hash chain
        # (each carries the previous event's hash), continued across restarts from the last line of the log.
        self.policy_sha256=hashlib.sha256(json.dumps(self.policy,sort_keys=True).encode()).hexdigest()
        self.last_hash=last_hash(self.log_dir/'events.jsonl') if self.log_dir else GENESIS
        self.R=self.policy['rules']
        self.geofence=dict(self.R['geofence']['box']);self.keepout=[dict(z) for z in self.R['geofence']['keepout']]
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
        # Safety envelope: halt the base if it reaches the geofence, even if admission missed it.
        if self.geofence_breach(r['x'],r['y']):r['v']=r['w']=0.
    def event(self,category,decision,message,request=None,rule=None,severity='info',human_override=False):
        ev={'timestamp':datetime.now(timezone.utc).isoformat(),'event_id':str(uuid.uuid4()),'source':'g1d-cyber-twin','robot_id':'g1d-01','session_id':self.session_id,'category':category,'decision':decision,'message':message,'rule_id':rule or 'G1D-000','severity':severity,'simulation':True,'safety_state':self.robot['mode'] if getattr(self,'robot',None) else 'init','human_override':human_override,'model_version':MODEL_VERSION,'firmware_version':FIRMWARE_VERSION,'policy_version':self.policy['version'],'policy_sha256':self.policy_sha256}
        if request is not None:ev['request']=copy.deepcopy(request)
        ev['prev_hash']=self.last_hash;ev['hash']=self.last_hash=event_hash(ev)
        self.events.append(ev);self.events=self.events[-2000:]
        if self.log_dir:
            with (self.log_dir/'events.jsonl').open('a') as f:f.write(json.dumps(ev,ensure_ascii=False)+'\n')
        return ev
    def snapshot(self):
        with self.lock:
            self.tick()
            return {'robot':copy.deepcopy(self.robot),'enforce':self.enforce,'blocked':sorted(self.blocked),'events':copy.deepcopy(self.events[-200:]),'counts':{x:sum(e['decision']==x for e in self.events) for x in ['ALLOW','DENY','MONITOR','DEFENSE']},'joint_limits':self.joints,'geofence':self.geofence,'keepout':self.keepout,'policy_version':self.policy['version'],'catalog':[{'id':k,'title':v,'description':DESCRIPTIONS[k],'rule':self.R[k]['id'],'attack':ATTACK[k]} for k,v in LABELS.items()]}
    def trajectory_error(self,params):
        """Check a trajectory: ValueError if malformed, a reason string if it breaks a lab limit, None if valid.
        params: joints (arm joint names), points (list of target lists, one value per joint), dt (s between points),
        optional start (measured joint values before the first point) and gripper ('open'/'closed' at the end)."""
        t=self.R['parameters']['trajectory'];mp,ms=t['max_points'],t['max_speed']
        names,points,dt,start=params.get('joints'),params.get('points'),params.get('dt'),params.get('start')
        if not isinstance(names,list) or not names or not all(isinstance(n,str) for n in names) or len(set(names))!=len(names):raise ValueError('joints: lista di nomi distinti richiesta')
        if not isinstance(points,list) or not 1<=len(points)<=mp or not all(isinstance(q,list) and len(q)==len(names) for q in points):raise ValueError(f'points: 1..{mp} liste con un valore per joint')
        if start is not None and not (isinstance(start,list) and len(start)==len(names)):raise ValueError('start: un valore per joint')
        if not all(finite(v) for v in [dt,*(start or []),*(v for q in points for v in q)]):raise ValueError('trajectory: numeri finiti richiesti')
        if params.get('gripper') not in (None,'open','closed'):raise ValueError('gripper: open oppure closed')
        if not 0<dt<=t['max_dt']:return f"Traiettoria: dt fuori da (0, {t['max_dt']}] s"
        for n in names:
            if n not in self.joints or not any(k in n for k in t['joint_keys']):return 'Traiettoria: joint non ammesso '+n
        prev=start if start is not None else [self.robot['joints'][n] for n in names]
        for i,q in enumerate(points):
            for n,v,v0 in zip(names,q,prev):
                if not self.joints[n]['min']<=v<=self.joints[n]['max']:return f'Traiettoria: {n} oltre il limite URDF al punto {i}'
                if abs(v-v0)>ms*dt:return f'Traiettoria: {n} oltre {ms} rad/s al punto {i}'
            prev=q
        return None
    def geofence_breach(self,x,y):
        """Reason string if (x,y) is outside the geofence box or inside a keep-out zone, else None."""
        f=self.geofence
        if not (f['xmin']<=x<=f['xmax'] and f['ymin']<=y<=f['ymax']):return f'base ({x:.2f},{y:.2f}) fuori dal geofence'
        for z in self.keepout:
            if z['xmin']<=x<=z['xmax'] and z['ymin']<=y<=z['ymax']:return f"base ({x:.2f},{y:.2f}) in zona interdetta {z.get('name','')}".rstrip()
        return None
    def move_would_breach(self,v,w,duration):
        """Integrate the requested base move forward (as tick does); return the first geofence breach, else None."""
        x,y,yaw=self.robot['x'],self.robot['y'],self.robot['yaw'];t=0.
        while t<duration:
            dt=min(.1,duration-t);yaw+=w*dt;x+=math.cos(yaw)*v*dt;y+=math.sin(yaw)*v*dt;t+=dt
            b=self.geofence_breach(x,y)
            if b:return b
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
            if principal not in self.policy['principals']:raise ValueError('Principal di laboratorio non valido')
            if not isinstance(p['request_id'],str) or not 1<=len(p['request_id'])<=128:raise ValueError('request_id non valido')
            if isinstance(p['timestamp'],bool) or not isinstance(p['timestamp'],(float,int)) or not math.isfinite(p['timestamp']):raise ValueError('timestamp non valido')
            def num(key,default=0):
                v=params.get(key,default)
                if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v):raise ValueError(key+' deve essere un numero finito')
                return v
            R=self.R;ua=R['unauthorized'];now=time.monotonic()
            limited=action not in R['rate']['exempt_actions']
            hist=[t for t in self.rate.get(principal,[]) if now-t<1.0]+([now] if limited else []);self.rate[principal]=hist
            age=abs(time.time()-p['timestamp'])
            violation=None;message='Policy rispettata'
            # Evaluation order: first match wins. Parameters come from policy.json (see load_policy).
            if p['via']!=R['bypass']['required_via']:violation='bypass';message='Virtual endpoint accetta solo richieste mediate dal PEP'
            elif limited and len(hist)>R['rate']['max_per_s']:violation='rate';message=f"Flooding: oltre {R['rate']['max_per_s']} comandi/s da {principal}"
            elif p['identity']!=principal:violation='spoofing';message='Identità dichiarata non corrisponde al principal del test'
            elif principal in self.blocked or (principal=='guest' and action not in ua['guest_actions']):violation='unauthorized';message='Principal senza autorizzazione o revocato'
            elif action in ua['privileged_actions'] and principal!=ua['privileged_principal']:violation='unauthorized';message='Azione riservata al '+ua['privileged_principal']+': '+action
            elif p.get('role',principal)!=principal:violation='escalation';message='Il ruolo non può essere elevato dal payload'
            elif p['target'] not in R['scope']['targets'] or action not in R['scope']['actions']:violation='scope';message='Target o azione fuori scope'
            elif p['request_id'] in self.seen or age>R['replay']['max_age_s']:violation='replay';message='Request ID duplicato o timestamp scaduto'
            elif action in R['sequence']['motion_actions'] and age>R['replay']['motion_max_age_s']:violation='replay';message=f"Comando di movimento non fresco ({age:.1f} s, max {R['replay']['motion_max_age_s']} s)"
            # Validate every numeric action even in monitor mode.
            invalid=False;detail=None
            if action=='move':m=R['parameters']['move'];invalid=abs(num('v'))>m['v_max'] or abs(num('w'))>m['w_max'] or not 0<num('duration',1)<=m['duration_max']
            elif action=='height':invalid=not 0<=num('value')<=R['parameters']['height_max']
            elif action=='joint':
                j=params.get('name');v=num('value')
                invalid=j not in self.joints or (j in self.joints and not self.joints[j]['min']<=v<=self.joints[j]['max'])
            elif action=='gripper':invalid=params.get('state') not in ('open','closed')
            elif action=='trajectory':detail=self.trajectory_error(params);invalid=detail is not None
            elif action=='enforce':invalid=not isinstance(params.get('value'),bool)
            if not violation and invalid:violation='parameters';message=detail or 'Parametri oltre i limiti del lab o del modello URDF'
            if not violation and (action in R['sequence']['motion_actions'] and self.robot['mode']!='armed'):
                violation='sequence';message='Sequenza non valida: arm richiesto prima del movimento'
            if not violation and self.robot['mode']=='estop' and action not in R['sequence']['estop_allowed']:
                violation='sequence';message='E-stop attivo: ripristino richiesto dalla console Defense'
            if not violation and action=='move':
                gb=self.move_would_breach(num('v'),num('w'),num('duration',1))
                if gb:violation='geofence';message=gb
            lk=R['lockout']
            if violation in lk['counted_rules'] and principal not in lk['exempt_principals'] and principal not in self.blocked:
                st=[t for t in self.strikes.get(principal,[]) if now-t<lk['window_s']]+[now];self.strikes[principal]=st
                if len(st)>=lk['max_violations']:
                    if self.enforce:self.blocked.add(principal);self.strikes.pop(principal,None)
                    violation='lockout';message=f"Lockout: {len(st)} violazioni di sicurezza in {lk['window_s']} s, {principal} revocato (Restore dalla console Defense)"
            self.seen[p['request_id']]=time.time()
            self.seen={k:v for k,v in self.seen.items() if time.time()-v<=R['replay']['window_s']}
            if violation:
                # Monitor records a would-deny decision but never applies a failed action.
                return self.event(category,'DENY' if self.enforce else 'MONITOR',message,p,R[violation]['id'],'high')
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
            elif action=='reset':self.reset_state()
            elif action=='enforce':self.enforce=params['value']
            elif action=='restore':self.blocked.clear();self.strikes.clear()
            elif action=='home':
                for k in r['joints']:r['joints'][k]=ARM_START.get(k,0.)
                r['height']=0.;r['gripper']='open'
            if action=='trajectory':
                # Slim the ALLOW audit record: keep only the final target pose, not every chunk point
                # (15 chunks/s x full joint arrays is what bloats events.jsonl). DENY records stay full.
                pts=p['params'].get('points',[])
                p['params']['n_points']=len(pts);p['params']['points']=pts[-1:];p['params'].pop('start',None)
            if action in ua['privileged_actions']:return self.event('defense','DEFENSE','Eseguito: '+action,p,severity='medium',human_override=True)
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
            elif kind=='geofence':
                self.robot['mode']='armed'
                p={'action':'move','params':{'v':.5,'w':0.,'duration':5}}
            elif kind=='replay':
                p={'action':'status','request_id':str(uuid.uuid4())};self.command(p,category='replay-setup')
            elif kind=='rate':
                self.rate[principal]=[time.monotonic()]*self.R['rate']['max_per_s']
                self.event('scenario-setup','INFO',f"Flooding: {self.R['rate']['max_per_s']} comandi nell'ultimo secondo da {principal}")
            elif kind=='lockout':
                n=self.R['lockout']['max_violations']-1;self.strikes[principal]=[time.monotonic()]*n;p['target']='g1d-02'
                self.event('scenario-setup','INFO',f'Brute force: {n} violazioni di sicurezza recenti da {principal}')
            elif kind=='sequence':
                # The fixture explicitly disarms; both consoles see the change.
                self.robot['mode']='disarmed';self.robot['v']=self.robot['w']=0
                self.event('scenario-setup','INFO','Sequence abuse: twin portato in disarmed')
            # Demo fixtures are sandboxed: they don't count toward lockout or rate (except their own scenario),
            # so pressing Red Team buttons does not quarantine the operator the model adapter runs as.
            saved=(copy.deepcopy(self.rate),copy.deepcopy(self.strikes))
            try:return self.command(p,principal,kind)
            finally:
                if kind!='lockout':self.rate,self.strikes=saved
    def defense(self,action,value=None):
        """Direct emergency channel: only actions that reduce risk. reset/enforce/restore go through command()."""
        with self.lock:
            if action=='estop':self.robot['mode']='estop';self.robot['v']=self.robot['w']=0
            elif action=='revoke':
                if value not in ('operator','guest'):raise ValueError('Identità non valida')
                self.blocked.add(value)
            elif action in PRIVILEGED:raise ValueError(action+": passa dal PEP, POST /api/command con principal defender")
            else:raise ValueError('Azione Defense sconosciuta')
            return self.event('defense','DEFENSE',action+': '+str(value),severity='medium',human_override=True)
    def suite(self):
        with self.lock:
            saved=copy.deepcopy(self.robot);blocked=self.blocked.copy();enforce=self.enforce;counters=(self.rate,self.strikes)
            self.blocked.clear();self.enforce=True;results=[]
            try:
                for k in LABELS:
                    self.reset_state();self.blocked.clear();self.rate={};self.strikes={};ev=self.attack(k)
                    rid=self.R[k]['id']
                    results.append({'scenario':k,'expected':rid,'actual':ev['rule_id'],'passed':ev['rule_id']==rid and ev['decision']=='DENY'})
                self.reset_state();self.blocked.clear();self.rate={};self.strikes={};ev=self.command({'action':'arm'})
                results.append({'scenario':'authorized','expected':'ALLOW','actual':ev['decision'],'passed':ev['decision']=='ALLOW'})
            finally:
                self.robot=saved;self.robot['v']=self.robot['w']=0;self.blocked=blocked;self.enforce=enforce;self.rate,self.strikes=counters
            self.event('test-suite','INFO',f"Test completati: {sum(r['passed'] for r in results)}/{len(results)}")
            return results
if __name__=='__main__':
    import sys
    n,err=verify_log(sys.argv[1] if len(sys.argv)>1 else ROOT/'data/events.jsonl')
    print(f'audit chain broken at line {n}: {err}' if err else f'audit chain OK: {n} events');sys.exit(1 if err else 0)
