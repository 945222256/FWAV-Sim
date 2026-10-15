"""External motion view into the existing Sane-Dickinson force implementation."""
from dataclasses import dataclass
from types import SimpleNamespace
import math

from config import sim_config as cfg


@dataclass(frozen=True)
class ExternalActionView:
    frame: object
    frequency_left_hz: float
    frequency_right_hz: float
    amplitude_left_rad: float
    amplitude_right_rad: float
    run_id: str
    segment: int
    global_step: int
    preview: bool = False

    @classmethod
    def from_execution(cls, execution, *, preview=False):
        sample = getattr(execution,"_preview_sample",None) or execution.service.read()
        envelope = execution.provider.transition.sample().z
        return cls(execution.read(), sample.frequency_hz+sample.dfreq_hz,
            sample.frequency_hz-sample.dfreq_hz,
            (execution.parameters["flap_L"].scale+execution.parameters["flap_L"].amplitude_differential)*envelope,
            (execution.parameters["flap_R"].scale+execution.parameters["flap_R"].amplitude_differential)*envelope,
            execution.run_id,execution.segment,execution._global_step,preview)

    def state(self, side, model=None):
        rotational = model.enable_rotational if model is not None else cfg.AeroModelConfig.ENABLE_ROTATIONAL
        added_mass = model.enable_added_mass if model is not None else cfg.AeroModelConfig.ENABLE_ADDED_MASS
        flap, twist = (self.frame.channels[k+"_"+side] for k in ("flap","twist"))
        quantities = [(flap,"value"),(twist,"value"),(flap,"velocity")]
        if rotational:
            quantities.append((twist,"velocity"))
        if added_mass:
            quantities.append((flap,"acceleration"))
        for channel, field in quantities:
            value=getattr(channel,field)
            if channel.presence not in ("value","zero") or value is None or not math.isfinite(value):
                raise RuntimeError("external force input unavailable: %s.%s"%(channel.joint,field))
        for channel in (flap,twist):
            if any(status not in ("available","declared_approximation","not_required")
                   for status in channel.derivative_statuses.values()):
                # Acceleration is optional for flap only when added mass is off.
                statuses=dict(channel.derivative_statuses)
                if not added_mass:
                    statuses.pop("flap_angular_acceleration",None)
                if not rotational:
                    statuses.pop("twist_angular_velocity",None)
                if any(s not in ("available","declared_approximation","not_required") for s in statuses.values()):
                    raise RuntimeError("external force derivative status unavailable: %s"%channel.joint)
        return dict(phi=flap.value,alpha=twist.value,phi_dot=flap.velocity,
            alpha_dot=twist.velocity if twist.velocity is not None else 0.,
            phi_ddot=flap.acceleration if flap.acceleration is not None else 0.,
            alpha_ddot=twist.acceleration)


def validate_external_model(model):
    from modules.aero.sane_dickinson import FlapperAeroModel
    from modules.aero.sane_dickinson_3d import SaneDickinson3DModel
    if type(model) not in (FlapperAeroModel,SaneDickinson3DModel):
        raise RuntimeError("external motion unsupported for actual model %s"%type(model).__name__)


def compute_external(model, view, pos, quat, linear, angular, t):
    """Call the real force method, with explicit states and temporary scale views.

    The temporary wing objects carry physical scales only. They have no evaluate
    or phase state. All original objects are restored even if force evaluation
    raises. The existing force/geometry/damping/guard equations run unchanged.
    """
    validate_external_model(model)
    if getattr(model,"_motion_external_view",None) is not None:
        raise RuntimeError("external force evaluation is not reentrant")
    view.state("L",model)
    view.state("R",model)
    left,right=model.wing_left,model.wing_right
    model.wing_left=SimpleNamespace(frequency=view.frequency_left_hz,amplitude=view.amplitude_left_rad)
    model.wing_right=SimpleNamespace(frequency=view.frequency_right_hz,amplitude=view.amplitude_right_rad)
    model._motion_external_view=view
    try:
        return model.compute_forces(pos,quat,linear,angular,t)
    finally:
        model.wing_left,model.wing_right=left,right
        del model._motion_external_view


def compute_action_window(model, execution, pos, quat, linear, angular):
    """Existing eight-sample left-reference operator; isolated force caches.

    Unequal child frequencies and one-shot/event programs produce a finite
    left-reference window, not a complete common-cycle mean.
    """
    import copy
    import numpy as np
    view=ExternalActionView.from_execution(execution)
    reference_period=1./max(view.frequency_left_hz,.2)
    twin=copy.deepcopy(model)
    forces,torques=[],[]
    sources=[]
    for k in range(8):
        offset=k*reference_period/8.
        predicted=execution.preview_at(offset)
        item=ExternalActionView.from_execution(predicted,preview=True)
        f,t=twin.compute_forces(pos,quat,linear,angular,item.frame.t_s,action_view=item)
        forces.append(f)
        torques.append(t)
        sources.append(dict(offset_s=offset,t_s=item.frame.t_s,frame_step=item.frame.step,
                            run_id=item.run_id,segment=item.segment,preview=True))
    return np.mean(forces,axis=0),np.mean(torques,axis=0),dict(
        kind="finite_left_reference_window",K=8,reference_period_s=reference_period,
        requested_operator="origin+k*period/8",sources=sources,
        fold_geometry_consumed=False,force_model=type(model).__name__,
        cadence="provider physical-step prediction; model caches isolated")
