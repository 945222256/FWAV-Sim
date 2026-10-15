"""I-221: committed rhythm -> stateful shapes -> immutable nine-channel action.

The caller owns RhythmService.advance and feedback adoption. This module never
integrates a clock. ScalarJet makes time-varying parameter derivatives explicit;
unknown derivatives remain unavailable. Joint mirroring and force application
belong to the consumers (I-222/I-223), not to this reference-coordinate layer.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Optional

from .primitives import (CanonicalClockSample, CouplingInput, ReferenceSampleSet,
                         fit_reference_periodic)
from .rhythm import (RhythmSample, EVENT_RESET, EVENT_PHASE_LOCK,
                     EVENT_FREEZE, EVENT_UNFREEZE, EVENT_STOP_FLAP,
                     EVENT_RESUME_FLAP)
from .trajectories import (AnalyticProvider, PrimitiveProvider, HoldProvider,
                           ShapeSample, TrajectoryError,
                           assemble_channel_trajectory, build_channel_action,
                           make_spring_transition)
from .types import (MotionRecipe, RhythmSnapshot, StepAction,
                    DERIVATIVE_STATUS_AVAILABLE as AVAILABLE,
                    DERIVATIVE_STATUS_MISSING as MISSING,
                    DERIVATIVE_STATUS_EVENT_BOUNDARY as BOUNDARY)


def _finite(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TrajectoryError("expected finite number", where=where)
    return float(value)


@dataclass(frozen=True)
class ScalarJet:
    """Physical-time value, velocity, acceleration. None means unavailable."""
    value: float
    velocity: Optional[float]
    acceleration: Optional[float]

    def __post_init__(self):
        _finite(self.value, "ScalarJet.value")
        for key in ("velocity", "acceleration"):
            value = getattr(self, key)
            if value is not None:
                _finite(value, "ScalarJet." + key)

    @classmethod
    def constant(cls, value):
        """Explicit constant program declaration; not an inferred derivative."""
        return cls(value, 0.0, 0.0)


@dataclass(frozen=True)
class ChannelCoefficients:
    center: ScalarJet
    scale: ScalarJet
    control: ScalarJet
    presence: str = "value"
    limits: Optional[tuple] = None
    continuity: str = "step"
    lag_s: float = 0.0
    gate_request: Optional[float] = None

    def __post_init__(self):
        if self.continuity not in ("step", "smooth") or self.presence not in ("value", "zero", "missing", "unsupported"):
            raise TrajectoryError("invalid coefficient policy", where="ChannelCoefficients")
        if any(not isinstance(j, ScalarJet) for j in (self.center, self.scale, self.control)):
            raise TrajectoryError("coefficient requires explicit ScalarJet", where="ChannelCoefficients")
        _finite(self.lag_s, "lag_s")
        if self.gate_request is not None:
            _finite(self.gate_request, "gate_request")
        if self.limits is not None:
            if len(self.limits) != 2 or _finite(self.limits[0], "lower") >= _finite(self.limits[1], "upper"):
                raise TrajectoryError("invalid limits", where="ChannelCoefficients")


@dataclass(frozen=True)
class PreviewAction:
    preview: bool
    frame: StepAction
    policy: str = "cloned service executes scheduled programs/events; unknown feedback and coefficients held"


def constant_coefficients(parameters):
    """Import I-219 adopted parameters as an explicitly held parameter program.

    Amplitude differential and angular offset retain their original separate
    roles. Tail trim is already adopted by I-219, so it is not added again.
    Gate requests and twist lag require their own consumer/resolver; they are
    never silently converted into shape amplitude/phase here.
    """
    result = {}
    for joint, p in parameters.items():
        result[joint] = ChannelCoefficients(
            ScalarJet.constant(p.center),
            ScalarJet.constant(p.scale + p.amplitude_differential),
            ScalarJet.constant(p.control_correction + p.legacy_angular_offset),
            p.presence, lag_s=p.lag_s, gate_request=p.gate_value)
    return result


class RhythmClockAdapter:
    """Read-only conversion of actual service samples to provider clocks.

    chi is unwrapped beats; chi_dot is Hz, not shape_phase_rate (rad/beat).
    Discrete progress uses the common beat budget divided by a declared duration
    in beats; freeze holds both periodic and one-shot states. Stop-flap does not
    stop the clock. A frequency step or phase event has unavailable derivatives
    at the boundary; constant adopted rates have exact zero acceleration inside
    the interval. Event resets are explicit even if phases happen to coincide.
    """
    NODES = ("chi_c", "chi_L", "chi_R", "chi_fold", "hold_tail")

    def __init__(self, initial: RhythmSample, duration_beats: float, reset_initial=None):
        if not isinstance(initial, RhythmSample):
            raise TrajectoryError("requires actual RhythmSample", where="clock adapter")
        self.duration_beats = _finite(duration_beats, "duration_beats")
        if self.duration_beats <= 0:
            raise TrajectoryError("duration_beats must be positive", where="clock adapter")
        self.initial = initial
        self.reset_initial = reset_initial if reset_initial is not None else (initial if initial.step == 0 else None)
        self.last = initial
        self.origin_chi = initial.chi_c

    @staticmethod
    def rate(sample, node):
        if sample.frozen:
            return 0.0
        return sample.frequency_hz + (sample.dfreq_hz if node == "chi_L" else
                                     -sample.dfreq_hz if node == "chi_R" else 0.0)

    def boundary(self, sample, node):
        return (EVENT_RESET in sample.events or EVENT_PHASE_LOCK in sample.events
                or sample.frozen != self.last.frozen
                or self.rate(sample, node) != self.rate(self.last, node))

    def clock(self, sample, node):
        if not isinstance(sample, RhythmSample) or node not in self.NODES:
            raise TrajectoryError("invalid sample or rhythm node", where=str(node))
        chi = sample.chi_c if node == "hold_tail" else getattr(sample, node)
        rate = self.rate(sample, node)
        if rate < 0:
            raise TrajectoryError("reverse child clock requires a reverse-capable primitive", where=node)
        if EVENT_RESET in sample.events and self.reset_initial is None:
            raise TrajectoryError("reset requires the service's original reset anchor", where="clock adapter")
        origin = self.reset_initial.chi_c if EVENT_RESET in sample.events else self.origin_chi
        p = (sample.chi_c - origin) / self.duration_beats
        common_rate = self.rate(sample, "chi_c") / self.duration_beats
        return CanonicalClockSample(
            step=sample.step, t_s=sample.t_s, chi=chi, chi_dot=rate,
            chi_ddot=None if self.boundary(sample, node) else 0.0,
            source="rhythm_service:" + node, frozen=sample.frozen,
            canonical_progress=p, progress_dot=common_rate,
            progress_ddot=None if self.boundary(sample, "chi_c") else 0.0)

    def commit(self, sample):
        if EVENT_RESET in sample.events:
            self.origin_chi = self.reset_initial.chi_c
        self.last = sample


class CooperativeTrajectory:
    """One atomic provider commit for each already-committed physical step.

    clone()/on_preview_step() implement I-220's actual PreviewEngine protocol.
    The startup DMP is an amplitude envelope for all six wing joints, replacing
    any additional legacy startup ramp in this program. Tail holds use the
    existing controller's adopted absolute control target. Mode changes/releases
    use switch(): caller supplies new adopted coefficients and explicit initial
    providers; no hidden zero-state reset or undeclared blend is performed.
    """
    def __init__(self, recipe: MotionRecipe, providers: Mapping,
                 coefficients: Mapping, initial: RhythmSample,
                 transition, duration_beats: float, nodes: Optional[Mapping] = None,
                 coupling_owners: Optional[Mapping] = None, reset_initial=None):
        joints = {row.joint for row in recipe.channels}
        if set(providers) != joints or set(coefficients) != joints:
            raise TrajectoryError("providers and coefficients must cover exactly nine recipe joints", where=recipe.name)
        self.recipe = recipe
        self.providers = {k: v.clone_for_preview() for k, v in providers.items()}
        self.coefficients = copy.deepcopy(dict(coefficients))
        bindings = {b.binding_id: b for b in recipe.provider_bindings}
        self.nodes = {r.joint: bindings[r.provider_binding].reads_node for r in recipe.channels}
        if nodes is not None:
            if set(nodes) != joints or any(n not in RhythmClockAdapter.NODES for n in nodes.values()):
                raise TrajectoryError("invalid explicit provider node map", where=recipe.name)
            self.nodes = dict(nodes)
        self.adapter = RhythmClockAdapter(initial, duration_beats, reset_initial)
        self.coupling_owners = dict(coupling_owners or {})
        if set(self.coupling_owners) - joints or any(not isinstance(v, str) or not v.strip() for v in self.coupling_owners.values()):
            raise TrajectoryError("invalid coupling owner declaration", where=recipe.name)
        self.transition = transition.clone_for_preview()
        self._seed = {k: v.clone_for_preview() for k, v in self.providers.items()}
        self._transition_seed = self.transition.clone_for_preview()
        self._frame = None
        self._last_input = None
        self._step = initial.step
        self._last_t = initial.t_s
        self.normalization = {}
        self.outputs = {}

    def clone(self):
        """Deep isolated preview copy, including weights, caches and event state."""
        twin = copy.copy(self)
        twin.__dict__ = copy.deepcopy({k: v for k, v in self.__dict__.items() if k != "_frame"})
        if self._frame is None:
            twin._frame = None
        else:
            frame = self._frame
            rhythm = frame.rhythm
            twin._frame = StepAction(frame.step, frame.t_s,
                RhythmSnapshot(rhythm.step, rhythm.t_s, dict(rhythm.node_values), dict(rhythm.derived_values)),
                {joint: replace(channel, derivative_statuses=dict(channel.derivative_statuses))
                 for joint, channel in frame.channels.items()})
        return twin

    def sample(self):
        if self._frame is None:
            raise TrajectoryError("no action committed", where=self.recipe.name)
        return self._frame

    def on_preview_step(self, sample):
        return self.accept(sample)

    def preview_actions(self, service, steps, *, adopted_frequency_hz=None):
        """Return marked action frames with I-220's cloned scheduled-program policy."""
        if isinstance(steps, bool) or not isinstance(steps, int) or steps < 0:
            raise TrajectoryError("preview steps must be a nonnegative integer", where="preview_actions")
        if service.read() != self.adapter.last:
            raise TrajectoryError("service and trajectory must share the same committed sample", where="preview_actions")
        clock, trajectory = service.clone(), self.clone()
        if adopted_frequency_hz is not None:
            clock.adopt(adopted_frequency_hz)
        return tuple(PreviewAction(True, trajectory.accept(clock.advance(None, None))) for _ in range(steps))

    def accept(self, sample: RhythmSample, *, coefficients=None, couplings=None,
               clock_overrides=None):
        """Read a committed sample; duplicate identical input returns the same frame.

        clock_overrides is the explicit I-222 history/prediction resolver outlet
        for time-lagged references. It supplies complete canonical derivatives;
        a nonzero lag must not be approximated by current_frequency * lag.
        """
        if not isinstance(sample, RhythmSample):
            raise TrajectoryError("requires actual RhythmSample", where="accept")
        incoming = (sample, None if coefficients is None else dict(coefficients),
                    None if couplings is None else dict(couplings),
                    None if clock_overrides is None else dict(clock_overrides))
        if sample.step == self._step and self._last_input is not None:
            if incoming == self._last_input:
                return self._frame
            raise TrajectoryError("conflicting input for committed step", where="accept")
        if sample.step != self._step + 1 or sample.t_s <= self._last_t:
            raise TrajectoryError("must consume consecutive physical steps", where="accept")
        twin = self.clone()
        twin._commit(sample, coefficients, couplings or {}, clock_overrides or {})
        twin._last_input = copy.deepcopy(incoming)
        self.__dict__.update(twin.__dict__)
        return self._frame

    def _commit(self, sample, coefficients, couplings, overrides):
        if set(couplings) - set(self.providers) or set(overrides) - set(self.providers):
            raise TrajectoryError("unknown joint in coupling/clock override", where="accept")
        for joint, coupling in couplings.items():
            if (not isinstance(coupling, CouplingInput)
                    or not isinstance(coupling.owner, str) or not coupling.owner.strip()
                    or not isinstance(coupling.outlet, str) or not coupling.outlet.strip()
                    or self.coupling_owners.get(joint) != coupling.owner
                    or coupling.outlet != joint + ".shape"
                    or not isinstance(self.providers[joint], PrimitiveProvider)):
                raise TrajectoryError("coupling requires declared unique owner and outlet", where=joint)
        if coefficients is not None and set(coefficients) != set(self.providers):
            raise TrajectoryError("coefficient update must cover all channels", where="accept")
        previous_coefficients = self.coefficients
        if coefficients is not None:
            self.coefficients = copy.deepcopy(dict(coefficients))
        for joint, coefficient in self.coefficients.items():
            if not isinstance(coefficient, ChannelCoefficients):
                raise TrajectoryError("requires ChannelCoefficients", where=joint)
        reset = EVENT_RESET in sample.events
        if reset:
            self.providers = {k: p.clone_for_preview() for k, p in self._seed.items()}
            self.transition = self._transition_seed.clone_for_preview()
        envelope = self.transition.advance(self.adapter.clock(sample, "chi_c"))
        if self.adapter.boundary(sample, "chi_c"):
            envelope = replace(envelope, z_dot=None, z_ddot=None,
                               first_order_status=BOUNDARY, second_order_status=BOUNDARY)
        channels, outputs = {}, {}
        for joint, provider in self.providers.items():
            node = self.nodes[joint]
            coeff = self.coefficients[joint]
            if coeff.lag_s != 0.0 and joint not in overrides:
                raise TrajectoryError("nonzero time lag requires explicit history/prediction clock resolver", where=joint)
            clock = overrides.get(joint, self.adapter.clock(sample, node))
            if not isinstance(clock, CanonicalClockSample) or clock.step != sample.step or clock.t_s != sample.t_s:
                raise TrajectoryError("resolved clock must belong to the same physical step", where=joint)
            if EVENT_PHASE_LOCK in sample.events and isinstance(provider, PrimitiveProvider):
                interval = sample.t_s - self._last_t
                provider.primitive.rebase_phase(clock.chi - clock.chi_dot * interval)
            shape = provider.advance(clock, couplings.get(joint))
            boundary = self.adapter.boundary(sample, node)
            # Phase-lock changes the coordinate, not the primitive's shape state.
            # Reverse jumps must be handled via explicit switch/reset, not erased.
            if boundary:
                shape = replace(shape, z_dot=None, z_ddot=None,
                                first_order_status=BOUNDARY, second_order_status=BOUNDARY)
            else:
                shape = replace(shape,
                    first_order_status=MISSING if shape.z_dot is None and shape.first_order_status == AVAILABLE else shape.first_order_status,
                    second_order_status=MISSING if shape.z_ddot is None and shape.second_order_status == AVAILABLE else shape.second_order_status)
            center, scale, control = coeff.center, coeff.scale, coeff.control
            if not joint.startswith("tail"):
                # Product of adopted scale, DMP startup envelope and stop gate.
                a, ad, add = envelope.z, envelope.z_dot, envelope.z_ddot
                gate = sample.stroke_gate
                scale = ScalarJet(scale.value * a * gate,
                    None if scale.velocity is None or ad is None else
                    (scale.velocity * a + scale.value * ad) * gate,
                    None if scale.acceleration is None or scale.velocity is None or ad is None or add is None else
                    (scale.acceleration * a + 2 * scale.velocity * ad + scale.value * add) * gate)
            first = all(j.velocity is not None for j in (center, scale, control))
            second = first and all(j.acceleration is not None for j in (center, scale, control))
            output = assemble_channel_trajectory(shape, center.value, scale.value, control.value,
                d_center=center.velocity or 0.0, d_scale=scale.velocity or 0.0,
                d_control=control.velocity or 0.0, dd_center=center.acceleration or 0.0,
                dd_scale=scale.acceleration or 0.0, dd_control=control.acceleration or 0.0,
                limits=coeff.limits)
            # Scalar defaults above only allow computing the value. Unknown
            # derivatives are removed before publication, never exposed as zero.
            event_boundary = (coeff.continuity == "step" and coeff != previous_coefficients[joint]
                or (not joint.startswith("tail") and any(e in sample.events for e in
                    (EVENT_STOP_FLAP, EVENT_RESUME_FLAP, EVENT_FREEZE, EVENT_UNFREEZE))))
            if not first or event_boundary:
                status = BOUNDARY if event_boundary else MISSING
                output = replace(output, velocity=None, acceleration=None,
                                 velocity_status=status, acceleration_status=status,
                                 notes=output.notes + "; parameter/gate derivative unavailable")
            elif not second:
                output = replace(output, acceleration=None, acceleration_status=MISSING,
                                 notes=output.notes + "; parameter acceleration unavailable")
            if coeff.presence in ("missing", "unsupported"):
                output = replace(output, value=None, velocity=None, acceleration=None,
                                 presence=coeff.presence, velocity_status=MISSING, acceleration_status=MISSING)
            elif coeff.presence == "zero" and output.value == 0.0:
                output = replace(output, presence="zero")
            outputs[joint] = output
            channels[joint] = build_channel_action(joint, output, shape.source)
        snapshot = RhythmSnapshot(sample.step, sample.t_s,
            {"chi_c": sample.chi_c, "epsilon": sample.epsilon},
            {"chi_L": sample.chi_L, "chi_R": sample.chi_R, "chi_fold": sample.chi_fold})
        self._frame = StepAction(sample.step, sample.t_s, snapshot, channels)
        self.outputs = outputs
        self._step, self._last_t = sample.step, sample.t_s
        self.adapter.commit(sample)

    def switch(self, replacement):
        """Install a prepared mode/release program at the current step boundary."""
        if not isinstance(replacement, CooperativeTrajectory) or replacement._step != self._step:
            raise TrajectoryError("replacement must be initialized at current committed step", where="switch")
        last_frame = self._frame
        self.__dict__.update(replacement.clone().__dict__)
        if self._frame is None:
            self._frame = last_frame


def build_cooperative_trajectory(recipe, program_path, initial, coefficients, *,
                                 split, alpha_set_rad, pitch_bias_rad,
                                 flap_backend="periodic_dmp", reset_initial=None):
    """Load a version-bound executable shape program beside the I-219 recipe.

    Coefficients and aero pitch parameters are explicit adopted caller assets.
    The program is opt-in: no control-loop, joint or force consumer is changed.
    Analytic stroke remains selectable through flap_backend='analytic'.
    """
    doc = json.loads(Path(program_path).read_text(encoding="utf-8"))
    known = {"schema", "recipe_name", "recipe_version", "normalization_version",
             "startup_duration_beats", "stroke_harmonics", "n_basis"}
    if not isinstance(doc, dict):
        raise TrajectoryError("shape program must be an object", where=str(program_path))
    if set(doc) != known:
        raise TrajectoryError("shape program fields mismatch: unknown=%s missing=%s" %
                              (sorted(set(doc) - known), sorted(known - set(doc))), where=str(program_path))
    if doc["schema"] != "cooperative_shapes_v1" or doc["recipe_name"] != recipe.name or doc["recipe_version"] != recipe.version:
        raise TrajectoryError("shape program/recipe version mismatch", where=str(program_path))
    if not isinstance(doc["normalization_version"], str) or not doc["normalization_version"].strip():
        raise TrajectoryError("normalization_version must be nonempty string", where=str(program_path))
    relation = {r.child: r for r in recipe.parent_child}
    if (abs(initial.delta_0 - relation["chi_R"].offset_value) > 1e-12
            or abs(initial.chi_fold - initial.chi_c - relation["chi_fold"].offset_value) > 1e-12):
        raise TrajectoryError("actual service phase offsets disagree with the loaded recipe", where=recipe.name)
    if flap_backend not in ("periodic_dmp", "analytic"):
        raise TrajectoryError("unknown stroke backend", where=flap_backend)
    if initial.step > 0 and reset_initial is None:
        raise TrajectoryError("attachment after step zero requires explicit service reset anchor", where=recipe.name)
    reset_initial = initial if reset_initial is None else reset_initial
    if not isinstance(reset_initial, RhythmSample) or reset_initial.step != 0:
        raise TrajectoryError("reset anchor must be the original step-zero RhythmSample", where=recipe.name)
    harmonics = doc["stroke_harmonics"]
    if not isinstance(harmonics, list) or not harmonics:
        raise TrajectoryError("stroke_harmonics must be a nonempty list", where=str(program_path))
    for i, row in enumerate(harmonics):
        if not isinstance(row, list) or len(row) != 3:
            raise TrajectoryError("expected [harmonic, amplitude, phase_rad]", where=str(i))
        n, a, ph = row
        if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
            raise TrajectoryError("harmonic must be positive integer", where=str(i))
        _finite(a, "harmonic amplitude")
        _finite(ph, "harmonic phase")
    if isinstance(doc["n_basis"], bool) or not isinstance(doc["n_basis"], int) or doc["n_basis"] < 3:
        raise TrajectoryError("n_basis must be integer >=3", where=str(program_path))
    def reference(x):
        return sum(a * math.sin(2 * math.pi * n * x + ph) for n, a, ph in harmonics)
    def reference_velocity(x):
        return sum(2 * math.pi * n * a * math.cos(2 * math.pi * n * x + ph) for n, a, ph in harmonics)
    bindings = {b.binding_id: b for b in recipe.provider_bindings}
    providers, norms = {}, {}
    for row in recipe.channels:
        node = bindings[row.provider_binding].reads_node
        if row.joint.startswith("flap") and flap_backend == "periodic_dmp":
            phase = getattr(initial, node)
            fractional = phase % 1.0
            ref = ReferenceSampleSet.from_analytic(reference, "phase", origin_note="versioned harmonic stroke")
            primitive, norm = fit_reference_periodic(ref, 0.0, 1.0, fractional,
                doc["normalization_version"], n_basis=doc["n_basis"],
                analytic_first=reference_velocity, name=row.joint + "_dmp")
            primitive.reset(reference(fractional), reference_velocity(fractional), phase,
                            note="reference value/rate at actual unwrapped child phase")
            providers[row.joint] = PrimitiveProvider(primitive)
            norms[row.joint] = norm
        elif row.joint.startswith("flap"):
            providers[row.joint] = AnalyticProvider(row.joint, "sin_phase")
        elif row.joint.startswith("twist"):
            providers[row.joint] = AnalyticProvider(row.joint, "aero_pitch", split=split,
                alpha_set_rad=alpha_set_rad, pitch_bias_rad=pitch_bias_rad)
        elif row.joint.startswith("fold"):
            providers[row.joint] = AnalyticProvider(row.joint, "fold_sin_phase",
                phase_offset_rad=row.phase_offset_rad, phase_offset_source=row.phase_offset_source)
        else:
            providers[row.joint] = HoldProvider(row.joint, 0.0,
                "shape zero; adopted absolute tail command is control correction")
    transition = make_spring_transition("startup_amplitude_dmp", 0.0, 1.0)
    runtime = CooperativeTrajectory(recipe, providers, coefficients, initial, transition,
                                    doc["startup_duration_beats"], reset_initial=reset_initial)
    for row in recipe.channels:
        seed = runtime._seed[row.joint]
        if isinstance(seed, PrimitiveProvider):
            phase = getattr(reset_initial, bindings[row.provider_binding].reads_node)
            seed.primitive.reset(reference(phase), reference_velocity(phase), phase,
                                 note="explicit original service reset anchor")
    runtime.normalization = norms
    return runtime
