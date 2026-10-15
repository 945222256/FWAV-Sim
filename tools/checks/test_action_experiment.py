"""实验接口的反例回归；此处假数据只验工具，不构成 I-187 真实验收。"""
import copy
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from modules.experiment_pipeline import action_recipe as R
from modules.experiment_pipeline import action_analysis as A


def fixture():
    recipe = R.read_json(ROOT / "config/experiments/global_frequency_v1.json")
    recipe["sampling"] = {"physics_dt_s": 0.1, "settle_s": 1.0, "measure_s": 2.0, "minimum_cycles": 1}
    case = R.compile_plan(recipe)["cases"][0]
    events = []
    def native(step):
        return {"schema": "i210.native_clock/1", "status": "observed", "query_errors": [],
                "physics_core": {"simulation_count": 1, "simulations": [{"id": 1, "name": "PhysX", "active": True,
                                                                      "step_count": step, "timestamp": step}]}}
    for step in range(1, 31):
        p = {"selection": "manual", "target_api_status": "api_succeeded", "target_api_error": None,
             "ap_out": None, "manual_channel": {"throttle": 0.0, "elevator": 0.0, "aileron": 0.0, "rudder": 0.0},
             "query_errors": {}, "joint_names": ["joint%d" % i for i in range(9)],
             "target_api_float32_rad": [[math.sin(step * 0.1)] * 9],
             "actual_readback": {"joint_position_rad": [[math.sin((step - 1) * 0.1)] * 9],
                                 "joint_velocity_rad_s": [[0.0] * 9]},
             "frequency_after_set_hz": 1.9, "frequency_consumed_by_evaluate_hz": 1.9}
        p["aero_wing_readback"] = {s: {"amplitude": 1.0, "pitch_lag_s": 0.0, "stroke_gate": 1.0} for s in ("L", "R")}
        events.append({"kind": "drive", "clock": {"main_step": step, "main_t_s": step * 0.1, "phys_dt_s": 0.1},
                       "payload": p, "native_clock": native(step)})
        p = {"status": "api_succeeded", "body_read_error": None, "tail_status": "computed", "tail_error": None,
             "model_type": "modules.aero.sane_dickinson_3d.SaneDickinson3DModel",
             "clipped": False, "model_input": {"position_world_m": case["fixed"]["initial_position_world_m"], "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                           "air_velocity_world_m_s": [0.0] * 3, "angular_velocity_world_rad_s": [0.0] * 3},
             "wing_readback": {s: {"frequency": 1.9} for s in ("L", "R")},
             "force_api_status": "api_succeeded", "torque_api_status": "api_succeeded",
             "submitted_F_float32_N": [[1.0, 2.0, 3.0]], "submitted_T_float32_Nm": [[4.0, 5.0, 6.0]]}
        events.append({"kind": "aero_apply", "clock": {"last_completed_main_step": step, "callback_index": step},
                       "payload": p, "native_clock": native(step)})
    params = {"fixed": 1, "MANUAL_ELEV_GAIN": 1.0, "MANUAL_AIL_GAIN": 1.0, "MANUAL_RUDD_GAIN": 1.0}
    conditions = {"complete": True, "errors": [], "samples": 30, "expected_samples": 30,
                  "fixed_adopted": A.expected_conditions(case), "max_body_reset_error": 0.0,
                  "world_physics_dt_s": 0.1, "effective_parameters": params,
                  "effective_parameters_sha256": R.canonical_hash(params)}
    return recipe, case, events, conditions


class RecipeTests(unittest.TestCase):
    def setUp(self):
        self.recipe, self.case, self.events, self.conditions = fixture()

    def result(self):
        return A.analyze_events(self.events, self.case, self.recipe["qualification"], self.conditions)

    def test_real_recipe_compiles_four_approved_cases(self):
        plan = R.compile_plan(R.read_json(ROOT / "config/experiments/global_frequency_v1.json"))
        self.assertEqual([c["frequency_hz"] for c in plan["cases"]], [1.9, 2.0, 1.7999999999999998, 1.95])

    def test_invalid_recipe_fails_without_substitution(self):
        mutations = [("variable", "unit", "rad"), ("variable", "name", "amplitude"),
                     ("variable", "delta", float("nan")), ("variable", "input_range_hz", [1.8, 1.9]),
                     ("fixed", "wind_world_m_per_s", [1, 0, 0]), ("fixed", "controller_enabled", True),
                     ("fixed", "body_free_dofs", {"tx": False}), ("sampling", "physics_dt_s", 0),
                     ("sampling", "measure_s", 2.03), ("qualification", "approved_misses", {"tgt": 1}),
                     ("qualification", "tracking_error_limit_rad", -1), ("qualification", "approved_misses", {})]
        for section, key, value in mutations:
            with self.subTest(key=key, value=value):
                bad = copy.deepcopy(self.recipe)
                bad[section][key] = value
                with self.assertRaises(ValueError):
                    R.compile_plan(bad)

    def test_unknown_fields_are_not_ignored(self):
        self.recipe["fixed"]["unapproved"] = 0
        with self.assertRaises(ValueError):
            R.compile_plan(self.recipe)

    def test_full_key_type_and_length_comparison(self):
        for actual in ({"a": [1]}, {"a": [1, 2], "b": 0}, {"a": [1, 2.0]}, {}):
            with self.subTest(actual=actual):
                self.assertTrue(R.compare_adopted({"a": [1, 2]}, actual))

    def test_case_cannot_self_approve_rehashed_fixed_change(self):
        scratch = ROOT / "logs/checks/action_experiment_test_tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as tmp:
            root = Path(tmp)
            plan = R.compile_plan(self.recipe)
            R.write_json(root / "plan.json", plan)
            file = root / "cases" / "baseline.json"
            R.write_json(file, self.case)
            self.assertEqual(R.load_runtime_case(file), self.case)
            changed = copy.deepcopy(self.case)
            changed["fixed"]["controller_enabled"] = True
            changed.pop("case_sha256")
            changed["case_sha256"] = R.canonical_hash(changed)
            R.write_json(file, changed)
            with self.assertRaises(ValueError):
                R.load_runtime_case(file)

    def test_valid_bound_fixture_full_measurement_and_tracking_alignment(self):
        result = self.result()
        self.assertTrue(result["data_valid"], result)
        self.assertEqual(result["tracking"]["rms_rad"], [0.0] * 9)
        self.assertEqual(result["tracking"]["status"], "report_only")
        self.assertEqual(result["tracking"]["samples"], 20)
        self.assertEqual(result["impulse"]["intervals"], 20)
        for actual, expected in zip(result["impulse"]["six_components"], [2, 4, 6, 8, 10, 12]):
            self.assertAlmostEqual(actual, expected)

    def test_same_data_offline_recompute_is_identical(self):
        self.assertEqual(self.result(), self.result())

    def test_bad_actual_inputs_cannot_pass(self):
        changes = [("frequency_after_set_hz", 2.1), ("frequency_consumed_by_evaluate_hz", 2.1),
                   ("selection", "nominal"), ("target_api_status", "raised_unknown_effect"),
                   ("joint_names", ["repeat"] * 9), ("query_errors", {"joint": "failed"}),
                   ("actual_readback", {"joint_position_rad": [[0] * 8]}),
                   ("ap_out", {"freq": 1.9}), ("manual_channel", {"throttle": 0.0}),
                   ("manual_channel", {"throttle": 0.0, "elevator": 1.0, "aileron": 0.0, "rudder": 0.0})]
        for key, value in changes:
            with self.subTest(key=key):
                events = copy.deepcopy(self.events)
                events[22]["payload"][key] = value
                result = A.analyze_events(events, self.case, self.recipe["qualification"], self.conditions)
                self.assertFalse(result["data_valid"])
                self.assertIsNone(result["impulse"]["six_components"])

    def test_truncated_or_duplicate_records_fail(self):
        for events in (self.events[:-2], self.events[:20] + self.events[22:], self.events[:20] + self.events[18:]):
            with self.subTest(length=len(events)):
                self.assertFalse(A.analyze_events(events, self.case, self.recipe["qualification"], self.conditions)["data_valid"])

    def test_changed_fixed_conditions_fail(self):
        for key, value in (("complete", False), ("max_body_reset_error", 0.01), ("world_physics_dt_s", 0.2),
                           ("effective_parameters", {}), ("fixed_adopted", {"aero_model": "sane_dickinson_3d"})):
            with self.subTest(key=key):
                cond = copy.deepcopy(self.conditions)
                cond[key] = value
                self.assertFalse(A.analyze_events(self.events, self.case, self.recipe["qualification"], cond)["data_valid"])

    def test_model_flow_not_equal_to_config_is_rejected(self):
        self.events[23]["payload"]["model_input"]["air_velocity_world_m_s"][0] = 0.01
        self.assertFalse(self.result()["data_valid"])

    def test_failed_or_saturated_force_is_rejected(self):
        for key, value in (("force_api_status", "raised_unknown_effect"), ("tail_status", "failed"),
                           ("submitted_F_float32_N", None), ("clipped", True), ("status", "api_failed")):
            with self.subTest(key=key):
                events = copy.deepcopy(self.events)
                events[23]["payload"][key] = value
                self.assertFalse(A.analyze_events(events, self.case, self.recipe["qualification"], self.conditions)["data_valid"])

    def test_deferred_force_is_not_double_counted(self):
        event = copy.deepcopy(self.events[23])
        event["payload"].update(status="deferred", force_api_status="not_attempted", torque_api_status="not_attempted",
                                submitted_F_float32_N=None, submitted_T_float32_Nm=None)
        self.events.insert(24, event)
        for index, e in enumerate(x for x in self.events if x["kind"] == "aero_apply"):
            e["clock"]["callback_index"] = index + 1
        self.assertEqual(self.result()["impulse"]["six_components"], fixture_result()["impulse"]["six_components"])

    def test_native_clock_missing_doubled_ambiguous_or_wrong_owner_fail(self):
        for mode in ("missing", "double", "ambiguous", "wrong_owner", "unbound_force"):
            with self.subTest(mode=mode):
                events = copy.deepcopy(self.events)
                if mode == "missing":
                    events[22].pop("native_clock")
                elif mode == "double":
                    for e in events:
                        e["native_clock"]["physics_core"]["simulations"][0]["step_count"] *= 2
                elif mode == "ambiguous":
                    core = events[22]["native_clock"]["physics_core"]
                    core["simulations"].append(dict(core["simulations"][0], id=2))
                    core["simulation_count"] = 2
                elif mode == "wrong_owner":
                    events[22]["native_clock"]["physics_core"]["simulations"][0]["name"] = "Other"
                else:
                    events[23]["native_clock"]["physics_core"]["simulations"][0]["step_count"] += 1
                result = A.analyze_events(events, self.case, self.recipe["qualification"], self.conditions)
                self.assertFalse(result["data_valid"])

    def test_numeric_tracking_limit_is_not_overridden(self):
        self.recipe["qualification"]["tracking_error_limit_rad"] = 0.1
        self.events[22]["payload"]["actual_readback"]["joint_position_rad"][0][0] += 0.2
        self.assertEqual(self.result()["tracking"]["status"], "fail")

    def test_producer_reader_roundtrip_and_tamper_rejection(self):
        from modules import fw_iid_ledger as F, i210_evidence as E
        scratch = ROOT / "logs/checks/action_experiment_test_tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        env = {"FLAPPER_I210_EVIDENCE": "1", "I186_RUN_ID": "TEST_A",
               "I186_EXPERIMENT_ID": "interface_test", "I186_ARCHIVE_DIR": str(scratch)}
        with tempfile.TemporaryDirectory(dir=scratch) as tmp, patch.dict(os.environ, env), patch.object(E, "_native_clock_snapshot", return_value={}):
            binary = Path(tmp) / "sample.bin"
            recorder = F.LedgerRecorder(binary, ["joint%d" % i for i in range(9)], 0.1,
                                        meta={"tag": "TEST_A", "desc": "interface fixture only"})
            recorder.record(1, {name: [0.0] * width for name, width in F.CHAN_LAYOUT})
            E.observe(recorder, "drive", {"main_step": 1}, {"probe": 1.0})
            recorder.close()
            path = Path(str(binary) + ".i210.jsonl")
            header, events = A.read_events(path, binary, "TEST_A")
            self.assertEqual(len(events), 1)
            original = path.read_bytes()
            for raw in (original[:-1], original.replace(b'"probe":1.0', b'"probe":2.0'), original + b'\n'):
                path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    A.read_events(path, binary, "TEST_A")
            path.write_bytes(original)
            with self.assertRaises(ValueError):
                A.read_events(path, binary, "WRONG")
            data = binary.read_bytes()
            binary.write_bytes(data + b'x')
            with self.assertRaises(ValueError):
                A.read_events(path, binary, "TEST_A")

    def test_csv_own_qualification_rejects_common_truncation(self):
        import csv
        scratch = ROOT / "logs/checks/action_experiment_test_tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        columns = ["t", "phase", "joints_json"]
        with tempfile.TemporaryDirectory(dir=scratch) as tmp:
            file = Path(tmp) / "sample.csv"
            R.write_json(str(file) + ".meta.json", {"tags": "TEST_A", "description": "interface fixture only"})
            def save(rows):
                with file.open('w', newline='', encoding='utf-8') as fh:
                    csv.writer(fh).writerows([columns] + rows)
            rows = [[round(step * .1, 4), "settle" if step <= 10 else "measure", "{}"] for step in range(4, 31, 4)]
            save(rows)
            self.assertEqual(A.qualify_csv(file, self.case, columns, "TEST_A")["status"], "valid")
            for changed in (rows[:-1], rows[:1] + rows, rows[:2] + [[]] + rows[3:]):
                save(changed)
                with self.assertRaises(ValueError):
                    A.qualify_csv(file, self.case, columns, "TEST_A")

    def test_default_entry_does_not_touch_configuration(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(R, 'apply_runtime_case', side_effect=AssertionError('default mutated')):
            self.assertIsNone(R.prepare_runtime_input(None, None, {}))

    def test_planned_input_uses_sim_clock_and_records_actual_condition_changes(self):
        import numpy as np
        scratch = ROOT / "logs/checks/action_experiment_test_tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as tmp, patch('atexit.register'), patch('time.time', side_effect=AssertionError('wall clock used')):
            cfg = ModuleType('test_cfg')
            cfg.PHYSICS_DT = .1
            cfg.LOG_DIR = tmp
            cfg.MANUAL_FREQ_MIN, cfg.MANUAL_FREQ_MAX = 1.2, 3.5
            cfg.MANUAL_ELEV_GAIN = cfg.MANUAL_AIL_GAIN = cfg.MANUAL_RUDD_GAIN = 1.0
            R.apply_runtime_case(cfg, self.case)
            view = SimpleNamespace(get_world_poses=lambda: (np.array([self.case['fixed']['initial_position_world_m']]), np.array([[1., 0., 0., 0.]])),
                                   get_velocities=lambda: np.zeros((1, 6)))
            host = {'_SIM_T': .1, '_SIDE_PULSE_ON': False}
            control = R.PlannedInput(self.case, lambda: host['_SIM_T'], cfg, view, host, tmp, 'TEST_A',
                                     world=SimpleNamespace(get_physics_dt=lambda: .1))
            for step in range(1, 31):
                host['_SIM_T'] = step * .1
                channels = control.read()
                actual_frequency = cfg.MANUAL_FREQ_MIN + (cfg.MANUAL_FREQ_MAX - cfg.MANUAL_FREQ_MIN) * (channels['throttle'] + 1) / 2
                self.assertAlmostEqual(actual_frequency, 1.9)
            saved = R.read_json(Path(tmp) / 'TEST_A.conditions.json')
            self.assertTrue(saved['complete'])
            self.assertEqual(saved['samples'], 30)
            self.assertEqual(saved['errors'], [])
            self.assertEqual(saved['max_body_reset_error'], 0.0)
            self.assertEqual(R.read_json(Path(tmp) / 'experiment_phase.json')['phase'], 'measure')
            cfg.WIND_ENABLED = True
            host['_SIM_T'] = 3.1
            control.read()
            self.assertFalse(control.summary['complete'])
            self.assertTrue(control.summary['errors'])

    def test_missing_sim_clock_never_uses_zero_or_wall_clock(self):
        with patch('atexit.register'):
            control = R.PlannedInput(self.case, lambda: None, None, None, {}, '.', 'TEST')
            with self.assertRaises(ValueError):
                control.read()


def fixture_result():
    r, c, e, s = fixture()
    return A.analyze_events(e, c, r["qualification"], s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
