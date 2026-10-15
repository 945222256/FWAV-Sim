import json
import numpy as np
from scipy.sparse import coo_matrix,vstack,eye
from scipy.optimize import least_squares
from surface_source import OUT,ROOT,C,Model
from arap import RootARAP

def refine(solver,q0,R,pivot,maxeval=35):
 p=solver.p;e=solver.e;l0=solver.l0;free=solver.free_ids;n=len(free)
 index=np.full(len(p),-1,dtype=int);index[free]=np.arange(n)
 aa,bb=e.T
 target=(p-pivot)@R.T+pivot
 fixed=q0.copy();fixed[solver.root]=p[solver.root];fixed[solver.outer]=target[solver.outer]
 def positions(x):
  q=fixed.copy();q[free]=x.reshape(-1,3);return q
 keep=l0>2e-6;edge=e[keep];ll=l0[keep];a,b=edge.T
 slots=[]
 for which,sgn in [(a,1),(b,-1)]:
  good=index[which]>=0
  for k in range(3):slots.append((np.flatnonzero(good),index[which[good]]*3+k,sgn,k))
 def fun(x):
  q=positions(x);length=np.linalg.norm(q[a]-q[b],axis=1);s=length/ll-1
  residual=s*np.sqrt(1+(s/.035)**2)
  return np.r_[residual,.02*(x-q0[free].ravel())/.05]
 def jac(x):
  q=positions(x);d=q[a]-q[b];length=np.linalg.norm(d,axis=1);s=length/ll-1
  mul=(1+2*(s/.035)**2)/np.sqrt(1+(s/.035)**2)
  deriv=d/np.maximum(length*ll,1e-16)[:,None]*mul[:,None]
  rows=[];cols=[];values=[]
  for ri,ci,sgn,k in slots:
   rows.append(ri);cols.append(ci);values.append(sgn*deriv[ri,k])
  J=coo_matrix((np.concatenate(values),(np.concatenate(rows),np.concatenate(cols))),shape=(len(edge),3*n)).tocsr()
  return vstack([J,eye(3*n,format='csr')*.4],format='csr')
 opt=least_squares(fun,q0[free].ravel(),jac=jac,method='trf',tr_solver='lsmr',x_scale='jac',
  max_nfev=maxeval,ftol=1e-5,xtol=1e-7,gtol=1e-6,tr_options={'maxiter':300,'atol':1e-5,'btol':1e-5})
 return positions(opt.x),{'nfev':opt.nfev,'cost':opt.cost,'status':opt.status}

if __name__=='__main__':
 parts=dict(np.load(OUT/'canonical_parts.npz'));meta=json.loads((OUT/'canonical_source.json').read_text())
 old=Model(ROOT/'eagle_shell/l8_i201_animv2/output/eaglex_anim_config_v2.glb');ni=old.parts['eagle_wing_main_R']['ni'];pivot=old.restG[ni][1]@C
 tri=np.concatenate([parts['eagle_wing_main_R'],parts['eagle_wing_tip_R']]);solver=RootARAP(tri,meta['root_station_abs_y_m'],.24,False,2)
 ti=37;R=C.T@old.globals(ti)[ni][0]@C;q,_=solver.solve(R,pivot,130)
 print('before',solver.metrics(q),flush=True)
 q,info=refine(solver,q,R,pivot,maxeval=45)
 print('after',solver.metrics(q),info,flush=True)
 np.savez_compressed(OUT/'probe_nonlinear_37.npz',p=solver.p,f=solver.f,q=q)
 (OUT/'nonlinear_probe.json').write_text(json.dumps({'metrics':solver.metrics(q),'optimizer':info},indent=2))
