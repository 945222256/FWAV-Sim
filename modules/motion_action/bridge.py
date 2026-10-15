"""Small native/mock entry bridge. Legacy selection returns no new runtime."""
import math
from .execution import MotionExecution, baseline_from_config


def create_execution(recipe, config, dt):
    if recipe is None:
        return None
    if config.FlapKinematicsConfig.PITCH_MODE != "aero":
        raise ValueError("cooperative_v1 execution currently binds the aero-pitch provider; another pitch mode requires an explicit program")
    execution=MotionExecution(recipe,dt=dt,baseline=baseline_from_config(config),
        split=config.FlapKinematicsConfig.SPLIT,
        alpha_set_rad=math.radians(config.FlapKinematicsConfig.ALPHA_SET),
        pitch_bias_rad=math.radians(config.FlapKinematicsConfig.FEATHER_BIAS))
    execution.derivative_policy="backward_difference_v1"
    return execution


def publish_native(execution, adapter, global_step, namespace=None, force_model=None):
    frame=execution.prepare_step(global_step)
    receipt=adapter.submit(frame)
    execution.record_consumption(receipt)
    if namespace is not None:
        sample=execution.service.read()
        names=list(adapter.articulation.joint_names)
        namespace["_WING_TGT_CACHE"]=[receipt.channels.get(j,{}).get("native_value")
            if receipt.channels.get(j,{}).get("status")=="api_accepted" else None for j in names]
        namespace["_FOLD_T_CACHE"]=frame.channels["fold_L"].value
        namespace["_DA_CACHE"]=float(execution._adopted.command.get("dA",0.) or 0.)
        namespace["_WING_STATE_CACHE"]=[sample.frequency_hz+sample.dfreq_hz,
            sample.frequency_hz-sample.dfreq_hz,
            execution.parameters["flap_L"].gate_value if execution.parameters["flap_L"].gate_value is not None else 1.,
            execution.parameters["flap_R"].gate_value if execution.parameters["flap_R"].gate_value is not None else 1.,
            float(execution._adopted.command.get("dlag",0.) or 0.)]
        namespace["_WING_FREQ_CACHE"]=(execution._adopted.source_info.get("auto_nominal_hz",sample.frequency_hz),sample.frequency_hz,"motion_recipe")
        namespace["_CTRL_MODE_CACHE"]=1 if "auto" in execution._adopted.frequencies else 2 if "manual" in execution._adopted.frequencies else 0
    if receipt.failed:
        raise RuntimeError("new action native write failed; next physical step prohibited")
    if force_model is not None:
        validate_force_inputs(execution,force_model)
    return frame,receipt


def validate_force_inputs(execution,model):
    from .aero_adapter import ExternalActionView,validate_external_model
    try:
        validate_external_model(model)
        view=ExternalActionView.from_execution(execution)
        view.state("L",model)
        view.state("R",model)
    except Exception as exc:
        execution.failed="force-input preflight failed before physical step: %r"%exc
        execution.record_event(dict(kind="force_input_preflight_failed",
            run_id=execution.run_id,segment=execution.segment,global_step=execution._global_step,
            error=repr(exc)))
        raise
