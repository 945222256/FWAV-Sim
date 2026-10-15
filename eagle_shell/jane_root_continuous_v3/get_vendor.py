"""Fetch pinned public viewer/validator files; no project data is uploaded."""
from pathlib import Path
import urllib.request,json,hashlib
HERE=Path(__file__).resolve().parent
jobs={
 'three/build/three.module.js':'https://unpkg.com/three@0.170.0/build/three.module.js',
 'three/examples/jsm/loaders/GLTFLoader.js':'https://unpkg.com/three@0.170.0/examples/jsm/loaders/GLTFLoader.js',
 'three/examples/jsm/utils/BufferGeometryUtils.js':'https://unpkg.com/three@0.170.0/examples/jsm/utils/BufferGeometryUtils.js',
 'three/examples/jsm/controls/OrbitControls.js':'https://unpkg.com/three@0.170.0/examples/jsm/controls/OrbitControls.js',
 'three/LICENSE':'https://unpkg.com/three@0.170.0/LICENSE',
 'gltf-validator/index.js':'https://unpkg.com/gltf-validator@2.0.0-dev.3.10/index.js',
 'gltf-validator/gltf_validator.dart.js':'https://unpkg.com/gltf-validator@2.0.0-dev.3.10/gltf_validator.dart.js',
 'gltf-validator/LICENSE':'https://unpkg.com/gltf-validator@2.0.0-dev.3.10/LICENSE'
}
manifest=[]
for name,url in jobs.items():
 path=HERE/'vendor/node_modules'/name;path.parent.mkdir(parents=True,exist_ok=True)
 if not path.exists():
  with urllib.request.urlopen(url,timeout=40) as response:data=response.read()
  path.write_bytes(data)
 data=path.read_bytes();manifest.append({'path':name,'url':url,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
 print(name,len(data),flush=True)
(HERE/'vendor/node_modules/three/package.json').write_text(json.dumps({'name':'three','version':'0.170.0','type':'module','exports':{'.':'./build/three.module.js'}}))
(HERE/'vendor/node_modules/gltf-validator/package.json').write_text(json.dumps({'name':'gltf-validator','version':'2.0.0-dev.3.10','main':'index.js'}))
(HERE/'vendor_manifest.json').write_text(json.dumps(manifest,indent=2))
