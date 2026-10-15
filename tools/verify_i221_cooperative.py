"""Production API integration checks for I-221. No simulator/control changes."""
import copy
import json
import math
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.motion_action.cooperative import (
    build_cooperative_trajectory, constant_coefficients, ScalarJet,
    RhythmClockAdapter, ChannelCoefficients)
from modules.motion_action.parameters import assemble_parameters
from modules.motion_action.rhythm import (RhythmService, RhythmEvent, ParamProgramStep,
    EVENT_STOP_FLAP, EVENT_RESUME_FLAP, EVENT_FREEZE, EVENT_UNFREEZE,
    EVENT_RESET, EVENT_PHASE_LOCK)
from modules.motion_action.runtime import PreviewEngine, HistoryBuffer
from modules.motion_action.types import load_recipe
from modules.motion_action.primitives import CouplingInput, COUPLING_UNIT_PERIODIC


class CooperativeChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipe = load_recipe(str(ROOT / "config/motion_recipes/cooperative_v1.json"))
        cls.program = ROOT / "config/motion_programs/cooperative_v1.json"

    def setup(self, backend="periodic_dmp", lag=0.0):
        delta = next(r.offset_value for r in self.recipe.parent_child if r.child == "chi_R")
        service = RhythmService(6.0, 0.002, .5, delta_0_beats=delta)
        params, adoption = assemble_parameters({}, dict(freq_hz=6., mean_rad=.21,
            amplitude_rad=.6, fold_center_rad=.1, fold_scale=.2,
            tail_trim_rad=.13, da_joint_amp_on=True, pitch_lag_s=lag),
            dict(dA=.04, rudder=0., aileron=.02),
            recipe_channels={r.joint: r for r in self.recipe.channels})
        coefficients = constant_coefficients(params)
        trajectory = build_cooperative_trajectory(self.recipe, self.program,
            service.read(), coefficients, split=.5, alpha_set_rad=.7,
            pitch_bias_rad=.03, flap_backend=backend)
        return service, trajectory, coefficients

    def test_real_child_clocks_and_nine_channel_frame(self):
        service, trajectory, coefficients = self.setup()
        for _ in range(160):
            sample = service.advance(None, .4)
            frame = trajectory.accept(sample)
        self.assertEqual(set(frame.channels), {r.joint for r in self.recipe.channels})
        for side, sign in (("L", 1), ("R", -1)):
            state = trajectory.providers["flap_" + side].primitive.state()
            self.assertAlmostEqual(state["chi"], getattr(sample, "chi_" + side))
            self.assertEqual(state["advance_count"], 160)
            shape = trajectory.providers["flap_" + side].sample()
            envelope = trajectory.transition.sample()
            self.assertAlmostEqual(frame.channels["flap_" + side].value,
                .21 + (.6 + sign * .04) * envelope.z * shape.z)
        self.assertAlmostEqual(frame.channels["tail_L"].value, .15)
        self.assertAlmostEqual(frame.channels["tail_R"].value, .11)
        self.assertEqual(frame.channels["tail_yaw"].value, 0.)
        self.assertEqual(trajectory.outputs["twist_L"].acceleration_status, "declared_approximation")
        self.assertAlmostEqual(frame.channels["fold_L"].value,
            .1 + .2 * trajectory.transition.sample().z * math.sin(2*math.pi*sample.chi_fold + 2.2))
        self.assertGreater(sample.beat_index, 0)
        with self.assertRaises(TypeError):
            frame.channels["flap_L"] = frame.channels["flap_R"]

    def test_idempotence_conflict_and_atomic_provider_failure(self):
        service, trajectory, coeff = self.setup()
        sample = service.advance()
        frame = trajectory.accept(sample)
        count = trajectory.providers["flap_L"].state()["advance_count"]
        self.assertIs(trajectory.accept(sample), frame)
        self.assertEqual(trajectory.providers["flap_L"].state()["advance_count"], count)
        with self.assertRaises(ValueError):
            trajectory.accept(sample, coefficients=coeff)
        original = trajectory.providers["flap_L"].primitive.state()
        trajectory.providers["twist_R"].shape_kind = "broken"
        next_sample = service.advance()
        with self.assertRaises(ValueError):
            trajectory.accept(next_sample)
        self.assertEqual(trajectory.providers["flap_L"].primitive.state(), original)
        self.assertIs(trajectory.sample(), frame)

    def test_stop_resume_freeze_reset_and_phase_lock(self):
        service, trajectory, _ = self.setup()
        events = [(2, EVENT_STOP_FLAP), (4, EVENT_RESUME_FLAP),
                  (6, EVENT_FREEZE), (8, EVENT_UNFREEZE), (10, EVENT_RESET)]
        for step, kind in events:
            service.schedule_event(RhythmEvent(kind, step))
        previous = None
        for step in range(1, 12):
            sample = service.advance(None, None)
            frame = trajectory.accept(sample)
            if step == 3:
                self.assertAlmostEqual(frame.channels["flap_L"].value, .21)
                self.assertGreater(sample.chi_c, previous.chi_c)
            if step == 7:
                self.assertEqual(sample.chi_c, previous.chi_c)
                self.assertEqual(frame.channels["flap_L"].velocity, 0.)
            if step == 10:
                self.assertEqual(sample.events, (EVENT_RESET,))
                fresh_service, fresh_trajectory, _ = self.setup()
                fresh = fresh_trajectory.accept(fresh_service.advance())
                self.assertAlmostEqual(frame.channels["flap_L"].value, fresh.channels["flap_L"].value)
            previous = sample
        service.schedule_event(RhythmEvent(EVENT_PHASE_LOCK, 12, {"d_epsilon_advance": -.3}))
        old_count = trajectory.providers["flap_L"].primitive.state()["advance_count"]
        frame = trajectory.accept(service.advance(None, None))
        self.assertIsNone(frame.channels["flap_L"].velocity)
        self.assertEqual(trajectory.providers["flap_L"].primitive.state()["advance_count"], old_count + 1)

    def test_reset_and_phase_lock_same_step_follow_actual_event_order(self):
        for order in ((EVENT_RESET, EVENT_PHASE_LOCK), (EVENT_PHASE_LOCK, EVENT_RESET)):
            service, trajectory, _ = self.setup()
            trajectory.accept(service.advance())
            for kind in order:
                service.schedule_event(RhythmEvent(kind, 2, {"d_epsilon_advance": -.3} if kind == EVENT_PHASE_LOCK else {}))
            sample = service.advance(None, None)
            frame = trajectory.accept(sample)
            self.assertEqual(sample.events, order)
            self.assertAlmostEqual(trajectory.providers["flap_L"].primitive.state()["chi"], sample.chi_L)
            self.assertIsNotNone(frame.channels["flap_L"].value)
            self.assertIsNone(frame.channels["flap_L"].velocity)

    def test_real_preview_protocol_program_events_and_deep_isolation(self):
        service, trajectory, _ = self.setup()
        trajectory.accept(service.advance())
        service.schedule_param_step(ParamProgramStep(3, "freq_hz", 9.))
        service.schedule_event(RhythmEvent(EVENT_RESET, 4))
        before = trajectory.providers["flap_L"].primitive.state()
        state = service.state()
        actions = trajectory.preview_actions(service, 6, adopted_frequency_hz=6.)
        window, meta = PreviewEngine(service, HistoryBuffer(), 6., providers=(trajectory,)).window(6)
        self.assertTrue(all(f.preview for f in actions))
        self.assertTrue(all(s.providers_stepped == ("CooperativeTrajectory",) for s in window))
        for action, sample in zip(actions, window):
            self.assertAlmostEqual(action.frame.rhythm.node_values["chi_c"], sample.chi_c)
        self.assertEqual(window[1].frequency_hz, 9.)
        self.assertEqual(before, trajectory.providers["flap_L"].primitive.state())
        self.assertEqual(state, service.state())
        clone = trajectory.clone()
        weights = list(clone.providers["flap_L"].primitive.weights)
        weights[0] += 2.
        clone.providers["flap_L"].primitive.set_weights(weights)
        self.assertNotEqual(clone.providers["flap_L"].primitive.weights,
                            trajectory.providers["flap_L"].primitive.weights)
        trajectory.providers["twist_R"].shape_kind = "broken"
        with self.assertRaises(ValueError):
            PreviewEngine(service, HistoryBuffer(), 6., providers=(trajectory,)).window(2)
        self.assertEqual(before, trajectory.providers["flap_L"].primitive.state())
        self.assertEqual(state, service.state())

    def test_parameter_chain_derivatives_and_unavailable_is_not_zero(self):
        service, trajectory, coeff = self.setup()
        sample = service.advance()
        frame = trajectory.accept(sample)
        changed = copy.deepcopy(coeff)
        changed["flap_L"] = replace(coeff["flap_L"], center=ScalarJet(.21, None, None))
        frame = trajectory.accept(service.advance(), coefficients=changed)
        self.assertIsNotNone(frame.channels["flap_L"].value)
        self.assertIsNone(frame.channels["flap_L"].velocity)
        self.assertIsNone(frame.channels["flap_L"].acceleration)
        smooth = copy.deepcopy(coeff)
        smooth["flap_L"] = replace(coeff["flap_L"], center=ScalarJet(.3, .2, .4),
            scale=ScalarJet(.7, .1, .3), control=ScalarJet(.01, .02, .03), continuity="smooth")
        frame = trajectory.accept(service.advance(), coefficients=smooth)
        shape = trajectory.providers["flap_L"].sample()
        env = trajectory.transition.sample()
        a = .7*env.z
        ad = .1*env.z + .7*env.z_dot
        add = .3*env.z + .2*env.z_dot + .7*env.z_ddot
        self.assertAlmostEqual(frame.channels["flap_L"].velocity, .2+ad*shape.z+a*shape.z_dot+.02)
        self.assertAlmostEqual(frame.channels["flap_L"].acceleration,
            .4+add*shape.z+2*ad*shape.z_dot+a*shape.z_ddot+.03)

    def test_lag_missing_rejected_and_explicit_resolver_clock_used(self):
        service, trajectory, _ = self.setup(lag=.01)
        sample = service.advance()
        with self.assertRaisesRegex(ValueError, "history/prediction"):
            trajectory.accept(sample)
        adapter = RhythmClockAdapter(service.read(), 3.)
        resolved = adapter.clock(sample, "chi_c")
        overrides = {j: replace(resolved, chi=-.1, chi_dot=2., chi_ddot=0.) for j in ("twist_L", "twist_R")}
        frame = trajectory.accept(sample, clock_overrides=overrides)
        self.assertIsNotNone(frame.channels["twist_L"].velocity)
        expected_shape = .7 * math.cos(-.2*math.pi) + .03
        self.assertAlmostEqual(frame.channels["twist_L"].value,
            expected_shape * trajectory.transition.sample().z)

    def test_coupling_single_owner_units_and_no_analytic_silent_drop(self):
        service, trajectory, _ = self.setup()
        sample = service.advance()
        coupling = CouplingInput(.2, COUPLING_UNIT_PERIODIC, "controller", "flap_L.shape")
        with self.assertRaisesRegex(ValueError, "unique owner"):
            trajectory.accept(sample, couplings={"flap_L": coupling})
        trajectory.coupling_owners["flap_L"] = "controller"
        with self.assertRaises(ValueError):
            trajectory.accept(sample, couplings={"flap_L": replace(coupling, unit="rad")})
        trajectory.accept(sample, couplings={"flap_L": coupling})
        trajectory.coupling_owners["twist_L"] = "controller"
        with self.assertRaises(ValueError):
            trajectory.accept(service.advance(), couplings={"twist_L": coupling})

    def test_program_validation_and_analytic_is_still_selectable(self):
        service, trajectory, coeff = self.setup("analytic")
        self.assertIsNone(getattr(trajectory.providers["flap_L"], "primitive", None))
        frame = trajectory.accept(service.advance())
        self.assertIn("analytic", frame.channels["flap_L"].source)
        doc = json.loads(self.program.read_text())
        doc["unknown"] = 0
        with tempfile.TemporaryDirectory(dir=ROOT / "logs/kula_direct_i221_20261015") as directory:
            path = Path(directory) / "program.json"
            path.write_text(json.dumps(doc))
            with self.assertRaises(ValueError):
                build_cooperative_trajectory(self.recipe, path, service.read(), coeff,
                    split=.5, alpha_set_rad=.7, pitch_bias_rad=.03, reset_initial=trajectory.adapter.reset_initial)
        bad = replace(service.read(), delta_0=999.)
        with self.assertRaises(ValueError):
            build_cooperative_trajectory(self.recipe, self.program, bad, coeff,
                split=.5, alpha_set_rad=.7, pitch_bias_rad=.03)

    def test_frequency_step_boundary_discrete_progress_and_mode_release(self):
        service, trajectory, coeff = self.setup()
        trajectory.accept(service.advance())
        sample = service.advance(8.)
        frame = trajectory.accept(sample)
        self.assertIsNone(frame.channels["flap_L"].velocity)
        self.assertIsNone(frame.channels["flap_L"].acceleration)
        self.assertAlmostEqual(trajectory.transition.sample().canonical_value, sample.chi_c/3.)
        sample = service.advance(None, None)
        trajectory.accept(sample)
        self.assertIsNotNone(trajectory.sample().channels["flap_L"].velocity)
        replacement = build_cooperative_trajectory(self.recipe, self.program, sample,
            coeff, split=.5, alpha_set_rad=.7, pitch_bias_rad=.03, flap_backend="analytic",
            reset_initial=trajectory.adapter.reset_initial)
        trajectory.switch(replacement)
        trajectory.accept(service.advance(None, None))
        self.assertIn("analytic", trajectory.sample().channels["flap_L"].source)

    def test_full_time_varying_output_against_finite_difference(self):
        service, trajectory, coeff = self.setup()
        values = []
        for _ in range(42):
            sample = service.advance(None, None)
            t = sample.t_s
            current = dict(coeff)
            current["flap_L"] = replace(coeff["flap_L"],
                center=ScalarJet(.21+.2*t+.2*t*t, .2+.4*t, .4),
                scale=ScalarJet(.64+.1*t+.15*t*t, .1+.3*t, .3),
                control=ScalarJet(.01+.02*t+.015*t*t, .02+.03*t, .03), continuity="smooth")
            values.append(trajectory.accept(sample, coefficients=current).channels["flap_L"])
        for k in (20, 30):
            speed = (values[k+1].value-values[k-1].value)/(2*service.dt)
            acceleration = (values[k+1].value-2*values[k].value+values[k-1].value)/service.dt**2
            self.assertAlmostEqual(values[k].velocity, speed, delta=.015)
            self.assertAlmostEqual(values[k].acceleration, acceleration, delta=.9)

    def test_presence_saturation_and_mode_reset_anchor(self):
        service, trajectory, coeff = self.setup()
        coeff = dict(coeff)
        coeff["flap_L"] = replace(coeff["flap_L"], limits=(-.1, .1))
        coeff["tail_yaw"] = replace(coeff["tail_yaw"], presence="zero")
        coeff["tail_R"] = replace(coeff["tail_R"], presence="unsupported")
        for _ in range(4):
            frame = trajectory.accept(service.advance(None, None), coefficients=coeff)
        self.assertEqual(frame.channels["flap_L"].value, .1)
        self.assertEqual(frame.channels["flap_L"].velocity, 0.)
        self.assertGreater(trajectory.outputs["flap_L"].unsaturated, .1)
        self.assertEqual(frame.channels["tail_yaw"].presence, "zero")
        self.assertEqual(frame.channels["tail_R"].presence, "unsupported")
        self.assertIsNone(frame.channels["tail_R"].value)
        replacement = build_cooperative_trajectory(self.recipe, self.program, service.read(), coeff,
            split=.5, alpha_set_rad=.7, pitch_bias_rad=.03, reset_initial=trajectory.adapter.reset_initial)
        trajectory.switch(replacement)
        service.schedule_event(RhythmEvent(EVENT_RESET, 5))
        frame = trajectory.accept(service.advance(None, None))
        self.assertAlmostEqual(trajectory.providers["flap_L"].primitive.state()["chi"], service.read().chi_L)
        self.assertIsNotNone(frame.channels["flap_L"].value)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CooperativeChecks)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs/kula_direct_i221_20261015/integration.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"tests": result.testsRun, "passed": result.wasSuccessful(),
        "failures": [(str(t), text) for t, text in result.failures],
        "errors": [(str(t), text) for t, text in result.errors]}, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.exit(0 if result.wasSuccessful() else 1)
