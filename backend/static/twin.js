/* BAKO digital twin: 3D vehicle built up phase by phase, driven by real build data.
   Vehicle model, lighting and motion (drop-in parts, camera glide, pulsing zones) come from the
   BAKO Check Station concept page. Requires three.js r128 (vendor/three.min.js).

   const twin = BakoTwin.mount(element);
   twin.update({phase: 3, phases: {0:'pass', 1:'pass', 2:'fail', 3:'run'}});   // 'run' | 'fail' | 'pass' | null
*/
(function () {
"use strict";
const MINT = 0x5fd08a, AMBER = 0xf2b341, RED = 0xff6b5e;
// Route phase -> parts that appear and the zones that light up (draft route in config/reference.json).
const PHASES = [
  {n: "Chassis",    groups: ["frame"],              zones: [[[0, 0.25, 0], [1.9, 0.12, 1.05]]],                                 cam: [3.0, 2.2, 3.8]},
  {n: "Suspension", groups: ["drive"],              zones: [[[-0.88, 0.3, 0], [0.3, 0.28, 0.4]], [[0.98, 0.3, 0], [0.12, 0.1, 0.6]]], cam: [1.2, 1.2, 3.4]},
  {n: "Harness",    groups: ["power"],              zones: [[[-1.24, 0.82, 0], [0.12, 0.22, 0.44]], [[0.85, 0.85, 0], [0.27, 0.2, 0.52]]], cam: [-2.6, 1.5, 2.6]},
  {n: "Battery",    groups: ["battery"],            zones: [[[-0.92, 0.72, 0], [0.6, 0.3, 0.88]]],                              cam: [-1.8, 2.0, 3.2]},
  {n: "Power",      groups: ["roof", "gnss"],       zones: [[[-0.68, 1.74, 0], [1.0, 0.12, 0.9]]],                              cam: [-0.6, 3.4, 3.0]},
  {n: "Body",       groups: ["interior", "lights"], zones: [[[1.08, 0.86, 0], [0.15, 0.22, 1.05]], [[-1.27, 1.18, 0], [0.1, 0.12, 1.1]]], cam: [-2.6, 1.8, 2.4]},
  {n: "Final test", groups: [],                     zones: [[[0, 0.85, 0], [2.6, 1.7, 1.25]]],                                  cam: [2.2, 2.6, 2.6]},
];

function webglOk() {
  try { const c = document.createElement("canvas"); return !!(window.WebGLRenderingContext && (c.getContext("webgl") || c.getContext("experimental-webgl"))); }
  catch (e) { return false; }
}

function mount(stage, opts) {
  opts = opts || {};
  if (!window.THREE || !webglOk()) { stage.classList.add("twin-off"); return null; }
  const reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const R=new THREE.WebGLRenderer({antialias:true,alpha:true});
  R.setPixelRatio(Math.min(devicePixelRatio,2));R.shadowMap.enabled=true;R.shadowMap.type=THREE.PCFSoftShadowMap;
  R.outputEncoding=THREE.sRGBEncoding;R.toneMapping=THREE.ACESFilmicToneMapping;R.toneMappingExposure=0.95;stage.prepend(R.domElement);
  const S=new THREE.Scene();
  const cam=new THREE.PerspectiveCamera(36,1,0.05,60);
  {const es=new THREE.Scene();es.background=new THREE.Color(0x0b0e10);
   const pan=(w,h,p,r,i)=>{const m=new THREE.Mesh(new THREE.PlaneGeometry(w,h),new THREE.MeshBasicMaterial({color:new THREE.Color(i,i,i),side:THREE.DoubleSide}));m.position.set(...p);m.rotation.set(...r);es.add(m)};
   pan(6,1.2,[0,5,0],[Math.PI/2,0,0],1.6);pan(4,2,[5,2,0],[0,-Math.PI/2,0],1.6);pan(4,2,[-5,2,2],[0,Math.PI/2,0],0.9);
   S.environment=new THREE.PMREMGenerator(R).fromScene(es,0.04).texture}
  S.add(new THREE.HemisphereLight(0xbfd8ff,0x0b0b0b,0.35));
  const key=new THREE.DirectionalLight(0xffffff,1.6);key.position.set(3,6,3);key.castShadow=true;key.shadow.mapSize.set(2048,2048);Object.assign(key.shadow.camera,{left:-3,right:3,top:3,bottom:-3});key.shadow.bias=-0.0004;S.add(key);
  const rimL=new THREE.DirectionalLight(0x9fe3b4,0.9);rimL.position.set(-4,2.5,-3);S.add(rimL);
  {const f=new THREE.Mesh(new THREE.CircleGeometry(4,64),new THREE.ShadowMaterial({opacity:.45}));f.rotation.x=-Math.PI/2;f.receiveShadow=true;S.add(f);
   const ring=new THREE.Mesh(new THREE.RingGeometry(2.1,2.115,96),new THREE.MeshBasicMaterial({color:0x9fe3b4,transparent:true,opacity:.3}));ring.rotation.x=-Math.PI/2;ring.position.y=0.002;S.add(ring)}
  const mats=[];const M=(c,o={})=>{const m=new THREE.MeshStandardMaterial(Object.assign({color:c,roughness:.6,metalness:.1,envMapIntensity:.45},o));m.color.convertSRGBToLinear();if(m.emissive)m.emissive.convertSRGBToLinear();mats.push(m);return m};
  const frame=M(0x3b4045,{metalness:.55,roughness:.45}),white=M(0xf3f4f2,{roughness:.25,side:THREE.DoubleSide}),green=M(0xbcd6b2,{roughness:.3,side:THREE.DoubleSide}),
  beige=M(0xdccb9e,{roughness:.75}),tire=M(0x1a1b1c,{roughness:.92}),rim=M(0xc4c8cc,{metalness:.85,roughness:.25}),rimDark=M(0x1c1e20,{metalness:.5,roughness:.4}),
  black=M(0x222527,{roughness:.8}),plastic=M(0x2c3033,{roughness:.55}),orange=M(0xe2722c,{roughness:.5}),lamp=M(0xe6eef3,{metalness:.7,roughness:.1}),
  red=M(0xb0261c,{emissive:0x4a0a06,roughness:.3}),alu=M(0xb9bec2,{metalness:.8,roughness:.35}),screen=M(0x0d1418,{emissive:0x1b3a4a,roughness:.2}),spring=M(0x111111,{metalness:.4});
  const car=new THREE.Group();S.add(car);let cur;const STG={};function SG(n){cur=STG[n]||(STG[n]=new THREE.Group());if(!cur.parent)car.add(cur)}
  const add=(g,m,p=[0,0,0],r=[0,0,0],par=null)=>{par=par||cur;const o=new THREE.Mesh(g,m);o.position.set(...p);o.rotation.set(...r);o.castShadow=true;o.receiveShadow=true;par.add(o);return o};
  const V=a=>new THREE.Vector3(...a);
  const tube=(pts,r=0.021,m=frame,tens=0.0)=>add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts.map(V),false,'catmullrom',tens),Math.max(24,pts.length*16),r,8,false),m);
  const box=(s,p,m,r)=>add(new THREE.BoxGeometry(...s),m,p,r);
  // rounded box via extruded rounded rect (profile in XY, depth along Z)
  function rbox(w,h,d,rad,m,p,r){const sh=new THREE.Shape(),x=-w/2,y=-h/2;sh.moveTo(x+rad,y);sh.lineTo(x+w-rad,y);sh.quadraticCurveTo(x+w,y,x+w,y+rad);sh.lineTo(x+w,y+h-rad);sh.quadraticCurveTo(x+w,y+h,x+w-rad,y+h);sh.lineTo(x+rad,y+h);sh.quadraticCurveTo(x,y+h,x,y+h-rad);sh.lineTo(x,y+rad);sh.quadraticCurveTo(x,y,x+rad,y);
   const g=new THREE.ExtrudeGeometry(sh,{depth:d-0.04,bevelEnabled:true,bevelThickness:0.02,bevelSize:0.02,bevelSegments:3,curveSegments:6});g.translate(0,0,-(d-0.04)/2);return add(g,m,p,r)}
  
  SG("frame");
  for(const s of[-1,1]){const z=0.5*s;
   // main side rail: rear deck -> drop to floor -> front upright -> A-pillar to roof
   tube([[-1.2,0.62,z],[-0.62,0.62,z],[-0.5,0.4,z],[-0.42,0.19,z],[0.52,0.19,z],[0.62,0.3,z],[0.63,1.0,z*0.94],[0.45,1.3,z*0.9],[0.27,1.6,z*0.88]],0.024,frame,0.05);
   tube([[-0.42,0.19,z*0.78],[0.52,0.19,z*0.78]],0.02);                 // inner floor rail
   tube([[-0.7,0.62,z],[-0.7,1.66,z*0.97]],0.022);                       // rear roof post
   tube([[-0.5,0.62,z*0.9],[-0.5,1.66,z*0.9]],0.02);                     // second post
   tube([[-1.2,0.62,z],[-1.24,0.66,z*1.04],[-1.24,0.9,z*1.04]],0.02);    // rear corner upright
   tube([[-0.5,1.2,z*0.9],[-1.1,1.2,z*1.06],[-1.24,1.18,z*0.75]],0.02,frame,0.4); // light-bar arm
   tube([[-0.42,0.19,z*0.55],[-0.42,0.55,z*0.55],[0.08,0.55,z*0.55],[0.08,0.19,z*0.55]],0.016); // seat cage
   tube([[0.63,0.45,z*0.9],[1.12,0.45,z*0.8]],0.02);                     // front lower longeron
   tube([[0.63,0.72,z*0.9],[0.98,0.72,z*0.8],[1.12,0.45,z*0.8]],0.018);  // front upper longeron
   // side ladder bracket on A-post
   for(const y of[0.55,0.7,0.85])box([0.03,0.02,0.05],[0.66,y,z*1.0],frame);
   // orange HV loom along rail
   tube([[-1.15,0.58,z*1.06],[-0.62,0.58,z*1.06],[-0.47,0.3,z*1.06],[-0.4,0.16,z*1.06],[0.5,0.16,z*1.06],[0.6,0.3,z*1.06]],0.011,orange,0.4);
  }
  // cross members
  for(const[x,y]of[[-1.2,0.62],[-0.62,0.62],[-0.42,0.19],[0.05,0.19],[0.52,0.19],[1.12,0.45],[0.98,0.72]])tube([[x,y,-0.5],[x,y,0.5]],0.018);
  tube([[-1.24,1.18,-0.38],[-1.26,1.24,0],[-1.24,1.18,0.38]],0.02);   // light bar
  tube([[-1.24,0.9,-0.52],[-1.26,0.92,0],[-1.24,0.9,0.52]],0.022);    // rear bumper hoop
  tube([[0.63,1.0,-0.47],[0.66,1.02,0],[0.63,1.0,0.47]],0.02);        // dash bar
  tube([[-0.7,1.66,-0.48],[-0.6,1.68,0],[-0.7,1.66,0.48]],0.016);     // roof crossbar
  // front tow stand
  tube([[1.12,0.45,-0.12],[1.25,0.32,-0.05],[1.3,0.1,0]],0.022);tube([[1.12,0.45,0.12],[1.25,0.32,0.05],[1.3,0.1,0]],0.022);
  box([0.04,0.03,0.42],[1.3,0.08,0],frame);box([0.25,0.03,0.04],[1.2,0.3,0],frame);
  
  SG("roof");// ROOF with sunroof hole + solar panel ----------
  const roofY=(x,z)=>1.76-0.24*(x+0.47)**2-0.32*z*z-(x>0.05?1.3*(x-0.05)**2:0)-(x<-1.05?1.5*(x+1.05)**2:0);
  function bent(shape,depth,mat,lift=0){const g=new THREE.ExtrudeGeometry(shape,{depth,bevelEnabled:false,curveSegments:10});
   // subdivide by tessellating: use many vertices via shape points; bend each
   const p=g.attributes.position;for(let i=0;i<p.count;i++){const sx=p.getX(i),sz=p.getY(i),t=p.getZ(i);p.setXYZ(i,sx,roofY(sx,sz)+t+lift,sz)}
   g.computeVertexNormals();return add(g,mat)}
  function rrect(x0,x1,z0,z1,r,n=24){const s=new THREE.Shape();const pts=[];
   const seg=(ax,az,bx,bz)=>{for(let i=0;i<n;i++){const t=i/n;pts.push([ax+(bx-ax)*t,az+(bz-az)*t])}};
   const corner=(cx,cz,a0)=>{for(let i=0;i<6;i++){const a=a0+i/6*Math.PI/2;pts.push([cx+r*Math.cos(a),cz+r*Math.sin(a)])}};
   seg(x0+r,z0,x1-r,z0);corner(x1-r,z0+r,-Math.PI/2);seg(x1,z0+r,x1,z1-r);corner(x1-r,z1-r,0);seg(x1-r,z1,x0+r,z1);corner(x0+r,z1-r,Math.PI/2);seg(x0,z1-r,x0,z0+r);corner(x0+r,z0+r,Math.PI);
   s.moveTo(...pts[0]);pts.slice(1).forEach(q=>s.lineTo(...q));return s}
  {const sh=rrect(-1.26,0.3,-0.63,0.63,0.18,30);const hole=rrect(-0.12,0.16,-0.3,0.3,0.08,8);sh.holes.push(new THREE.Path(hole.getPoints()));
   bent(sh,0.035,white);
   // side skirts (lip) of roof
   for(const s of[-1,1]){const pts=[];for(let i=0;i<=30;i++){const x=-1.2+i/30*1.45;pts.push(V([x,roofY(x,0.63*s)-0.03,0.63*s]))}
    add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts),60,0.035,6,false),white)}
   // solar panel texture
   const cv=document.createElement('canvas');cv.width=512;cv.height=256;const c=cv.getContext('2d');c.fillStyle='#14181d';c.fillRect(0,0,512,256);
   c.strokeStyle='#3a4450';c.lineWidth=1;for(let x=0;x<512;x+=16){c.beginPath();c.moveTo(x,0);c.lineTo(x,256);c.stroke()}for(let y=0;y<256;y+=10){c.beginPath();c.moveTo(0,y);c.lineTo(512,y);c.stroke()}
   c.fillStyle='#9aa3aa';for(let x=6;x<512;x+=22){c.fillRect(x,2,3,3);c.fillRect(x,251,3,3)}
   const tex=new THREE.CanvasTexture(cv);const pm=M(0xffffff,{map:tex,roughness:.25,metalness:.3});
   const g=new THREE.PlaneGeometry(0.95,0.86,20,12);g.rotateX(-Math.PI/2);const p=g.attributes.position;
   for(let i=0;i<p.count;i++){const x=p.getX(i)-0.68,z=p.getZ(i);p.setXYZ(i,x,roofY(x,z)+0.04,z)}g.computeVertexNormals();add(g,pm);
   box([0.12,0.05,0.1],[0.21,roofY(0.21,0.4)+0.05,0.38],white);box([0.12,0.05,0.1],[0.21,roofY(0.21,-0.4)+0.05,-0.38],white); // hinge bosses
  }
  
  SG("interior");// SEAT (shaped bucket) ----------
  {const seat=new THREE.Group();seat.position.set(-0.15,0,0);cur.add(seat);
   const cush=rbox(0.5,0.12,0.5,0.05,beige,[0,0.64,0],[0,0,-0.04]);seat.attach(cush);
   const back=rbox(0.13,0.82,0.46,0.06,beige,[-0.27,1.08,0],[0,0,0.2]);seat.attach(back);
   const head=rbox(0.11,0.22,0.38,0.08,beige,[-0.37,1.55,0],[0,0,0.2]);seat.attach(head);
   for(const s of[-1,1]){const bol=rbox(0.4,0.07,0.07,0.03,beige,[0.02,0.72,0.22*s],[0,0,-0.04]);seat.attach(bol)}
   const rec=rbox(0.18,0.14,0.06,0.04,black,[-0.2,0.62,0.27],[0,0,0]);seat.attach(rec);
   const base=box([0.42,0.06,0.42],[0,0.55,0],plastic);seat.attach(base);
   // seatbelt
   tube([[-0.7,1.5,0.32],[-0.42,1.3,0.24],[-0.05,0.75,0.18],[0.05,0.7,-0.24]],0.012,black,0.3);
  }
  
  // ---------- REAR: battery tray, controller plate, motor, lights ----------
  SG("battery");rbox(0.52,0.22,0.8,0.03,black,[-0.92,0.72,0]);                         // battery pack
  box([0.4,0.004,0.3],[-0.92,0.84,0.05],M(0xf2f2f2));                    // label sheet
  box([0.6,0.03,0.06],[-0.92,0.85,-0.25],black,[0,0.35,0]);               // hold-down strap
  SG("power");box([0.04,0.35,0.38],[-1.22,1.0,0],black);                              // controller mount plate
  box([0.05,0.12,0.3],[-1.24,0.78,0],alu);                                // finned controller
  for(let i=0;i<10;i++)box([0.03,0.1,0.006],[-1.27,0.78,-0.14+i*0.031],alu);
  add(new THREE.CylinderGeometry(0.035,0.035,0.03,16),black,[-1.25,0.82,0],[0,0,Math.PI/2]); // charge port
  SG("drive");add(new THREE.CylinderGeometry(0.11,0.11,0.32,20),alu,[-0.88,0.3,0.0],[Math.PI/2,0,0]);   // rear motor
  add(new THREE.CylinderGeometry(0.025,0.025,1.12,10),frame,[-0.88,0.29,0],[Math.PI/2,0,0]); // rear axle
  SG("lights");for(const s of[-1,1]){const tl=rbox(0.06,0.07,0.2,0.02,red,[-1.27,1.18,0.48*s],[0,0.25*s,0]);box([0.05,0.09,0.03],[-1.25,1.18,0.36*s],black)}
  SG("power");tube([[-1.2,0.5,0.2],[-1.0,0.35,0.15],[-0.88,0.4,0.1]],0.014,orange,0.5);tube([[-1.2,0.55,0.15],[-1.05,0.42,0.1],[-0.88,0.42,0.05]],0.014,orange,0.5);
  
  SG("interior");// COCKPIT: dashboard, screen, steering, footwell ----------
  {const d=rbox(0.28,0.3,1.0,0.06,plastic,[0.55,0.97,0],[0,0,-0.15]);
   box([0.02,0.035,0.62],[0.4,0.9,0.12],green,[0,0,-0.15]);               // green trim strip
   box([0.05,0.16,0.24],[0.43,1.16,0.12],black,[0,0,-0.25]);box([0.005,0.13,0.21],[0.405,1.165,0.12],screen,[0,0,-0.25]);
   for(const z of[-0.36,0.36])add(new THREE.TorusGeometry(0.04,0.01,8,20),alu,[0.42,1.04,z],[0,Math.PI/2,0]); // air vents
   const col=[0.38,1.0,0.14];tube([[0.62,0.82,0.14],col],0.022,black);
   const wh=new THREE.Group();wh.position.set(...col);wh.rotation.set(0,0,-0.5);cur.add(wh);
   add(new THREE.TorusGeometry(0.175,0.018,10,40),black,[0,0,0],[0,Math.PI/2,0],wh);
   add(new THREE.CylinderGeometry(0.06,0.06,0.05,20),black,[0,0,0],[0,0,Math.PI/2],wh);
   for(const a of[Math.PI/2,Math.PI*1.25,-Math.PI*0.25])add(new THREE.BoxGeometry(0.02,0.13,0.035),alu,[0,0.085*Math.sin(a),0.085*Math.cos(a)],[-a+Math.PI/2,0,0],wh);
   box([0.45,0.03,0.85],[0.6,0.24,0],black);                              // footwell floor
   box([0.03,0.6,0.85],[0.75,0.55,0],black);                              // firewall
   for(const s of[-1,1])box([0.4,0.45,0.03],[0.58,0.48,0.45*s],black);   // kick panels
   box([0.06,0.03,0.08],[0.72,0.3,0.1],alu,[0,0,0.6]);box([0.06,0.03,0.06],[0.72,0.3,0.25],alu,[0,0,0.6]); // pedals
  }
  
  SG("lights");// FRONT, headlights, electronics, steering rack, A-arms ----------
  {const f=add(new THREE.CylinderGeometry(0.62,0.56,0.36,48,1,true,Math.PI*0.2,Math.PI*0.6),green,[0.55,0.82,0]);f.scale.set(1,1,1.08);
   // top lip of fascia
   const lip=[];for(let i=0;i<=30;i++){const a=Math.PI*0.2+i/30*Math.PI*0.6;lip.push(V([0.55+0.62*Math.sin(a),1.0,0.62*Math.cos(a)*1.08]))}
   add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(lip),60,0.022,6,false),green);
   for(const s of[-1,1]){const a=Math.PI/2-0.62*s,x=0.55+0.6*Math.sin(a)+0.01,z=0.6*Math.cos(a)*1.08;
    const hl=add(new THREE.CylinderGeometry(0.09,0.09,0.06,28),plastic,[x-0.02,0.86,z],[0,0,Math.PI/2]);hl.rotation.order='YXZ';hl.rotation.y=-0.62*s;
    add(new THREE.CircleGeometry(0.08,28),lamp,[x+0.015,0.86,z+0.012*s],[0,Math.PI/2-0.62*s,0]);
    add(new THREE.TorusGeometry(0.085,0.007,6,28),M(0xffffff,{emissive:0x99aabb}),[x+0.018,0.86,z+0.013*s],[0,Math.PI/2-0.62*s,0])}
   SG("power");box([0.25,0.18,0.5],[0.85,0.85,0],black);                              // ECU / relay block
   add(new THREE.CylinderGeometry(0.07,0.07,0.16,16),black,[0.88,0.72,-0.2],[Math.PI/2,0,0]); // blower
   box([0.12,0.1,0.12],[0.86,0.72,0.28],M(0xeeeeee,{roughness:.4}));       // washer tank
   for(const s of[-1,1])tube([[0.7,0.92,0.15*s],[0.8,0.8,0.3*s],[0.95,0.75,0.25*s]],0.02,black,0.5); // ducting
   SG("drive");
   add(new THREE.CylinderGeometry(0.028,0.028,0.55,12),black,[0.98,0.3,0],[Math.PI/2,0,0]);
   for(const s of[-1,1]){tube([[0.98,0.3,0.27*s],[0.95,0.29,0.52*s]],0.012,alu);
    tube([[0.75,0.2,0.18*s],[0.87,0.24,0.5*s],[1.0,0.2,0.18*s]],0.016,black); // A-arm
    box([0.08,0.14,0.05],[0.87,0.28,0.52*s],black)}                         // knuckle
  }
  
  SG("frame");// SUSP ----------
  for(const[x,z,t]of[[0.84,0.42,-0.15],[-0.84,0.42,0.2]])for(const s of[-1,1]){
   const g=new THREE.Group();g.position.set(x,0.48,z*s);g.rotation.set(t*s*0.6,0,0);cur.add(g);
   add(new THREE.CylinderGeometry(0.018,0.018,0.38,10),alu,[0,0,0],[0,0,0],g);
   const helix=[];for(let i=0;i<=80;i++){const a=i/80*Math.PI*14;helix.push(V([0.04*Math.cos(a),-0.14+i/80*0.24,0.04*Math.sin(a)]))}
   add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(helix),200,0.007,5,false),spring,[0,0,0],[0,0,0],g)}
  
  SG("drive");// WHEELS: tire + 5 double-spoke two-tone rim ----------
  function wheel(x,z){const s=Math.sign(z),g=new THREE.Group();g.position.set(x,0.28,z);cur.add(g);
   const t=add(new THREE.TorusGeometry(0.21,0.07,16,48),tire,[0,0,0],[0,0,0],g);t.scale.set(1,1,1.35);
   add(new THREE.CylinderGeometry(0.175,0.175,0.15,32,1,true),rimDark,[0,0,0],[Math.PI/2,0,0],g);
   add(new THREE.CylinderGeometry(0.17,0.17,0.01,32),rimDark,[0,0,-0.02*s],[Math.PI/2,0,0],g);
   add(new THREE.TorusGeometry(0.172,0.01,6,40),rim,[0,0,0.07*s],[0,0,0],g);
   for(let i=0;i<5;i++){const a=i/5*Math.PI*2;for(const o of[-0.13,0.13]){
    add(new THREE.BoxGeometry(0.03,0.14,0.012),rim,[0.1*Math.cos(a+o),0.1*Math.sin(a+o),0.055*s],[0,0,a+o-Math.PI/2],g)}}
   add(new THREE.CylinderGeometry(0.045,0.045,0.03,20),rimDark,[0,0,0.065*s],[Math.PI/2,0,0],g);
   add(new THREE.CylinderGeometry(0.03,0.03,0.032,6),alu,[0,0,0.07*s],[Math.PI/2,0,0],g);
   add(new THREE.CylinderGeometry(0.13,0.13,0.01,28),alu,[0,0,-0.04*s],[Math.PI/2,0,0],g);} // brake disc
  for(const x of[0.87,-0.88])for(const z of[-0.6,0.6])wheel(x,z);
  
  
  
  SG("gnss");rbox(0.14,0.035,0.1,0.015,M(0x15191c,{roughness:.3}),[0.2,1.66,0]);

  Object.values(STG).forEach(g => g.visible = false);
  // pulsing highlight zones, one set per phase
  PHASES.forEach(ph => {
    ph.meshes = ph.zones.map(([p, sz]) => {
      const m = new THREE.Mesh(new THREE.BoxGeometry(...sz), new THREE.MeshBasicMaterial({color: MINT, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false}));
      m.position.set(...p); S.add(m);
      const e = new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry), new THREE.LineBasicMaterial({color: MINT, transparent: true, opacity: 0, toneMapped: false}));
      m.add(e); m.userData.e = e; return m;
    });
    ph.anchor = V(ph.zones[0][0]);
  });

  const camGoal = V([3.2, 2.2, 3.8]).multiplyScalar(1.35), tgtGoal = V([-0.1, 0.7, 0]), tgt = tgtGoal.clone();
  cam.position.set(4, 2.6, 4.6);
  const camPos = cam.position.clone(), off = new THREE.Vector3();
  let drag = null, yaw = 0, pitch = 0, visible = true, current = -1, states = {};
  const drops = [];
  R.domElement.style.touchAction = "none";
  R.domElement.addEventListener("pointerdown", e => { drag = [e.clientX, e.clientY, yaw, pitch]; R.domElement.setPointerCapture(e.pointerId); });
  R.domElement.addEventListener("pointerup", () => drag = null);
  R.domElement.addEventListener("pointermove", e => {
    if (!drag) return;
    yaw = drag[2] + (e.clientX - drag[0]) * 0.006;
    pitch = Math.max(-0.4, Math.min(0.5, drag[3] + (e.clientY - drag[1]) * 0.004));
  });
  function size() { const w = stage.clientWidth, h = stage.clientHeight; if (!w || !h) return; R.setSize(w, h); cam.aspect = w / h; cam.updateProjectionMatrix(); }
  const ro = new ResizeObserver(size); ro.observe(stage); size();
  const io = new IntersectionObserver(es => { visible = es[0].isIntersecting; }); io.observe(stage);

  function update(data) {
    const phase = Math.max(0, Math.min(6, data.phase | 0));
    states = data.phases || {};
    // parts of every phase reached so far; newly reached ones drop in from above
    PHASES.forEach((ph, i) => ph.groups.forEach(n => {
      const g = STG[n]; if (!g) return;
      const show = i <= phase;
      if (show && !g.visible) { g.visible = true; if (current >= 0 && !reduced) { g.position.y = 1.4; drops.push({g, t: performance.now()}); } }
      if (!show) { g.visible = false; g.position.y = 0; }
    }));
    PHASES.forEach((ph, i) => {
      const s = states[i];
      const c = s === "fail" ? RED : s === "run" ? AMBER : MINT;
      ph.meshes.forEach(m => { m.material.color.setHex(c); m.userData.e.material.color.setHex(c); });
    });
    if (phase !== current) {
      camGoal.set(...PHASES[phase].cam).multiplyScalar(1.35);
      tgtGoal.copy(PHASES[phase].anchor).lerp(V([-0.1, 0.7, 0]), 0.5);
      current = phase;
    }
  }
  function setColour(hex) { green.color.set(hex).convertSRGBToLinear(); }

  const clock = new THREE.Clock();
  let raf = 0, alive = true;
  (function loop() {
    if (!alive) return;
    raf = requestAnimationFrame(loop);
    if (!visible || document.hidden) return;
    const t = clock.getElapsedTime();
    const k = reduced ? 1 : 0.04;
    tgt.lerp(tgtGoal, k); camPos.lerp(camGoal, k);
    off.copy(camPos).sub(tgt);
    off.applyAxisAngle(V([0, 1, 0]), yaw + (reduced ? 0 : Math.sin(t * 0.2) * 0.08));
    off.y += pitch * off.length();
    cam.position.copy(tgt).add(off); cam.lookAt(tgt);
    const now = performance.now();
    for (let i = drops.length - 1; i >= 0; i--) {
      const d = drops[i], u = Math.min(1, (now - d.t) / 900), e = 1 - Math.pow(1 - u, 3);
      d.g.position.y = 1.4 * (1 - e); if (u >= 1) drops.splice(i, 1);
    }
    PHASES.forEach((ph, i) => {
      // current phase pulses; earlier phases keep a steady tint only when they need attention
      const s = states[i], cur = i === current;
      let target = 0;
      if (s === "run" && cur) target = reduced ? 0.25 : 0.2 + 0.15 * Math.sin(t * 8);
      else if (s === "fail") target = reduced ? 0.35 : 0.3 + 0.2 * Math.sin(t * 6);
      else if (s === "pass" && cur) target = 0.15;
      const edge = (s === "fail" || cur && s) ? 0.9 : 0;
      ph.meshes.forEach(m => {
        m.material.opacity += (target - m.material.opacity) * 0.15;
        m.userData.e.material.opacity += (edge - m.userData.e.material.opacity) * 0.15;
      });
    });
    R.render(S, cam);
  })();

  function destroy() { alive = false; cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); R.dispose(); R.domElement.remove(); }
  return {update, setColour, destroy, phases: PHASES.map(p => p.n)};
}

// Phase states from a session record (GET /api/sessions/{id}): 'fail' | 'run' | 'pass' | null.
function alertPhase(a) {
  if (/^bms_|cell_imbalance/.test(a.rule)) return 3;
  if (a.rule === "dcdc_out_of_range") return 2;
  if (/^temp_/.test(a.rule)) {
    const m = a.message || "";
    return /temp_motor/.test(m) ? 1 : /temp_mppt/.test(m) ? 4 : /temp_dcdc/.test(m) ? 2 : 3;
  }
  return null;
}
function phaseStates(rec) {
  const out = {};
  const crit = (rec.alerts || []).filter(a => !a.acknowledged_at && !a.cleared_at && a.severity === "critical");
  for (let p = 0; p <= 6; p++) {
    const ops = (rec.route || []).filter(o => o.phase === p);
    const fail = (rec.anomalies || []).some(a => !a.closed_at && a.phase === p) || ops.some(o => o.status === "not_ok") ||
                 crit.some(a => alertPhase(a) === p);
    const run = ops.some(o => o.status === "in_progress" || o.status === "unconfirmed");
    const pass = ops.length > 0 && ops.every(o => o.status === "ok");
    out[p] = fail ? "fail" : run ? "run" : pass ? "pass" : null;
  }
  return out;
}
// Stage rail under the twin (same markup as the Check Station).
function rail(el, phase, states) {
  el.innerHTML = PHASES.map((ph, i) => {
    const s = states[i];
    return `<div class="rs${i === phase ? " cur" : ""}${s ? " " + s : ""}"><b>${String(i).padStart(2, "0")}</b>${ph.n}</div>`;
  }).join("");
}

window.BakoTwin = {mount, phaseStates, rail, PHASES: PHASES.map(p => p.n)};
})();
