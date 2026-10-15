import hashlib,io,json,struct
import numpy as np
from PIL import Image
from gltf_runtime import Model
from surface_source import ROOT,OUT
m=Model(OUT/'eaglex_continuous_root_v3.glb');parts=dict(np.load(OUT/'canonical_parts.npz'))
maximum=0.
for name,t in parts.items():
 expected=t[:,:,3:5].reshape(-1,2).copy();expected[:,1]=1-expected[:,1]
 maximum=max(maximum,float(np.max(np.abs(m.parts[name]['uv']-expected))))
def embedded(path):
 b=path.read_bytes();jl=struct.unpack_from('<I',b,12)[0];g=json.loads(b[20:20+jl]);blob=b[28+jl:]
 tex=g['materials'][0]['pbrMetallicRoughness']['baseColorTexture']['index'];im=g['textures'][tex]['source']
 v=g['bufferViews'][g['images'][im]['bufferView']];image=blob[v.get('byteOffset',0):v.get('byteOffset',0)+v['byteLength']]
 return g,image
source,raw=embedded(ROOT/'assets/eagleX_raw_50k/cfd068548a51f523e76e53b2c9c2f559.glb');export,current=embedded(m.path)
src=np.asarray(Image.open(io.BytesIO(raw)).convert('RGBA'));dst=np.asarray(Image.open(io.BytesIO(current)).convert('RGBA'))
print(json.dumps({'source_dimensions':list(src.shape),'export_dimensions':list(dst.shape),'source_image_sha':hashlib.sha256(raw).hexdigest(),'export_image_sha':hashlib.sha256(current).hexdigest(),
 'flipped_pixels_identical':bool(src.shape==dst.shape and np.array_equal(src[::-1],dst))}),flush=True)
result={'glb_sha256':hashlib.sha256(m.path.read_bytes()).hexdigest(),'maximum_uv_export_error':maximum,
 'source_texture_pixels_identical':bool(np.array_equal(src,dst)),'source_alpha_range':[int(src[:,:,3].min()),int(src[:,:,3].max())],
 'export_alpha_mode':export['materials'][0].get('alphaMode','OPAQUE'),'passed':maximum<1e-7 and np.array_equal(src,dst)}
assert result['passed'],result
(OUT/'texture_checks.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
