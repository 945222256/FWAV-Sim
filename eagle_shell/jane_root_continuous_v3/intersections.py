"""AABB hierarchy and exact non-adjacent triangle tests for pose diagnostics."""
import numpy as np
from shapely.geometry import Polygon

def candidates(tris,ids,moving):
 lo=tris.min(1)-1e-10;hi=tris.max(1)+1e-10;cen=(lo+hi)/2;nodes=[]
 def build(which):
  index=len(nodes);nodes.append(None);l=lo[which].min(0);h=hi[which].max(0)
  if len(which)<=8:nodes[index]=(l,h,which,None,None,bool(moving[which].any()))
  else:
   axis=np.argmax(h-l);order=which[np.argsort(cen[which,axis])];mid=len(order)//2
   a=build(order[:mid]);b=build(order[mid:]);nodes[index]=(l,h,None,a,b,bool(moving[which].any()))
  return index
 root=build(np.arange(len(tris)));stack=[(root,root)];out=[]
 while stack:
  i,j=stack.pop();a=nodes[i];b=nodes[j]
  if not(a[5] or b[5]) or np.any(a[0]>b[1]) or np.any(b[0]>a[1]):continue
  if i==j and a[2] is None:
   stack.extend([(a[3],a[3]),(a[3],a[4]),(a[4],a[4])]);continue
  if a[2] is not None and b[2] is not None:
   ia,ib=np.meshgrid(a[2],b[2],indexing='ij');ia=ia.ravel();ib=ib.ravel()
   good=(ia<ib) if i==j else np.ones(len(ia),dtype=bool)
   good&=moving[ia]|moving[ib];good&=np.all(lo[ia]<=hi[ib],axis=1)&np.all(lo[ib]<=hi[ia],axis=1)
   shared=np.any(ids[ia,:,None]==ids[ib,None,:],axis=(1,2));good&=~shared
   if good.any():out.append(np.sort(np.stack([ia[good],ib[good]],axis=1),axis=1))
  elif b[2] is not None or (a[2] is None and np.prod(a[1]-a[0])>=np.prod(b[1]-b[0])):
   stack.extend([(a[3],j),(a[4],j)])
  else:stack.extend([(i,b[3]),(i,b[4])])
 return np.unique(np.concatenate(out),axis=0) if out else np.empty((0,2),int)

def intersects(tris,pairs):
 if not len(pairs):return pairs
 A=tris[pairs[:,0]];B=tris[pairs[:,1]];hit=np.zeros(len(pairs),bool)
 def segment_hits(P,T):
  ok=np.zeros(len(P),bool);e1=T[:,1]-T[:,0];e2=T[:,2]-T[:,0]
  for k in range(3):
   origin=P[:,k];d=P[:,(k+1)%3]-origin;h=np.cross(d,e2);det=np.einsum('ij,ij->i',e1,h)
   good=np.abs(det)>1e-14;inv=np.zeros_like(det);inv[good]=1/det[good]
   s=origin-T[:,0];u=np.einsum('ij,ij->i',s,h)*inv;q=np.cross(s,e1)
   v=np.einsum('ij,ij->i',d,q)*inv;t=np.einsum('ij,ij->i',e2,q)*inv
   ok|=good&(u>1e-7)&(v>1e-7)&(u+v<1-1e-7)&(t>1e-6)&(t<1-1e-6)
  return ok
 hit|=segment_hits(A,B)|segment_hits(B,A)
 normal=np.cross(A[:,1]-A[:,0],A[:,2]-A[:,0]);normal/=np.maximum(np.linalg.norm(normal,axis=1)[:,None],1e-15)
 coplanar=np.max(np.abs(np.einsum('nki,ni->nk',B-A[:,0,None,:],normal)),axis=1)<1e-9
 for i in np.flatnonzero(coplanar&~hit):
  axes=[k for k in range(3) if k!=np.argmax(np.abs(normal[i]))]
  if Polygon(A[i][:,axes]).intersection(Polygon(B[i][:,axes])).area>1e-12:hit[i]=True
 return pairs[hit]
