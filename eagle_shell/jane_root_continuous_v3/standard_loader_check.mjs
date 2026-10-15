import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import * as THREE from './vendor/node_modules/three/build/three.module.js';
import { GLTFLoader } from './vendor/node_modules/three/examples/jsm/loaders/GLTFLoader.js';
const here=path.dirname(fileURLToPath(import.meta.url));
const file=path.join(here,'output/eaglex_continuous_root_v3.glb');
const bytes=fs.readFileSync(file);
const require=createRequire(import.meta.url);
const validator=require('./vendor/node_modules/gltf-validator/index.js');
const validation=await validator.validateBytes(new Uint8Array(bytes),{uri:'eaglex_continuous_root_v3.glb',maxIssues:10000});
validation.glb_sha256=createHash('sha256').update(bytes).digest('hex');
fs.writeFileSync(path.join(here,'output/khronos_validation.json'),JSON.stringify(validation,null,2));
console.log(JSON.stringify({validation:validation.issues.numErrors,warnings:validation.issues.numWarnings,firstIssues:validation.issues.messages.slice(0,8)}));
if(validation.issues.numErrors)throw new Error('Khronos validation failed');
// Node has no image decoder. This test is geometry/animation only. The browser
// separately loads the real embedded PNG and renders actual textured pixels.
globalThis.self=globalThis;
globalThis.createImageBitmap=async()=>({width:1,height:1,close(){}});
globalThis.ProgressEvent=class {constructor(type,init={}){this.type=type;Object.assign(this,init);}};
const data=bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength);
const gltf=await new Promise((resolve,reject)=>new GLTFLoader().parse(data,'',resolve,reject));
const expected=JSON.parse(fs.readFileSync(path.join(here,'output/loader_reference.json'),'utf8'));
if(expected.glb_sha256!==createHash('sha256').update(bytes).digest('hex'))throw new Error('Reference version mismatch');
const mixer=new THREE.AnimationMixer(gltf.scene);mixer.clipAction(gltf.animations[0]).play();
const objects={};gltf.scene.traverse(o=>{if(o.isMesh)objects[o.name]=o;});
let max=0,count=0;
for(const frame of expected.frames){
 mixer.setTime(frame.time_s);gltf.scene.updateMatrixWorld(true);
 gltf.scene.traverse(o=>{if(o.isSkinnedMesh)o.skeleton.update();});
 for(const [name,ref] of Object.entries(frame.parts)){
  const mesh=objects[name];if(!mesh)throw new Error('Missing mesh '+name);
  for(let i=0;i<ref.indices.length;i++){
   const p=new THREE.Vector3();mesh.getVertexPosition(ref.indices[i],p);p.applyMatrix4(mesh.matrixWorld);
   const q=ref.xyz_gltf[i];const error=Math.hypot(p.x-q[0],p.y-q[1],p.z-q[2]);max=Math.max(max,error);count++;
  }
 }
}
const result={glb_sha256:createHash('sha256').update(bytes).digest('hex'),loader:'unmodified Three.js GLTFLoader 0.170.0',sampled_positions:count,max_position_error_m:max,
 geometry_only_image_stub:true,browser_texture_check_required:true,passed:max<2e-6};
fs.writeFileSync(path.join(here,'output/standard_loader_check.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify(result));if(!result.passed)throw new Error('Standard loader disagrees with independent runtime');
