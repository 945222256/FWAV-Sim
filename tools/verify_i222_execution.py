"""Direct production-runtime checks, without Isaac imports or agent dispatch."""
import copy
from pathlib import Path
import sys
import unittest
from dataclasses import replace
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from config import sim_config as cfg
from modules.motion_action.types import load_recipe
from modules.motion_action.execution import MotionExecution, baseline_from_config, enqueue_controller_demand
from modules.motion_action.joint_adapter import JointActionAdapter
from modules.motion_action.rhythm import ParamProgramStep


class NativeFixture:
    def __init__(self, names, fail=None):
        self.joint_names = names
        self.calls = []
        self.fail = fail
    def _call(self, key, values):
        self.calls.append((key, values))
        if self.fail == key:
            raise RuntimeError("injected native failure")
    def set_joint_position_targets(self, **values): self._call("target",values)
    def set_joint_positions(self, **values): self._call("position",values)
    def set_joint_velocities(self, **values): self._call("velocity",values)


class ExecutionChecks(unittest.TestCase):
    def setup(self):
        recipe = load_recipe(str(ROOT/"config/motion_recipes/cooperative_v1.json"))
        runtime = MotionExecution(recipe, dt=.002, baseline=baseline_from_config(cfg),
            split=cfg.FlapKinematicsConfig.SPLIT,
            alpha_set_rad=np.radians(cfg.FlapKinematicsConfig.ALPHA_SET),
            pitch_bias_rad=np.radians(cfg.FlapKinematicsConfig.FEATHER_BIAS))
        return runtime, recipe

    def test_actual_default_negative_lag_and_shared_step(self):
        runtime, recipe = self.setup()
        for i in range(1,6): runtime.warmup_step(i)
        for step in range(1,21):
            frame = runtime.prepare_step(step)
            self.assertIs(runtime.prepare_step(step),frame)
        self.assertEqual(runtime.provider.providers["flap_L"].primitive.state()["advance_count"],20)
        self.assertEqual(len(runtime.warmup_routes),5)
        self.assertIsNotNone(frame.channels["twist_L"].velocity)
        self.assertLess(runtime.parameters["twist_L"].lag_s,0.)
        clone = runtime.service.clone()
        sample = clone.read()
        clock, boundary = runtime._lag_clock(clone,runtime._history,sample,-.01)
        self.assertAlmostEqual(clock.chi,sample.chi_c+sample.frequency_hz*.01)
        self.assertEqual(runtime.service.read(),sample)

    def test_queue_timing_priorities_zero_and_mode_exit(self):
        runtime, _ = self.setup()
        runtime.prepare_step(1)
        packet=enqueue_controller_demand(runtime,cfg,after_step=1,
            ap_out=dict(freq=2.,elevator=.2,aileron=.03,rudder=.04,dA=.05,dlag=.01,dfreq=.2),
            manual=dict(throttle=.9,elevator=-.9),backup=dict(freq=1.4))
        self.assertEqual(runtime.service.read().frequency_hz,cfg.WING_DRIVE_CONFIG["frequency"])
        runtime.prepare_step(2)
        expected=cfg.MANUAL_FREQ_MIN+(cfg.MANUAL_FREQ_MAX-cfg.MANUAL_FREQ_MIN)*1.9/2.
        self.assertAlmostEqual(runtime.service.read().frequency_hz,expected)
        self.assertAlmostEqual(runtime.parameters["tail_L"].control_correction,.23)
        enqueue_controller_demand(runtime,cfg,after_step=2,backup=dict(freq=1.45))
        runtime.prepare_step(3)
        self.assertEqual(runtime.service.read().frequency_hz,1.45)
        self.assertEqual(runtime.service.read().dfreq_hz,0.)
        self.assertEqual(runtime.parameters["flap_L"].legacy_angular_offset,0.)
        self.assertAlmostEqual(runtime.parameters["twist_L"].lag_s,cfg.FlapKinematicsConfig.PITCH_LAG_S)

    def test_known_future_program_not_overwritten_by_held_demand(self):
        runtime, _=self.setup()
        runtime.service.schedule_param_step(ParamProgramStep(2,"freq_hz",3.))
        runtime.prepare_step(1)
        runtime.prepare_step(2)
        self.assertEqual(runtime.service.read().frequency_hz,3.)
        runtime.prepare_step(3)
        self.assertEqual(runtime.service.read().frequency_hz,3.)

    def test_native_named_mirrored_targets_and_partial_pin_failure(self):
        runtime, recipe=self.setup()
        frame=runtime.prepare_step(1)
        art=NativeFixture(["unrelated"]+[r.joint for r in reversed(recipe.channels)])
        adapter=JointActionAdapter(art,recipe)
        result=adapter.submit(frame)
        self.assertFalse(result.failed)
        values=art.calls[0][1]
        self.assertNotIn("unrelated",values["joint_names"])
        self.assertEqual(values["positions"].dtype,np.float32)
        self.assertAlmostEqual(result.channels["flap_R"]["native_value"],-frame.channels["flap_R"].value,places=6)
        art.fail="velocity"
        receipt=adapter.submit(frame,pin=True)
        self.assertTrue(receipt.failed)
        self.assertEqual(receipt.operations["direct_position"]["status"],"api_accepted")
        self.assertEqual(receipt.operations["direct_velocity"]["status"],"api_failed")
        self.assertNotIn("position_target",receipt.operations)
        runtime.record_consumption(receipt)
        with self.assertRaises(RuntimeError): runtime.prepare_step(2)

    def test_positive_unknown_history_stops_before_publication(self):
        runtime,_=self.setup()
        runtime.baseline["pitch_lag_s"]=.1
        with self.assertRaisesRegex(RuntimeError,"uninitialized"):
            runtime.prepare_step(1)
        self.assertEqual(runtime.service.read().step,0)
        with self.assertRaises(RuntimeError): runtime.read()

    def test_release_resets_phase_queue_and_segment_not_global_identity(self):
        runtime,_=self.setup()
        runtime.prepare_step(1)
        runtime.enqueue(after_step=1,command=dict(rudder=0.,dA=.1),frequencies=dict(auto=2.5),source="auto")
        run_id=runtime.run_id
        runtime.release()
        self.assertEqual(runtime.segment,1)
        self.assertEqual(runtime.run_id,run_id)
        self.assertEqual(runtime.service.read().step,0)
        self.assertEqual(runtime._queue,[])
        frame=runtime.prepare_step(2)
        self.assertEqual(frame.step,1)
        self.assertEqual(runtime._global_step,2)
        self.assertEqual(runtime.parameters["flap_L"].legacy_angular_offset,0.)

    def test_zero_gate_is_explicit_hold_and_edge_derivative_is_unavailable(self):
        runtime,_=self.setup()
        runtime.derivative_policy="backward_difference_v1"
        runtime.prepare_step(1)
        runtime.enqueue(after_step=1,command=dict(rudder=0.,gate_L=0.),frequencies={},source="external")
        frame=runtime.prepare_step(2)
        self.assertEqual(runtime.parameters["flap_L"].gate_value,0.)
        self.assertAlmostEqual(frame.channels["flap_L"].value,runtime.parameters["flap_L"].center)
        self.assertIsNone(frame.channels["flap_L"].velocity)
        self.assertFalse(frame.channels["flap_L"].derivative_available)
        frame=runtime.prepare_step(3)
        self.assertEqual(frame.channels["flap_L"].velocity,0.)
        self.assertEqual(runtime.provider.providers["flap_L"].state()["advance_count"],3)

    def test_backup_snapshot_and_explicit_external_zero_survive_queue(self):
        runtime,_=self.setup()
        runtime.prepare_step(1)
        packet=enqueue_controller_demand(runtime,cfg,after_step=1,backup=dict(freq=1.4,version="backup-v7",mode="altitude"))
        self.assertEqual(packet.source_info["backup_snapshot"]["version"],"backup-v7")
        runtime.prepare_step(2)
        self.assertEqual(runtime.service.read().frequency_hz,1.4)
        runtime.enqueue(after_step=2,command=dict(rudder=0.,elevator=0.,aileron=0.,dA=0.,dfreq=0.),
            frequencies=dict(manual=1.5),source="external")
        runtime.prepare_step(3)
        self.assertEqual(runtime.parameters["tail_L"].control_correction,0.)
        self.assertEqual(runtime.parameters["tail_R"].control_correction,0.)


if __name__=="__main__":
    unittest.main()
