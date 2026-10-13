import * as THREE from 'three';
import {OrbitControls} from './vendor/OrbitControls.js';
import {STLLoader} from './vendor/STLLoader.js';
export async function createScene(container) {
 const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});
 renderer.setPixelRatio(Math.min(devicePixelRatio,1.7));renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.05;container.appendChild(renderer.domElement);
 const scene=new THREE.Scene();scene.fog=new THREE.Fog(0x14212e,7,18);
 const camera=new THREE.PerspectiveCamera(34,1,.02,50);camera.position.set(1.7,1.45,2.0);
 const controls=new OrbitControls(camera,renderer.domElement);controls.target.set(0,.69,0);controls.enableDamping=true;controls.minDistance=1.2;controls.maxDistance=8;controls.maxPolarAngle=Math.PI*.49;
 scene.add(new THREE.HemisphereLight(0xe8f2ff,0x384453,1.5));
 const key=new THREE.DirectionalLight(0xfff5e4,2.5);key.position.set(3,5,3);key.castShadow=true;key.shadow.mapSize.set(2048,2048);key.shadow.camera.left=-3;key.shadow.camera.right=3;key.shadow.camera.top=3;key.shadow.camera.bottom=-3;key.shadow.bias=-.0003;scene.add(key);
 const rim=new THREE.DirectionalLight(0x7cbbff,1.5);rim.position.set(-3,3,-2);scene.add(rim);
 const ground=new THREE.Mesh(new THREE.PlaneGeometry(40,40),new THREE.MeshStandardMaterial({color:0x0d1620,roughness:.94,metalness:.12}));ground.rotation.x=-Math.PI/2;ground.position.y=-.003;ground.receiveShadow=true;scene.add(ground);
 const grid=new THREE.GridHelper(20,80,0x466071,0x2a3b4b);grid.position.y=.001;grid.material.transparent=true;grid.material.opacity=.5;scene.add(grid);
 const ring=new THREE.Mesh(new THREE.RingGeometry(.59,.595,96),new THREE.MeshBasicMaterial({color:0xc0ed79,transparent:true,opacity:.55,side:THREE.DoubleSide}));ring.rotation.x=-Math.PI/2;ring.position.y=.004;scene.add(ring);
 const world=new THREE.Group();world.rotation.x=-Math.PI/2;scene.add(world);
 const urdf=await (await fetch('/model/g1_d.urdf')).text();const xml=new DOMParser().parseFromString(urdf,'text/xml');
 const links={},joints={},meshes=[],loader=new STLLoader();let completed=0;
 const nums=(v,def='0 0 0')=>(v||def).split(/\s+/).map(Number);
 function origin(group,node){if(!node)return;group.position.fromArray(nums(node.getAttribute('xyz')));const [r,p,y]=nums(node.getAttribute('rpy'));group.quaternion.setFromEuler(new THREE.Euler(r,p,y,'ZYX'));}
 const tasks=[];
 for(const link of xml.querySelectorAll('robot > link')){
  const name=link.getAttribute('name');const group=new THREE.Group();group.name=name;links[name]=group;
  for(const visual of link.querySelectorAll(':scope > visual')){
   const file=visual.querySelector('mesh')?.getAttribute('filename');if(!file)continue;
   const holder=new THREE.Group();origin(holder,visual.querySelector('origin'));group.add(holder);
   const color=nums(visual.querySelector('color')?.getAttribute('rgba'),'0.7 0.7 0.7 1');
   tasks.push(loader.loadAsync('/model/'+file).then(geometry=>{
    geometry.computeVertexNormals();const mat=new THREE.MeshStandardMaterial({color:/head|Wheel|logo|hand_/.test(name)?0x252a30:/Pitching|Yaw_Link/.test(name)?0x353b43:/LZ_/.test(name)?0x929ba6:new THREE.Color(...color.slice(0,3)),metalness:.25,roughness:.46});
    const mesh=new THREE.Mesh(geometry,mat);mesh.castShadow=true;mesh.receiveShadow=true;mesh.name=name;holder.add(mesh);meshes.push(mesh);
    const scale=visual.querySelector('mesh').getAttribute('scale');if(scale)mesh.scale.fromArray(nums(scale));
    completed++;document.querySelector('#model-loading').textContent=`Caricamento mesh ufficiali · ${completed}/${tasks.length}`;
   }));
  }
 }
 const children=new Set();
 for(const joint of xml.querySelectorAll('robot > joint')){
  const parent=joint.querySelector('parent').getAttribute('link'),child=joint.querySelector('child').getAttribute('link');children.add(child);
  const fixed=new THREE.Group();origin(fixed,joint.querySelector('origin'));const motion=new THREE.Group();fixed.add(motion);motion.add(links[child]);links[parent].add(fixed);
  const axis=new THREE.Vector3().fromArray(nums(joint.querySelector('axis')?.getAttribute('xyz'),'1 0 0'));
  joints[joint.getAttribute('name')]={motion,axis,type:joint.getAttribute('type'),value:0};
 }
 for(const [name,link] of Object.entries(links))if(!children.has(name))world.add(link);
 await Promise.all(tasks);world.updateMatrixWorld(true);const box=new THREE.Box3().setFromObject(world);const lift=-box.min.y+.006;world.position.y=lift;
 document.querySelector('#model-loading').classList.add('hidden');document.querySelector('#mesh-status').textContent=`Unitree URDF · ${meshes.length} mesh originali`;
 let state=null,last=performance.now();
 const resize=()=>{const w=container.clientWidth,h=container.clientHeight;if(!w||!h)return;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();};new ResizeObserver(resize).observe(container);resize();
 function frame(now){requestAnimationFrame(frame);const dt=Math.min((now-last)/1000,.1);last=now;
  if(state){world.position.x=THREE.MathUtils.lerp(world.position.x,state.x,Math.min(1,dt*12));world.position.z=THREE.MathUtils.lerp(world.position.z,-state.y,Math.min(1,dt*12));world.quaternion.setFromEuler(new THREE.Euler(-Math.PI/2,0,0));world.rotateZ(state.yaw);
   for(const [name,j] of Object.entries(joints)){const target=state.joints[name]??0;j.value=THREE.MathUtils.lerp(j.value,target,Math.min(1,dt*10));if(j.type==='prismatic')j.motion.position.copy(j.axis).multiplyScalar(j.value);else if(j.type!=='fixed')j.motion.quaternion.setFromAxisAngle(j.axis,j.value);}
   ring.position.x=world.position.x;ring.position.z=world.position.z;ring.material.color.set(state.mode==='estop'?0xff717a:0xc0ed79);
  }controls.update();renderer.render(scene,camera);
 }
 requestAnimationFrame(frame);
 return {update:r=>{state=r;},reset:()=>{camera.position.set(world.position.x+1.7,1.45,world.position.z+2.0);controls.target.set(world.position.x,.69,world.position.z);},wire:()=>meshes.forEach(m=>m.material.wireframe=!m.material.wireframe),meshCount:meshes.length};
}
