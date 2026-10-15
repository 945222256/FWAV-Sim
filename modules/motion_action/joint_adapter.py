"""Named native joint writes; API acceptance is separate from physical tracking."""
from dataclasses import dataclass
import math
import numpy as np

from .types import deep_copy_mapping


@dataclass(frozen=True)
class JointWriteReceipt:
    step: int
    operation: str
    channels: dict
    operations: dict
    tail: dict
    failed: bool
    solver_consumption: str = "unknown"

    def __post_init__(self):
        for key in ("channels", "operations", "tail"):
            object.__setattr__(self, key, deep_copy_mapping(getattr(self, key)))


class JointActionAdapter:
    def __init__(self, articulation, recipe, tail_body=None):
        self.articulation = articulation
        self.recipe = recipe
        self.tail_body = tail_body
        self.last_receipt = None

    def submit(self, frame, *, pin=False):
        """Write only named available channels; never overwrite unrelated joints.

        Pin performs direct position AND velocity writes. No API effect is rolled
        back after a later operation fails; each operation retains its own result.
        """
        actual = self.articulation.joint_names
        actual = [] if actual is None else list(actual)
        rows = {row.joint: row for row in self.recipe.channels}
        channels, names, positions, velocities = {}, [], [], []
        invalid = False
        for joint, action in frame.channels.items():
            if action.presence in ("missing", "unsupported"):
                channels[joint] = {"status": action.presence, "value": None}
                continue
            if joint not in actual:
                channels[joint] = {"status": "missing_joint", "value": action.value}
                invalid = True
                continue
            sign = -1.0 if rows[joint].mirrored else 1.0
            value = action.value
            velocity = action.velocity
            if value is None or not math.isfinite(value) or (pin and (velocity is None or not math.isfinite(velocity))):
                channels[joint] = {"status": "unavailable_for_pin" if pin else "invalid_target", "value": value}
                invalid = True
                continue
            names.append(joint)
            positions.append(sign * float(value))
            velocities.append(None if velocity is None else sign * float(velocity))
            channels[joint] = {"status": "requested", "reference_value": value,
                               "native_value": float(np.float32(sign * value)),
                               "native_velocity": None if velocity is None else float(np.float32(sign * velocity))}
        operations = {}
        # An incomplete pin vector cannot represent the requested rigid hold.
        if pin and invalid:
            operations["preflight"] = {"status": "failed", "reason": "pin requires every requested state"}
        elif names:
            values = np.asarray([positions], dtype=np.float32)
            calls = [("direct_position", "set_joint_positions", values)] if pin else []
            if pin:
                calls.append(("direct_velocity", "set_joint_velocities", np.asarray([velocities], dtype=np.float32)))
            calls.append(("position_target", "set_joint_position_targets", values))
            for label, method, array in calls:
                try:
                    getattr(self.articulation, method)(**{
                        "velocities" if label == "direct_velocity" else "positions": array,
                        "joint_names": names})
                    operations[label] = {"status": "api_accepted", "joint_names": tuple(names)}
                except Exception as exc:
                    operations[label] = {"status": "api_failed", "error": repr(exc), "joint_names": tuple(names)}
                    break
            success = all(r["status"] == "api_accepted" for r in operations.values())
            for joint in names:
                channels[joint]["status"] = "api_accepted" if success else "partial_or_failed"
        tail = {"status": "not_bound"}
        if self.tail_body is not None:
            tail_channels = [frame.channels[j] for j in ("tail_L", "tail_R", "tail_yaw")]
            if all(c.presence in ("value", "zero") and c.value is not None for c in tail_channels):
                left, right, rudder = [float(c.value) for c in tail_channels]
                try:
                    self.tail_body.set_command((left+right)/2., (left-right)/2., rudder)
                    tail = {"status": "api_accepted", "absolute_commands": (left, right, rudder)}
                except Exception as exc:
                    tail = {"status": "api_failed", "error": repr(exc)}
            else:
                tail = {"status": "unavailable", "reason": "tail triplet incomplete"}
        failed = invalid or any(r["status"] in ("failed", "api_failed") for r in operations.values()) or tail["status"] in ("api_failed", "unavailable")
        receipt = JointWriteReceipt(frame.step, "pin_state" if pin else "position_target",
                                    channels, operations, tail, failed)
        self.last_receipt = receipt
        return receipt


def submit_ideal_frame(backend,execution,*,pin=False):
    """Use the native adapter on an explicitly ideal-following backend facade."""
    from dataclasses import replace
    from modules.backend.mock_backend import JOINT_NAMES
    class IdealArticulation:
        joint_names=list(JOINT_NAMES)
        def __init__(self):
            self.before=backend._joint_pos.copy()
            self.direct_velocity=False
        def _indices(self,names): return [self.joint_names.index(name) for name in names]
        def set_joint_positions(self,positions,joint_names):
            backend._joint_pos[self._indices(joint_names)]=np.asarray(positions).reshape(-1)
        def set_joint_velocities(self,velocities,joint_names):
            self.direct_velocity=True
            backend._joint_vel[self._indices(joint_names)]=np.asarray(velocities).reshape(-1)
        def set_joint_position_targets(self,positions,joint_names):
            values=np.asarray(positions).reshape(-1)
            indices=self._indices(joint_names)
            backend._joint_pos[indices]=values
            if not self.direct_velocity:
                backend._joint_vel[indices]=(values-self.before[indices])/backend._physics_dt
            backend._joint_targets.update({j:float(v) for j,v in zip(joint_names,values)})
    receipt=JointActionAdapter(IdealArticulation(),execution.recipe,backend.tail_body).submit(execution.read(),pin=pin)
    channels={j:dict(c) for j,c in receipt.channels.items()}
    operations={k:dict(v) for k,v in receipt.operations.items()}
    for result in list(channels.values())+list(operations.values()):
        if result.get("status")=="api_accepted": result["status"]="ideal_state_written"
    return replace(receipt,operation="ideal_pin_state" if pin else "ideal_position_target",
                   channels=channels,operations=operations,solver_consumption="ideal_following")
