"""Shared real/mock action lifecycle, command queue and lag-clock resolution.

The physical owner calls prepare_step before a solve and enqueue after it. New
paths fail closed before the next solve; successful native writes are not undone.
"""
from __future__ import annotations
import copy
import math
from dataclasses import dataclass, replace, field
from pathlib import Path
import uuid

from .cooperative import build_cooperative_trajectory, constant_coefficients
from .parameters import FreqSource, assemble_parameters
from .rhythm import RhythmService
from .types import RhythmSnapshot, StepAction, deep_copy_mapping


@dataclass(frozen=True)
class DemandPacket:
    after_step: int
    command: dict
    frequencies: dict
    source: str
    version: int
    source_info: dict = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "command", deep_copy_mapping(self.command))
        object.__setattr__(self, "frequencies", deep_copy_mapping(self.frequencies))
        object.__setattr__(self, "source_info", deep_copy_mapping(self.source_info))


class ActionExecutionError(RuntimeError):
    pass


class MotionExecution:
    """Owns one common rhythm service and one provider group per run segment."""
    def __init__(self, recipe, *, dt, baseline, split, alpha_set_rad,
                 pitch_bias_rad, program_path=None, run_id=None):
        self.recipe = recipe
        self.dt = float(dt)
        self.baseline = dict(baseline)
        self.split = float(split)
        self.alpha_set_rad = float(alpha_set_rad)
        self.pitch_bias_rad = float(pitch_bias_rad)
        self.program_path = str(program_path or Path(__file__).resolve().parents[2] / "config/motion_programs/cooperative_v1.json")
        self.run_id = run_id or str(uuid.uuid4())
        self.derivative_policy = "reject_unavailable"
        self.segment = 0
        self.warmup_routes = []
        self.lifecycle = []
        self.failed = None
        self.recording_error = None
        self.journal = None
        self._queue = []
        self._version = 0
        self._global_step = 0
        self._frame = None
        self._pinned = False
        self._reset_segment()

    def _reset_segment(self):
        delta = next(r.offset_value for r in self.recipe.parent_child if r.child == "chi_R")
        self.service = RhythmService(self.baseline["freq_hz"], self.dt, self.split, delta_0_beats=delta)
        self._history = [self.service.read()]
        params, adoption = assemble_parameters({}, self.baseline, {"rudder":0.},
            recipe_channels={r.joint:r for r in self.recipe.channels})
        self.parameters = params
        self.adoption = adoption
        self.provider = build_cooperative_trajectory(self.recipe, self.program_path,
            self.service.read(), constant_coefficients(params), split=self.split,
            alpha_set_rad=self.alpha_set_rad, pitch_bias_rad=self.pitch_bias_rad)
        self._adopted = DemandPacket(self._global_step-1, {"rudder":0.}, {}, "baseline", self._version)
        self._segment_origin = self._global_step
        self._frame = None
        self._queue = []
        self._previous_values={j:p.center+p.control_correction+p.legacy_angular_offset for j,p in params.items()}
        self._previous_interval_velocity={j:0. for j in params}

    def warmup_step(self, index, *, route="legacy_initialization"):
        if self._frame is not None:
            raise ActionExecutionError("warmup cannot run after formal frame publication")
        if index != len(self.warmup_routes) + 1:
            raise ActionExecutionError("warmup steps must be individually recorded")
        self.warmup_routes.append((index, route))

    def enqueue(self, *, after_step, command, frequencies, source, source_info=None):
        if after_step < self._global_step:
            raise ActionExecutionError("cannot enqueue a demand in the consumed past")
        allowed = {"freq", "dfreq", "dA", "dlag", "elevator", "aileron", "rudder", "gate_L", "gate_R"}
        if set(command) - allowed:
            raise ActionExecutionError("unknown demand fields: %s" % sorted(set(command)-allowed))
        for key, value in command.items():
            if value is not None and (isinstance(value, bool) or not isinstance(value,(int,float)) or not math.isfinite(value)):
                raise ActionExecutionError("invalid demand %s=%r" % (key,value))
        if set(frequencies) - {"manual", "auto", "backup", "baseline_hold"}:
            raise ActionExecutionError("unknown frequency source")
        self._version += 1
        packet = DemandPacket(after_step, dict(command), dict(frequencies), source, self._version, dict(source_info or {}))
        self._queue.append(packet)
        return packet

    def _lag_clock(self, service, history, sample, lag_s):
        """Query t-lag using real history or a named service-clone prediction.

        No old wing oscillator is evaluated. Future unknown feedback holds the
        currently adopted service values while scheduled programs/events execute.
        """
        target = sample.t_s - lag_s
        points = list(history)
        if target < points[0].t_s - 1e-12:
            raise ActionExecutionError("lag history uninitialized at t=%r" % target)
        if target > points[-1].t_s + 1e-12:
            future = service.clone()
            max_steps = int(math.ceil((target - sample.t_s)/self.dt)) + 1
            if max_steps > 8192:
                raise ActionExecutionError("prediction exceeds declared 8192-step budget")
            for _ in range(max_steps):
                points.append(future.advance(None, None))
                if points[-1].t_s >= target - 1e-12:
                    break
        from .cooperative import RhythmClockAdapter
        adapter = RhythmClockAdapter(sample, 1.)
        for left, right in zip(points, points[1:]):
            if left.t_s - 1e-12 <= target <= right.t_s + 1e-12:
                fraction = min(1.,max(0.,(target-left.t_s)/(right.t_s-left.t_s)))
                boundary = fraction>=1.-1e-10 and (bool(right.events) or right.frequency_hz != left.frequency_hz or right.frozen != left.frozen)
                # Interior of the piecewise constant adopted-frequency interval.
                # At an event crossing, a retrospective interpolant is invalid.
                if right.events and fraction < 1.-1e-10:
                    raise ActionExecutionError("lag query crosses an event with unknown intra-step location")
                phase = left.chi_c + fraction*(right.chi_c-left.chi_c)
                rate = 0. if right.frozen else right.frequency_hz
                clock = adapter.clock(sample, "chi_c")
                return replace(clock, chi=phase, chi_dot=rate,
                    chi_ddot=None if boundary else 0., source="rhythm_service:" + ("prediction" if lag_s<0 else "history")), boundary
        raise ActionExecutionError("lag target has no bracketing service samples")

    def prepare_step(self, global_step):
        if self.recording_error is not None:
            from .journal import ActionRecordingError
            raise ActionRecordingError(self.recording_error)
        if self.failed is not None:
            raise ActionExecutionError("execution stopped: %s" % self.failed)
        if global_step == self._global_step and self._frame is not None:
            return self._frame
        if global_step != self._global_step + 1:
            raise ActionExecutionError("physical steps must be consecutive")
        adopted = self._adopted
        for packet in self._queue:
            if packet.after_step < global_step:
                adopted = packet
        try:
            sources = {k: FreqSource(k, v, str(adopted.version)) for k,v in adopted.frequencies.items()}
            params, adoption = assemble_parameters(sources, self.baseline,
                {k:v for k,v in adopted.command.items() if v is not None},
                recipe_channels={r.joint:r for r in self.recipe.channels})
            frequency = next((sources[k].value_hz for k in ("manual","auto","backup","baseline_hold")
                              if k in sources and sources[k].value_hz is not None), self.baseline["freq_hz"])
            service, provider = self.service.clone(), self.provider.clone()
            new_demand = adopted.version != self._adopted.version
            sample = service.advance(float(frequency) if new_demand else None,
                float(adopted.command.get("dfreq",0.) or 0.) if new_demand else None)
            history = (self._history + [sample])[-8192:]
            overrides, boundaries = {}, []
            for joint in ("twist_L", "twist_R"):
                if params[joint].lag_s:
                    clock, boundary = self._lag_clock(service, history, sample, params[joint].lag_s)
                    overrides[joint] = clock
                    if boundary:
                        boundaries.append(joint)
            frame = provider.accept(sample, coefficients=constant_coefficients(params), clock_overrides=overrides)
            # A shifted frequency/event boundary has no ordinary derivative even
            # if the unshifted present-time frequency is smooth.
            if boundaries:
                channels = dict(frame.channels)
                for joint in boundaries:
                    action = channels[joint]
                    channels[joint] = replace(action, velocity=None, acceleration=None,
                        derivative_available=False,
                        derivative_statuses={k:"event_boundary_unavailable" for k in action.derivative_statuses},
                        derivative_note=action.derivative_note+"; lag query at frequency/event boundary")
                frame = StepAction(frame.step, frame.t_s, frame.rhythm, channels)
            gate_changes=set()
            gated_channels=dict(frame.channels)
            for side in ("L","R"):
                joint="flap_"+side
                gate=params[joint].gate_value
                old_gate=self.parameters[joint].gate_value
                stopped=(gate if gate is not None else 1.)<.5
                was_stopped=(old_gate if old_gate is not None else 1.)<.5
                if stopped!=was_stopped:
                    gate_changes.update((joint,"twist_"+side))
                if stopped:
                    for name,value in ((joint,params[joint].center+params[joint].legacy_angular_offset+params[joint].control_correction),
                                       ("twist_"+side,self.alpha_set_rad*2./math.pi+self.pitch_bias_rad)):
                        action=gated_channels[name]
                        gated_channels[name]=replace(action,value=value,velocity=0.,acceleration=0.,
                            derivative_available=True,derivative_statuses={k:"available" for k in action.derivative_statuses},
                            source=action.source+"; adopted neutral hold gate")
            for joint in gate_changes:
                action=gated_channels[joint]
                gated_channels[joint]=replace(action,velocity=None,acceleration=None,derivative_available=False,
                    derivative_statuses={k:"event_boundary_unavailable" for k in action.derivative_statuses},
                    derivative_note=action.derivative_note+"; gate edge is discontinuous and cannot be force-consumed")
            frame=StepAction(frame.step,frame.t_s,frame.rhythm,gated_channels)
            interval_velocity={j:(c.value-self._previous_values[j])/self.dt
                for j,c in frame.channels.items() if c.value is not None and j in self._previous_values}
            changed_program=(new_demand or sample.frequency_hz!=self.service.read().frequency_hz
                             or sample.dfreq_hz!=self.service.read().dfreq_hz)
            if self.derivative_policy=="backward_difference_v1" and changed_program and not sample.events:
                channels=dict(frame.channels)
                for joint,action in channels.items():
                    if joint not in interval_velocity or not joint.startswith(("flap","twist")):
                        continue
                    if joint in gate_changes:
                        continue
                    base_shape=provider.providers[joint].sample()
                    if base_shape.z_dot is None:  # Actual analytic seam remains unavailable.
                        continue
                    if action.velocity is None or (joint.startswith("flap") and action.acceleration is None):
                        statuses=dict(action.derivative_statuses)
                        for key in statuses:
                            if statuses[key] not in ("available","declared_approximation","not_required"):
                                statuses[key]="declared_approximation"
                        estimated_velocity=interval_velocity[joint]
                        estimated_acceleration=(estimated_velocity-self._previous_interval_velocity[joint])/self.dt
                        channels[joint]=replace(action,
                            velocity=action.velocity if action.velocity is not None else estimated_velocity,
                            acceleration=action.acceleration if action.acceleration is not None else estimated_acceleration,
                            derivative_available=True,derivative_statuses=statuses,
                            derivative_note=action.derivative_note+"; backward_difference_v1 on adopted target samples; approximate")
                frame=StepAction(frame.step,frame.t_s,frame.rhythm,channels)
        except Exception as exc:
            self.failed = repr(exc)
            raise ActionExecutionError("frame preparation failed before physical step: %r" % exc) from exc
        self.service, self.provider, self._history = service, provider, history
        self.parameters, self.adoption, self._adopted = params, adoption, adopted
        self._queue = [p for p in self._queue if p.after_step >= global_step]
        self._global_step = global_step
        self._frame = frame
        self._previous_values={j:c.value for j,c in frame.channels.items() if c.value is not None}
        self._previous_interval_velocity.update(interval_velocity)
        self.lifecycle.append(("frame_published", global_step, self.segment, sample.step))
        return frame

    def read(self):
        if self._frame is None:
            raise ActionExecutionError("first frame has not been published")
        return self._frame

    def clone_for_prediction(self):
        twin=copy.copy(self)
        twin.service=self.service.clone()
        twin.provider=self.provider.clone()
        twin.baseline=dict(self.baseline)
        twin.parameters=copy.deepcopy(self.parameters)
        twin.adoption=dict(self.adoption)
        twin._history=list(self._history)
        twin._queue=list(self._queue)
        twin.lifecycle=list(self.lifecycle)
        twin.warmup_routes=list(self.warmup_routes)
        twin.journal=None  # Prediction never shares a live record writer.
        twin._previous_values=dict(self._previous_values)
        twin._previous_interval_velocity=dict(self._previous_interval_velocity)
        return twin

    def preview_at(self, offset_s):
        """Exact-time finite-window sample on an isolated physical-grid prediction.

        Full steps use the same public prepare path. A fractional final query
        consumes only its canonical interval on a provider copy, never a real
        physical step or receipt. Step-start events follow the service's existing
        adopted rule. All outputs of this method are prediction-only objects.
        """
        if not math.isfinite(offset_s) or offset_s<0:
            raise ActionExecutionError("preview offset must be nonnegative finite")
        twin=self.clone_for_prediction()
        count=int(math.floor(offset_s/self.dt+1e-10))
        if count>8192:
            raise ActionExecutionError("preview exceeds 8192 physical steps")
        for _ in range(count): twin.prepare_step(twin._global_step+1)
        remainder=offset_s-count*self.dt
        if remainder>1e-12:
            trial=twin.clone_for_prediction()
            trial.prepare_step(trial._global_step+1)
            right=trial.service.read()
            remaining=self.dt-remainder
            fc=0. if right.frozen else right.frequency_hz
            fd=0. if right.frozen else right.dfreq_hz
            chi=right.chi_c-fc*remaining
            epsilon=right.epsilon-fd*remaining
            from modules.aero.kinematics import split_phase_map
            phase,rate=split_phase_map(chi%1.,self.split)
            sample=replace(right,t_s=twin.service.read().t_s+remainder,
                chi_c=chi,epsilon=epsilon,chi_L=chi+epsilon,
                chi_R=chi-epsilon+right.delta_0,
                chi_fold=chi+(right.chi_fold-right.chi_c),
                wrapped_tau=chi%1.,beat_index=int(math.floor(chi)),
                shape_phase=phase,shape_phase_rate=rate)
            overrides={}
            for joint in ("twist_L","twist_R"):
                lag=trial.parameters[joint].lag_s
                if lag:
                    overrides[joint],boundary=trial._lag_clock(trial.service,trial._history,sample,lag)
                    if boundary:
                        raise ActionExecutionError("fractional preview lag derivative crosses an event")
            frame=twin.provider.accept(sample,coefficients=constant_coefficients(trial.parameters),clock_overrides=overrides)
            twin._frame=frame
            twin.parameters=trial.parameters
            twin._preview_sample=sample
        return twin

    def record_consumption(self, receipt):
        if receipt.failed:
            self.failed = "native write failed; successful preceding effects retained"
        self.last_receipt = receipt
        if self.journal is not None:
            try:
                self.journal.target(self,receipt)
            except Exception as exc:
                self.recording_error=repr(exc)
                raise

    def release(self, *, reset_phase=True):
        if not reset_phase:
            raise ActionExecutionError("only explicit reset-on-release program is supported")
        if self.failed is not None:
            raise ActionExecutionError("release cannot erase a failed native execution")
        self.segment += 1
        self.failed = None
        self._pinned = False
        self._reset_segment()
        self.lifecycle.append(("release_reset", self._global_step, self.segment))
        self.record_event(dict(kind="release_reset",run_id=self.run_id,segment=self.segment,
            global_step=self._global_step,phase_policy="reset_to_initial_anchor",
            command_queue="cleared",event_queue="cleared",first_frame="not_yet_published"))

    def attach_journal(self,path,*,detailed=False):
        from .journal import ActionJournal
        try:
            self.journal=ActionJournal(path,detailed=detailed)
            snapshot=self.journal.snapshot_program(self)
            self.record_event(dict(kind="run_metadata",schema="motion_action_journal_v1",
                run_id=self.run_id,recipe=self.recipe.name,recipe_version=self.recipe.version,
                baseline=self.baseline,warmup_routes=self.warmup_routes,program_snapshot=snapshot,
                derivative_policy=self.derivative_policy,backend_native_hopf="unsupported"))
        except Exception as exc:
            self.recording_error=repr(exc)
            if self.journal is not None:
                self.journal.close()
            raise

    def record_event(self,record):
        if self.journal is not None:
            try:
                self.journal.append(record)
            except Exception as exc:
                self.recording_error=repr(exc)
                raise

    def close(self):
        if self.journal is not None:
            try:
                self.journal.close()
            except Exception as exc:
                self.recording_error=repr(exc)
                raise

    def controller_context(self):
        sample = self.service.read()
        return deep_copy_mapping({"rhythm_owner":"motion_execution", "beat_index":sample.beat_index,
            "chi_c":sample.chi_c, "native_hopf":"unsupported", "segment":self.segment})


def baseline_from_config(config):
    k, w = config.FlapKinematicsConfig, config.WING_DRIVE_CONFIG
    return dict(freq_hz=float(w["frequency"]), mean_rad=math.radians(k.MEAN),
        amplitude_rad=math.radians(k.AMPLITUDE), da_joint_amp_on=k.DA_JOINT_AMP_ON,
        pitch_lag_s=float(k.PITCH_LAG_S), tail_trim_rad=float(w["tail_trim"]),
        fold_center_rad=float(w["fold_amp"])*.5, fold_scale=float(w["fold_amp"])*.5)


def enqueue_controller_demand(execution, config, *, after_step, ap_out=None,
                              manual=None, backup=None, external=None):
    """Explicit source interface shared by real and ideal-following mock paths."""
    if external is not None:
        return execution.enqueue(after_step=after_step, **external)
    sources, command, source_info = {}, {"rudder":0.}, {}
    auto = ap_out is not None
    if auto:
        from modules.wing_actuation import select_effective_freq
        effective, nominal, source = select_effective_freq(ap_out)
        source_info.update(auto_nominal_hz=nominal,auto_adopted_hz=effective,frequency_selection=source)
        sources["auto"] = effective
        command = {k:float(ap_out[k]) for k in ("dfreq","dA","dlag","elevator","aileron","rudder","gate_L","gate_R") if ap_out.get(k) is not None}
    if backup is not None:
        source_info["backup_snapshot"]=dict(backup)
        if backup.get("freq") is not None:
            sources["backup"] = float(backup["freq"])
    if manual is not None and (getattr(config,"EXPERIMENT_MODE","") or abs(manual.get("throttle",0.))>.05):
        sources["manual"] = config.MANUAL_FREQ_MIN + (config.MANUAL_FREQ_MAX-config.MANUAL_FREQ_MIN)*(manual.get("throttle",0.)+1.)/2.
        # Existing tail command precedence: auto wins while present.
    if not auto and manual is not None:
        command.update(elevator=config.MANUAL_ELEV_GAIN*manual.get("elevator",0.),
                       aileron=config.MANUAL_AIL_GAIN*manual.get("aileron",0.),
                       rudder=config.MANUAL_RUDD_GAIN*manual.get("rudder",0.),
                       dA=manual.get("da",0.))
    return execution.enqueue(after_step=after_step, command=command, frequencies=sources,
                             source="manual" if "manual" in sources else "auto" if auto else "backup" if sources else "baseline",
                             source_info=source_info)
