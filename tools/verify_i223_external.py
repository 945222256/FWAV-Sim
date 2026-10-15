"""Exercise actual model and callback consumers with isolated native API fixtures."""
import sys
from pathlib import Path
from dataclasses import replace
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from config import sim_config as cfg
from modules.motion_action.types import load_recipe,StepAction
from modules.motion_action.bridge import create_execution
from modules.motion_action.aero_adapter import ExternalActionView,compute_action_window
from modules.aero.sane_dickinson_3d import SaneDickinson3DModel
from modules.aero_solver import AeroPhysicsCallback


class NativeBodyFixture:
    def __init__(self,fail=False): self.calls=[]; self.fail=fail
    def get_world_poses(self): return np.array([[0.,0.,2.]]),np.array([[1.,0.,0.,0.]])
    def get_velocities(self): return np.zeros((1,6))
    def apply_forces_and_torques_at_pos(self,**kwargs):
        self.calls.append(kwargs)
        if self.fail: raise RuntimeError("injected force API failure")


class ExternalChecks(unittest.TestCase):
    def setup(self):
        recipe=load_recipe(str(ROOT/"config/motion_recipes/cooperative_v1.json"))
        runtime=create_execution(recipe,cfg,.002)
        runtime.prepare_step(1)
        model=SaneDickinson3DModel()
        return runtime,model

    def test_actual_model_bypasses_old_waves_and_restores_objects(self):
        runtime,model=self.setup()
        left,right=model.wing_left,model.wing_right
        def forbidden(*a,**k): raise AssertionError("legacy evaluate called")
        left.evaluate=right.evaluate=forbidden
        before=(left._tau_state,left._t_prev,right._tau_state,right._t_prev)
        view=ExternalActionView.from_execution(runtime)
        f,t=model.compute_forces(np.zeros(3),[1,0,0,0],np.zeros(3),np.zeros(3),.002,action_view=view)
        self.assertTrue(np.all(np.isfinite(f)))
        self.assertIs(model.wing_left,left)
        self.assertIs(model.wing_right,right)
        self.assertEqual(before,(left._tau_state,left._t_prev,right._tau_state,right._t_prev))
        with self.assertRaises(AssertionError):
            model.compute_forces(np.zeros(3),[1,0,0,0],np.zeros(3),np.zeros(3),.002)

    def test_exact_eight_point_window_and_state_cache_isolation(self):
        runtime,model=self.setup()
        before=runtime.provider.providers["flap_L"].primitive.state()
        old_force=model.current_F_world.copy()
        f,t,meta=compute_action_window(model,runtime,np.zeros(3),[1,0,0,0],np.zeros(3),np.zeros(3))
        self.assertEqual(meta["K"],8)
        for k,s in enumerate(meta["sources"]):
            self.assertAlmostEqual(s["t_s"],runtime.read().t_s+k*meta["reference_period_s"]/8.)
        self.assertEqual(before,runtime.provider.providers["flap_L"].primitive.state())
        np.testing.assert_array_equal(old_force,model.current_F_world)
        self.assertFalse(meta["fold_geometry_consumed"])

    def test_required_derivatives_and_actual_model_switches(self):
        runtime,model=self.setup()
        view=ExternalActionView.from_execution(runtime)
        channels=dict(view.frame.channels)
        channels["flap_L"]=replace(channels["flap_L"],acceleration=None,
            derivative_statuses={"flap_angular_velocity":"available","flap_angular_acceleration":"missing"})
        bad=replace(view,frame=StepAction(view.frame.step,view.frame.t_s,view.frame.rhythm,channels))
        with self.assertRaises(RuntimeError):
            model.compute_forces(np.zeros(3),[1,0,0,0],np.zeros(3),np.zeros(3),.002,action_view=bad)
        model.enable_added_mass=False
        f,t=model.compute_forces(np.zeros(3),[1,0,0,0],np.zeros(3),np.zeros(3),.002,action_view=bad)
        self.assertTrue(np.all(np.isfinite(f)))

    def test_callback_deferral_merge_duplicate_and_failure_receipts(self):
        runtime,model=self.setup()
        body=NativeBodyFixture()
        callback=AeroPhysicsCallback(None,None,body)
        callback.model=model
        callback.motion_execution=runtime
        callback._suppress_apply=True
        callback(.002)
        self.assertEqual(callback.motion_last_force_receipt["status"],"deferred")
        self.assertEqual(len(body.calls),0)
        callback._suppress_apply=False
        callback(.002)
        receipt=callback.motion_last_force_receipt
        self.assertEqual(receipt["status"],"api_accepted")
        self.assertEqual(len(receipt["source_frames"]),2)
        self.assertEqual(receipt["solver_consumption_step"],"unknown")
        self.assertEqual(runtime.provider.providers["flap_L"].state()["advance_count"],1)
        body.fail=True
        with self.assertRaises(RuntimeError): callback(.002)
        self.assertEqual(callback.motion_last_force_receipt["status"],"api_failed")
        self.assertIsNotNone(runtime.failed)


if __name__=="__main__": unittest.main()
