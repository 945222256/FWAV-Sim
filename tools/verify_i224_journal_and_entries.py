"""Real/mock entry and record checks with preserved legacy-source comparisons."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tools"))
from config import sim_config as cfg
from verify_i222_execution import NativeFixture
from modules.motion_action.types import load_recipe
from modules.motion_action.bridge import create_execution,publish_native
from modules.motion_action.joint_adapter import JointActionAdapter
from modules.motion_action.journal import ActionRecordingError
from modules.backend.mock_backend import MockBackend


class JournalEntryChecks(unittest.TestCase):
    def setup(self):
        recipe=load_recipe(str(ROOT/"config/motion_recipes/cooperative_v1.json"))
        runtime=create_execution(recipe,cfg,.002)
        art=NativeFixture([r.joint for r in recipe.channels])
        adapter=JointActionAdapter(art,recipe)
        return runtime,adapter,art

    def test_minimum_identity_with_details_off_and_immutable_projection(self):
        runtime,adapter,art=self.setup()
        with tempfile.TemporaryDirectory(dir=ROOT/"logs/kula_direct_chain_20261015") as directory:
            path=Path(directory)/"actions.jsonl"
            runtime.attach_journal(path,detailed=False)
            publish_native(runtime,adapter,1)
            runtime.close()
            records=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records),2)
            self.assertEqual(records[-1]["global_step"],1)
            self.assertEqual(records[-1]["channels"]["tail_yaw"]["requested"],0.)
            self.assertEqual(records[-1]["solver_consumption_step"],"unknown")
            self.assertNotIn("weights",records[-1])
            self.assertTrue(path.with_suffix(".recipe.json").exists())
            with self.assertRaises(TypeError):
                runtime.journal.last_projection["channels"]["tail_yaw"]["requested"]=99.

    def test_record_failure_is_separate_and_does_not_erase_native_effects(self):
        runtime,adapter,art=self.setup()
        class BrokenFile:
            def write(self,*a): raise OSError("injected record write failure")
            def close(self): pass
        with tempfile.TemporaryDirectory(dir=ROOT/"logs/kula_direct_chain_20261015") as directory:
            runtime.attach_journal(Path(directory)/"failed.jsonl")
            runtime.journal._file.close()
            runtime.journal._file=BrokenFile()
            with self.assertRaises(ActionRecordingError): publish_native(runtime,adapter,1)
            self.assertEqual(len(art.calls),1)
            self.assertIsNone(runtime.failed)
            self.assertIsNotNone(runtime.recording_error)
            with self.assertRaises(ActionRecordingError): runtime.prepare_step(2)
            runtime.close()

    def test_actual_main_pin_consumer_and_partial_write_record(self):
        runtime,adapter,art=self.setup()
        runtime.prepare_step(1)
        tree=ast.parse((ROOT/"main_sim.py").read_text(encoding="utf-8"))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_pin_wing_joints")
        module=ast.Module(body=[function],type_ignores=[])
        scope={"_MOTION_RUNTIME":runtime,"_MOTION_ADAPTER":adapter}
        exec(compile(module,"main_sim.py:actual_pin_consumer","exec"),scope)
        receipt=scope["_pin_wing_joints"](art,None,None,.002)
        self.assertEqual([call[0] for call in art.calls],["position","velocity","target"])
        self.assertFalse(receipt.failed)
        art.fail="velocity"
        with self.assertRaises(RuntimeError): scope["_pin_wing_joints"](art,None,None,.002)
        self.assertEqual(runtime.last_receipt.operations["direct_position"]["status"],"api_accepted")
        self.assertIsNotNone(runtime.failed)

    def test_new_mock_auto_consumes_without_old_oscillators_and_reset_changes_identity(self):
        saved=cfg.ActionExpressionConfig.RECIPE_ID
        cfg.ActionExpressionConfig.RECIPE_ID="cooperative_v1"
        backend=MockBackend()
        try:
            backend.setup({})
            backend.reset()
            def forbidden(*a,**k): raise AssertionError("old oscillator used")
            for wing in (backend._wk,backend.model.wing_left,backend.model.wing_right): wing.evaluate=forbidden
            backend.step()
            backend.step()
            self.assertEqual(backend.motion_execution.service.read().step,2)
            self.assertEqual(backend.motion_execution.provider.providers["flap_L"].state()["advance_count"],2)
            self.assertEqual(backend.motion_execution.read().channels["flap_L"].derivative_statuses["flap_angular_acceleration"],"declared_approximation")
            path=backend.motion_execution.journal.path
            identity=backend.motion_execution.run_id
            backend.reset()
            self.assertNotEqual(backend.motion_execution.run_id,identity)
            records=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[-1]["kind"],"mock_force_consumed")
            self.assertEqual(records[-1]["solver_consumption_step"],2)
        finally:
            backend.close()
            cfg.ActionExpressionConfig.RECIPE_ID=saved

    def test_legacy_mock_matches_preserved_prechange_source(self):
        path=ROOT/"logs/kula_direct_chain_20261015/source_checkpoint/modules/backend/mock_backend.py"
        spec=importlib.util.spec_from_file_location("modules.backend._kula_before_mock",path)
        old=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        saved=cfg.ActionExpressionConfig.RECIPE_ID
        cfg.ActionExpressionConfig.RECIPE_ID="legacy_current"
        with patch.dict(os.environ,{"FLAPPER_ACTION_RECIPE":"legacy_current"}):
            before,after=old.MockBackend(),MockBackend()
            try:
                for backend in (before,after): backend.setup({});backend.reset()
                for _ in range(3):
                    before.step({"source":"idle"});after.step({"source":"idle"})
                    for field in ("_pos","_vel_lin","_vel_ang","_joint_pos","_joint_vel","last_F_world","last_T_world"):
                        np.testing.assert_array_equal(getattr(before,field),getattr(after,field))
            finally:
                before.close();after.close();cfg.ActionExpressionConfig.RECIPE_ID=saved

    def test_mock_direct_pin_restores_state_then_release_resets_segment(self):
        saved=cfg.ActionExpressionConfig.RECIPE_ID
        cfg.ActionExpressionConfig.RECIPE_ID="cooperative_v1"
        backend=MockBackend()
        try:
            backend.setup({});backend.reset();backend.step({"source":"idle"})
            expected=backend._joint_pos.copy()
            backend._joint_pos[:]=99.
            backend.pin_wing_joints(backend._t)
            np.testing.assert_array_equal(backend._joint_pos,expected)
            backend.reset_aero_states_for_launch()
            backend.reset_controller_for_launch()
            backend.pin_wing_joints(backend._t)
            self.assertEqual(backend.motion_execution.segment,1)
            self.assertEqual(backend.motion_execution.service.read().step,0)
            backend.step({"source":"idle"})
            self.assertEqual(backend.motion_execution.service.read().step,1)
        finally:
            backend.close();cfg.ActionExpressionConfig.RECIPE_ID=saved


if __name__=="__main__": unittest.main()
