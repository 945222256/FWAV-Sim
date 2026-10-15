"""Versioned minimal action/consumer identity alongside existing binary logs."""
import json
import math
import hashlib
from dataclasses import asdict
from pathlib import Path
from .types import deep_copy_mapping


class ActionRecordingError(RuntimeError):
    pass


def record_force_receipt(execution,receipt):
    if execution.journal is None:
        return
    try:
        execution.journal.append(dict(kind="force_submit",run_id=execution.run_id,
            segment=execution.segment,global_step=execution._global_step,receipt=receipt))
    except Exception as exc:
        execution.recording_error=repr(exc)
        raise


def _json_value(value):
    if hasattr(value,"tolist"):
        return _json_value(value.tolist())
    if isinstance(value,dict):
        return {str(k):_json_value(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value,float) and not math.isfinite(value):
        return {"status":"nonfinite","raw":repr(value)}
    return value


class ActionJournal:
    def __init__(self,path,*,detailed=False):
        self.path=Path(path)
        self.detailed=bool(detailed)
        self.error=None
        self.last_projection=None
        self._file=None
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            self._file=self.path.open("x",encoding="utf-8")
        except Exception as exc:
            self.error=repr(exc)
            raise ActionRecordingError("cannot open mandatory action identity journal: %r"%exc) from exc

    def append(self,record):
        if self._file is None:
            raise ActionRecordingError("action journal is closed")
        try:
            self._file.write(json.dumps(_json_value(record),ensure_ascii=False,allow_nan=False)+"\n")
            self._file.flush()
        except Exception as exc:
            self.error=repr(exc)
            raise ActionRecordingError("action record failed; execution effects are not rolled back: %r"%exc) from exc

    def snapshot_program(self,execution):
        sidecar=self.path.with_suffix(".recipe.json")
        record=dict(schema="motion_program_snapshot_v1",recipe=asdict(execution.recipe),
            program=json.loads(Path(execution.program_path).read_text(encoding="utf-8")),
            normalization={j:asdict(n) for j,n in execution.provider.normalization.items()},
            weights={j:list(p.primitive.weights) for j,p in execution.provider.providers.items() if hasattr(p,"primitive")},
            startup_primitive=dict(weights=list(execution.provider.transition.primitive.weights),
                alpha=execution.provider.transition.primitive.alpha,beta=execution.provider.transition.primitive.beta,
                tau_p=execution.provider.transition.primitive.tau_p,
                endpoints=execution.provider.transition.primitive.endpoints,
                duration_beats=execution.provider.adapter.duration_beats),
            source_sha256={name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
                           for name in ("cooperative.py","primitives.py","rhythm.py","execution.py","joint_adapter.py","aero_adapter.py","bridge.py","journal.py")})
        content=json.dumps(_json_value(record),ensure_ascii=False,allow_nan=False,indent=2)
        with sidecar.open("x",encoding="utf-8") as file:
            file.write(content)
        return dict(path=str(sidecar),sha256=hashlib.sha256(sidecar.read_bytes()).hexdigest())

    def target(self,execution,receipt):
        frame=execution.read()
        record=dict(schema="motion_action_journal_v1",kind="target_submit",
            run_id=execution.run_id,segment=execution.segment,global_step=execution._global_step,
            frame_step=frame.step,frame_t_s=frame.t_s,recipe=execution.recipe.name,
            recipe_version=execution.recipe.version,parameter_version=execution._adopted.version,
            source=execution._adopted.source,derivative_policy=execution.derivative_policy,
            operation=receipt.operation,
            source_info=dict(execution._adopted.source_info),
            demand=dict(command=dict(execution._adopted.command),frequency_sources=dict(execution._adopted.frequencies),
                        after_step=execution._adopted.after_step),
            adopted_parameters={j:dict(center=p.center,scale=p.scale,
                amplitude_differential=p.amplitude_differential,legacy_angular_offset=p.legacy_angular_offset,
                control_correction=p.control_correction,lag_s=p.lag_s,gate_value=p.gate_value,
                command_identity=p.command_identity) for j,p in execution.parameters.items()},
            channels={j:dict(requested=c.value,presence=c.presence,velocity=c.velocity,
                            acceleration=c.acceleration,derivative_statuses=dict(c.derivative_statuses),
                            consumer=receipt.channels.get(j,{"status":"missing_receipt"}))
                      for j,c in frame.channels.items()},
            operations=dict(receipt.operations),tail=dict(receipt.tail),failed=receipt.failed,
            solver_consumption_step="unknown")
        if self.detailed:
            record["weights"]={j:list(p.primitive.weights) for j,p in execution.provider.providers.items() if hasattr(p,"primitive")}
        self.last_projection=deep_copy_mapping(record)
        self.append(record)
        return self.last_projection

    def close(self):
        if self._file is not None:
            file,self._file=self._file,None
            try:
                file.close()
            except Exception as exc:
                self.error=repr(exc)
                raise ActionRecordingError("action journal close failed: %r"%exc) from exc
