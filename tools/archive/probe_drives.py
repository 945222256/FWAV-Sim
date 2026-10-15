import os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIAG = os.path.join(ROOT, "logs", "probe_drives.txt")
def log(m):
    with open(DIAG, "a", encoding="utf-8") as f: f.write(m+"\n"); f.flush()
open(DIAG,"w").close()
from isaacsim import SimulationApp
app = SimulationApp({"headless": True})
from pxr import Usd, UsdPhysics
stage = Usd.Stage.Open(os.path.join(ROOT, "assets/flapper/fwav_15dof.usd").replace("\\","/"))
for prim in stage.Traverse():
    if prim.GetTypeName() == "PhysicsRevoluteJoint":
        d = UsdPhysics.DriveAPI.Get(prim, "angular")
        tt = st = da = mf = "N/A"
        if d:
            ta = prim.GetAttribute('physics:type'); tt = ta.Get() if ta else '?'
            st = d.GetStiffnessAttr().Get() if d.GetStiffnessAttr() else "?"
            da = d.GetDampingAttr().Get() if d.GetDampingAttr() else "?"
            mf = d.GetMaxForceAttr().Get() if d.GetMaxForceAttr() else "?"
        log(f"{prim.GetName():10s} targetType={tt} K={st} D={da} maxF={mf}")
app.close()
