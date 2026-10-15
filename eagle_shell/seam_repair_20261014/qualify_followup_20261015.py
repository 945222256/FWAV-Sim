"""Add measured area/ownership qualifications; no production or mesh mutation."""
from pathlib import Path
import json
import numpy as np
from glb_surface import Model

HERE=Path(__file__).resolve().parent;OUT=HERE/'symmetry_20261015'
path=OUT/'symmetry_root_and_strain.json';result=json.loads(path.read_text(encoding='utf-8'))
trial=Model(HERE/'after/eaglex_root_lbs_trial.glb');poses={}
for key,entry in result['versions']['rejected_trial']['main_wing_edge_strain_by_mode'].items():
 side=key[-1];name='eagle_wing_main_'+side;v=trial.bind[name];f=trial.parts[name]['faces']
 e=np.concatenate([f[:,[0,1]],f[:,[1,2]],f[:,[2,0]]]);l0=np.linalg.norm(v[e[:,0]]-v[e[:,1]],axis=1)
 e=e[l0>1e-7];l0=l0[l0>1e-7]
 if 'stretch_frame' in entry:
  ti=entry['stretch_frame']
  if ti not in poses:poses[ti]=trial.world(ti)
  p=poses[ti][name];ratio=np.linalg.norm(p[e[:,0]]-p[e[:,1]],axis=1)/l0-1
  ix=int(ratio.argmax());entry['stretch_edge_bind_xyz_m']=v[e[ix]].tolist()
  entry['stretch_edge_bind_midpoint_xyz_m']=v[e[ix]].mean(0).tolist()
result['partition_change']={}
for side in ['L','R']:
 before=result['versions']['before'];after=result['versions']['recut']
 old=before['parts']['main_'+side]['projected_area_m2'];new=after['parts']['main_'+side]['projected_area_m2']
 result['partition_change'][side]={'main_area_reduction_percent':100*(old-new)/old,
  'root_boundary_mean_outward_shift_m':after['root_boundary'][side]['length_weighted_mean_abs_span_m']-before['root_boundary'][side]['length_weighted_mean_abs_span_m']}
result['candidate_qualification']={'static_topological_continuity_verified':True,'final_partition_accepted':False,
 'reason':'Root cut at |y|=.06 reclassifies inboard wing as fixed body; actual wing-body interface and strict symmetry are not established.',
 'trial_material_limit_known':False,'trial_5_percent_gate_is_measured_material_limit':False}
path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')

oldpath=HERE/'after/recut_and_trial_checks.json';prior=json.loads(oldpath.read_text(encoding='utf-8'))
prior['followup_20261015']={'final_partition_accepted':False,'source':'../symmetry_20261015/symmetry_root_and_strain.json',
 'qualification':'Continuous static cut only. |y|=.06 is the existing joint station, not a verified skin interface. Moving wing area was reduced by assigning inboard wing to fixed body.',
 'whole_frame_trial_peak_stretch_fraction':result['versions']['rejected_trial']['main_wing_edge_strain_by_mode']['TWIST_INHERIT/L']['max_stretch_fraction']}
oldpath.write_text(json.dumps(prior,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'partition_change':result['partition_change'],
 'trial_incidence_peak':result['versions']['rejected_trial']['main_wing_edge_strain_by_mode']['TWIST_INHERIT/L']},ensure_ascii=False,indent=2))
