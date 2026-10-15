"""Derived smooth centre-line correction; no additional actuation channel."""
import numpy as np
from scipy.interpolate import CubicSpline
from scipy.optimize import least_squares

def refine(solver,q0,rotation_vector=None,maxeval=45):
 p=solver.p;e=solver.e;l0=solver.l0
 knots=np.linspace(.030,solver.rigid_station,9)
 yy=np.clip(np.abs(p[:,1]),knots[0],knots[-1])
 controls=np.zeros((9,7));controls[1:-1]=np.eye(7)
 B=CubicSpline(knots,controls,bc_type=((1,np.zeros(7)),(1,np.zeros(7))))(yy)
 B[solver.root|solver.outer]=0
 keep=l0>2e-6;a,b=e[keep].T;ll=l0[keep];D=B[a]-B[b];d0=q0[a]-q0[b]
 mask=np.ones(3)
 if rotation_vector is not None:
  axis=int(np.argmax(np.abs(rotation_vector)))
  if np.linalg.norm(np.delete(rotation_vector,axis))<1e-7:mask[axis]=0
 def fun(x):
  d=d0+D@(x.reshape(7,3)*mask);s=np.linalg.norm(d,axis=1)/ll-1
  return np.r_[s*np.sqrt(1+(s/.035)**2),x*2]
 def jac(x):
  d=d0+D@(x.reshape(7,3)*mask);length=np.linalg.norm(d,axis=1);s=length/ll-1
  mul=(1+2*(s/.035)**2)/np.sqrt(1+(s/.035)**2)
  direction=d/np.maximum(length*ll,1e-16)[:,None]*mul[:,None]
  j=(D[:,:,None]*direction[:,None,:]*mask).reshape(-1,21)
  return np.concatenate([j,np.eye(21)*2])
 opt=least_squares(fun,np.zeros(21),jac=jac,bounds=(-.020,.020),max_nfev=maxeval,ftol=1e-7,xtol=1e-7,gtol=1e-7)
 shift=B@(opt.x.reshape(7,3)*mask)
 return q0+shift,{'nfev':opt.nfev,'maximum_derived_section_shift_m':float(np.linalg.norm(shift,axis=1).max()),'status':opt.status}
