"""Positive-weight local/global surface solve; root fixed, outer orientation fixed.

Optional common outboard translation is a derived deformation, not a new input DOF.
The caller must measure strain, orientation, surface contact and displacement.
"""
import numpy as np
from scipy.sparse import coo_matrix,diags
from scipy.sparse.linalg import splu

class RootARAP:
 def __init__(self,tri,root,rigid_station=.12,allow_translation=False,weight_power=1,root_profile=None):
  flat=tri[:,:,:3].reshape(-1,3)
  _,first,inv=np.unique(np.round(flat,10),axis=0,return_index=True,return_inverse=True)
  self.p=flat[first];self.corner_to_vertex=inv;self.f=inv.reshape(-1,3)
  self.anchor=np.full(len(self.p),root) if root_profile is None else np.interp(self.p[:,0],root_profile['x_m'],root_profile['abs_y_m'])
  self.root=np.abs(self.p[:,1])<=self.anchor+.003
  self.rigid_station=rigid_station
  self.outer=np.abs(self.p[:,1])>=rigid_station
  self.free=~self.root&~self.outer;self.allow_translation=allow_translation
  self.free_ids=np.flatnonzero(self.free)
  edge=np.sort(np.concatenate([self.f[:,[0,1]],self.f[:,[1,2]],self.f[:,[2,0]]]),axis=1)
  self.e=np.unique(edge,axis=0);a,b=self.e.T
  self.d=self.p[a]-self.p[b];self.l0=np.linalg.norm(self.d,axis=1)
  # Inverse edge length balances relative distortion on irregular source triangles.
  self.w=1/np.maximum(self.l0,.0002)**weight_power
  self.w/=np.median(self.w)
  n=len(self.p);degree=np.bincount(np.r_[a,b],weights=np.r_[self.w,self.w],minlength=n)
  adj=coo_matrix((np.r_[self.w,self.w],(np.r_[a,b],np.r_[b,a])),shape=(n,n)).tocsc()
  self.L=diags(degree)-adj
  row=list(self.free_ids);col=list(range(len(row)));val=[1.]*len(row)
  if allow_translation:
   ids=np.flatnonzero(self.outer);row+=ids.tolist();col += [len(self.free_ids)]*len(ids);val += [1.]*len(ids)
  self.A=coo_matrix((val,(row,col)),shape=(n,len(self.free_ids)+int(allow_translation))).tocsc()
  self.lu=splu((self.A.T@self.L@self.A).tocsc())

 def solve(self,R,pivot,iterations=35,initial=None):
  target=(self.p-pivot)@R.T+pivot
  fixed=np.zeros_like(self.p);fixed[self.root]=self.p[self.root];fixed[self.outer]=target[self.outer]
  if initial is None:
   phase=np.clip((np.abs(self.p[:,1])-np.abs(self.p[self.root,1]).max())/(np.abs(self.p[self.outer,1]).min()-np.abs(self.p[self.root,1]).max()),0,1)
   q=self.p+(phase*phase*(3-2*phase))[:,None]*(target-self.p)
  else:q=initial.copy()
  q[self.root]=self.p[self.root]
  if not self.allow_translation:q[self.outer]=target[self.outer]
  a,b=self.e.T
  for _ in range(iterations):
   dq=q[a]-q[b];cov=np.zeros((len(self.p),3,3))
   prod=np.einsum('e,ei,ej->eij',self.w,self.d,dq)
   np.add.at(cov,a,prod);np.add.at(cov,b,prod)
   u,_,vt=np.linalg.svd(cov);rot=vt.transpose(0,2,1)@u.transpose(0,2,1)
   det=np.linalg.det(rot);vt[det<0,-1,:]*=-1;rot=vt.transpose(0,2,1)@u.transpose(0,2,1)
   force=self.w[:,None]*np.einsum('eij,ej->ei',.5*(rot[a]+rot[b]),self.d)
   rhs=np.zeros_like(q);np.add.at(rhs,a,force);np.add.at(rhs,b,-force)
   x=self.lu.solve(np.asarray(self.A.T@(rhs-self.L@fixed)))
   new=self.A@x+fixed
   delta=np.linalg.norm(new-q,axis=1).max();q=new
   if delta<2e-8:break
  t=x[-1] if self.allow_translation else np.zeros(3)
  return q,t

 def metrics(self,q):
  a,b=self.e.T;ratio=np.linalg.norm(q[a]-q[b],axis=1)/self.l0-1
  mid=np.abs(self.p[self.e,1]).mean(1);active=(mid<self.rigid_station)&(self.l0>1e-6)
  rr=ratio[active]
  return {'max_stretch':float(rr.max()),'max_shrink':float(rr.min()),
          'p95_absolute_strain':float(np.percentile(np.abs(rr),95)),
          'minimum_outward_span_m':float(np.abs(q[:,1]).min())}

 def refine_lengths(self,q,R,pivot,iterations=220):
  """Length-target projection with a small local-rigidity term; endpoints held.

  Reweighted stages reduce concentrated strain instead of translating the wing.
  This is a geometric solve, not a material constitutive model.
  """
  target=(self.p-pivot)@R.T+pivot
  fixed=np.zeros_like(self.p);fixed[self.root]=self.p[self.root];fixed[self.outer]=target[self.outer]
  a,b=self.e.T;ww=self.w.copy();q=q.copy();arap_target=self.d.copy()
  for it in range(iterations):
   if it%45==0:
    strain=np.linalg.norm(q[a]-q[b],axis=1)/self.l0-1
    ww=self.w*(1+np.minimum(np.abs(strain)/.03,8)**2)
    deg=np.bincount(np.r_[a,b],weights=np.r_[ww,ww],minlength=len(q))
    adj=coo_matrix((np.r_[ww,ww],(np.r_[a,b],np.r_[b,a])),shape=(len(q),len(q))).tocsc()
    L=diags(deg)-adj;lu=splu((self.A.T@L@self.A).tocsc())
   dq=q[a]-q[b]
   if it%10==0:
    cov=np.zeros((len(q),3,3));prod=np.einsum('e,ei,ej->eij',ww,self.d,dq)
    np.add.at(cov,a,prod);np.add.at(cov,b,prod)
    u,_,vt=np.linalg.svd(cov);rot=vt.transpose(0,2,1)@u.transpose(0,2,1)
    vt[np.linalg.det(rot)<0,-1,:]*=-1;rot=vt.transpose(0,2,1)@u.transpose(0,2,1)
    arap_target=np.einsum('eij,ej->ei',.5*(rot[a]+rot[b]),self.d)
   length_target=dq*(self.l0/np.maximum(np.linalg.norm(dq,axis=1),1e-12))[:,None]
   force=ww[:,None]*(.97*length_target+.03*arap_target)
   rhs=np.zeros_like(q);np.add.at(rhs,a,force);np.add.at(rhs,b,-force)
   x=lu.solve(np.asarray(self.A.T@(rhs-L@fixed)));q=self.A@x+fixed
  return q
