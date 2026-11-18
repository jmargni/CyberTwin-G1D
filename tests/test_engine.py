import tempfile,unittest,json,time
from engine import Twin,LABELS,RULES,PRIVILEGED
class PolicyTests(unittest.TestCase):
 def setUp(self):self.t=Twin()
 def defend(self,action,value=None):return self.t.command({'action':action,'params':{} if value is None else {'value':value}},'defender')
 def test_privileged_actions_need_defender(self):
  self.t.defense('estop')
  ev=self.t.command({'action':'reset'});self.assertEqual((ev['decision'],ev['rule_id']),('DENY','G1D-101'));self.assertEqual(self.t.robot['mode'],'estop')
  ev=self.t.command({'action':'enforce','params':{'value':False}},'guest');self.assertEqual(ev['rule_id'],'G1D-101');self.assertTrue(self.t.enforce)
  ev=self.defend('reset');self.assertEqual(ev['decision'],'DEFENSE');self.assertTrue(ev['human_override']);self.assertEqual(self.t.robot['mode'],'disarmed')
 def test_rule_parameters_come_from_policy_file(self):
  import engine
  with tempfile.TemporaryDirectory() as d:
   f=d+'/policy.json'
   json.dump({'version':'t','rules':{'parameters':{'move':{'v_max':0.1,'w_max':1.0,'duration_max':5.0}}}},open(f,'w'))
   p=engine.load_policy(f);self.assertEqual(p['rules']['parameters']['move']['v_max'],0.1);self.assertEqual(p['rules']['geofence']['id'],'G1D-109')
   self.t.policy=p;self.t.R=p['rules'];self.t.command({'action':'arm'})
   self.assertEqual(self.t.command({'action':'move','params':{'v':0.2,'duration':1}})['rule_id'],'G1D-102')
   json.dump({'rules':{'parametres':{}}},open(f,'w'))
   with self.assertRaises(ValueError):engine.load_policy(f)
 def test_defense_channel_refuses_risk_raising_actions(self):
  for a in PRIVILEGED:
   with self.subTest(a=a),self.assertRaises(ValueError):self.t.defense(a,False)
 def test_enforce_value_must_be_boolean(self):
  self.assertEqual(self.defend('enforce','no')['rule_id'],'G1D-102');self.assertTrue(self.t.enforce)
 def test_monitor_cannot_be_left_by_a_non_defender(self):
  self.defend('enforce',False);self.t.command({'action':'enforce','params':{'value':True}});self.assertFalse(self.t.enforce)
 def test_all_document_scenarios_block_with_specific_rule(self):
  for k in LABELS:
   with self.subTest(k=k):
    t=Twin();ev=t.attack(k);self.assertEqual(ev['decision'],'DENY');self.assertEqual(ev['rule_id'],RULES[k]);self.assertEqual(t.robot['v'],0)
 def test_valid_action_changes_state(self):
  self.t.command({'action':'arm'});self.assertEqual(self.t.command({'action':'height','params':{'value':.3}})['decision'],'ALLOW');self.assertAlmostEqual(self.t.robot['joints']['LZ_it_Joint'],.15)
 def test_replay_is_rejected_after_first_acceptance(self):
  p={'action':'status','request_id':'same'};self.assertEqual(self.t.command(p)['decision'],'ALLOW');self.assertEqual(self.t.command(p)['rule_id'],'G1D-105')
 def test_expired_request_rejected(self):
  self.assertEqual(self.t.command({'action':'status','timestamp':time.time()-40})['rule_id'],'G1D-105')
 def test_estop_latched_until_defense_reset(self):
  self.t.defense('estop');self.assertEqual(self.t.command({'action':'arm'})['decision'],'DENY');self.defend('reset');self.assertEqual(self.t.command({'action':'arm'})['decision'],'ALLOW')
 def test_revoke_and_restore(self):
  self.t.defense('revoke','operator');self.assertEqual(self.t.command({'action':'arm'})['rule_id'],'G1D-101');self.defend('restore');self.assertEqual(self.t.command({'action':'arm'})['decision'],'ALLOW')
 def test_monitor_does_not_apply_invalid_action(self):
  self.defend('enforce',False);self.assertEqual(self.t.attack('parameters')['decision'],'MONITOR');self.assertEqual(self.t.robot['v'],0)
 def test_invalid_numbers_are_rejected(self):
  for value in (float('nan'),float('inf'),'5',True):
   with self.subTest(value=value):
    with self.assertRaises(ValueError):self.t.command({'action':'move','params':{'v':value}})
 def test_unknown_action_and_joint(self):
  self.assertEqual(self.t.command({'action':'shell'})['rule_id'],'G1D-103');self.assertEqual(self.t.command({'action':'joint','params':{'name':'nope','value':0}})['rule_id'],'G1D-102')
 def test_joint_limit_and_negative_height(self):
  self.assertEqual(self.t.command({'action':'joint','params':{'name':'left_elbow_joint','value':100}})['rule_id'],'G1D-102');self.assertEqual(self.t.command({'action':'height','params':{'value':-.1}})['rule_id'],'G1D-102')
 def test_suite_restores_posture_and_policy(self):
  self.t.command({'action':'arm'});self.t.command({'action':'height','params':{'value':.4}});self.defend('enforce',False);self.t.defense('revoke','operator');r=self.t.suite();self.assertTrue(all(x['passed'] for x in r));self.assertEqual(len(r),len(LABELS)+1);self.assertEqual(self.t.robot['height'],.4);self.assertFalse(self.t.enforce);self.assertIn('operator',self.t.blocked)
 def test_jsonl_matches_recorded_decision(self):
  with tempfile.TemporaryDirectory() as p:
   t=Twin(p);e=t.attack('spoofing');events=[json.loads(x) for x in (t.log_dir/'events.jsonl').read_text().splitlines()];self.assertEqual(events[-1]['event_id'],e['event_id'])
ARM=['left_shoulder_pitch_joint','left_elbow_joint']
def traj(points,**kw):return {'action':'trajectory','params':{'joints':ARM,'points':points,'dt':1/15,**kw}}
class TrajectoryTests(unittest.TestCase):
 def setUp(self):
  # The fixtures' chunks start near 0; the twin's arms start at the task's start pose (ARM_START).
  self.t=Twin();self.t.robot['joints'].update(dict.fromkeys(self.t.robot['joints'],0.));self.t.command({'action':'arm'})
 def test_valid_chunk_mirrors_last_point_and_gripper(self):
  e=self.t.command(traj([[.05,.05],[.1,.1]],gripper='closed'));self.assertEqual(e['decision'],'ALLOW')
  self.assertAlmostEqual(self.t.robot['joints']['left_elbow_joint'],.1);self.assertEqual(self.t.robot['gripper'],'closed')
 def test_requires_armed(self):
  self.t.command({'action':'disarm'});self.assertEqual(self.t.command(traj([[0,0]]))['rule_id'],'G1D-107')
 def test_estop_blocks(self):
  self.t.defense('estop');self.assertEqual(self.t.command(traj([[0,0]]))['decision'],'DENY')
 def test_urdf_limit(self):
  e=self.t.command(traj([[0,5]],start=[0,4.9]));self.assertEqual(e['rule_id'],'G1D-102');self.assertIn('limite URDF',e['message'])
 def test_speed_limit_against_start_and_between_points(self):
  self.assertEqual(self.t.command(traj([[.5,0]],start=[0,0]))['rule_id'],'G1D-102')
  e=self.t.command(traj([[0,0],[0,.5]]));self.assertEqual(e['rule_id'],'G1D-102');self.assertIn('rad/s',e['message'])
  self.assertEqual(self.t.command(traj([[.5,0]],start=[.45,0]))['decision'],'ALLOW')
 def test_only_arm_joints(self):
  e=self.t.command({'action':'trajectory','params':{'joints':['LZ_mt_Joint'],'points':[[0.]],'dt':.1}});self.assertEqual(e['rule_id'],'G1D-102')
 def test_malformed_rejected(self):
  for bad in ({'joints':ARM,'points':[[0]],'dt':.1},{'joints':ARM,'points':[[0,float('nan')]],'dt':.1},{'joints':ARM,'points':[[0,0]]*65,'dt':.1},{'joints':ARM,'points':[[0,0]],'dt':.1,'gripper':'half'}):
   with self.subTest(bad=bad):
    with self.assertRaises(ValueError):self.t.command({'action':'trajectory','params':bad})
 def test_monitor_does_not_mirror_invalid_chunk(self):
  self.t.command({'action':'enforce','params':{'value':False}},'defender');e=self.t.command(traj([[3,0]],start=[3,0]))
  self.assertEqual(e['decision'],'MONITOR');self.assertEqual(self.t.robot['joints']['left_shoulder_pitch_joint'],0)
 def test_start_and_home_at_task_start_pose(self):
  from engine import ARM_START
  t=Twin();self.assertEqual(t.robot['joints']['right_elbow_joint'],ARM_START['right_elbow_joint'])
  t.command({'action':'arm'});t.command({'action':'joint','params':{'name':'right_elbow_joint','value':1.0}});t.command({'action':'home'})
  self.assertEqual(t.robot['joints']['right_elbow_joint'],ARM_START['right_elbow_joint']);self.assertEqual(t.robot['joints']['Left_Wheel_Joint'],0)
if __name__=='__main__':unittest.main()
