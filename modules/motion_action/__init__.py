# -*- coding: utf-8 -*-
"""motion_action — 完整动作对象包（事项 I-219，L2 队）。

本包提供完整动作表达的对象层：配方（含节律节点与关系）、九通道逐项
绑定、参数装配、当步动作与消费结果类型。首个合作配方在
config/motion_recipes/ 落地，旧配方与旧执行器保持默认（回归铁律：
默认不装载新配方时行为逐位不变）。

设计约束（Fil 审计 A03/A06/A08 与节律补充）：
一、帧发布后只读；请求、采用、写入和实测分别记录（本包只定义类型与
装配，不直接写关节——写入由后续事项的执行器接入）。
二、九关节逐项有名称、单位、坐标、限位来源及角色；缺失、未支持与
明确零值分别表达（ValuePresence 三态）。
三、节律节点、父子派生与耦合关系显式描述；父子派生无环，耦合网络
允许闭环但与派生环区分。
四、参数装配显式接收自动、手动、备用拍频及基准快照，不读主程序全
局字典；差频专用限制不加到集体拍频（不设第二层限制）。
"""
from .types import (  # noqa: F401
    VALUE_PRESENCE_MISSING, VALUE_PRESENCE_UNSUPPORTED, VALUE_PRESENCE_ZERO,
    NINE_JOINT_CHANNELS, DERIVATIVE_POLICIES, DERIVATIVE_STATUSES,
    COORDINATE_UNITS, ADAPTER_DERIVATIVE_REQUIREMENTS,
    DERIV_Q_FLAP_ANGULAR_VELOCITY, DERIV_Q_FLAP_ANGULAR_ACCELERATION,
    DERIV_Q_TWIST_ANGULAR_VELOCITY, DERIV_Q_ALL,
    JointChannelSpec, RhythmNode, ParentChildRelation, CouplingRelation,
    ProviderBinding, ChannelBinding, MotionRecipe, RhythmSnapshot,
    ChannelAction, StepAction, ConsumptionResult, RecipeError,
    AdapterDerivativeRequirement, FrozenMapping, deep_copy_mapping,
    load_recipe, load_recipe_by_name, validate_recipe,
)
from .rhythm import (  # noqa: F401
    RhythmService, RhythmSample, RhythmEvent, RhythmServiceError,
    ParamProgramStep, PARAM_PROGRAM_KEYS,
    HopfBackend, REFERENCE_CORE_VERSION, EVENT_BEAT, EVENT_STOP_FLAP,
    EVENT_RESUME_FLAP, EVENT_FREEZE, EVENT_UNFREEZE, EVENT_RESET,
    EVENT_PHASE_LOCK, HOPF_MIN_H_SAMPLES, HOPF_MAX_PHASE_STEP_RAD,
)
from .runtime import (  # noqa: F401
    HistoryBuffer, HistoryEntry, HistoryQueryResult, PreviewEngine,
    PreviewMetadata, PreviewSample, ServiceClock, HISTORY_INTERPOLATION,
    PREVIEW_PREDICTION_METHOD, PREVIEW_PROVIDER_HOOK,
)
from .parameters import (  # noqa: F401
    FreqSource, ChannelParameters, ParameterAssembly, assemble_parameters,
    FeedbackAttribution, FEEDBACK_ATTRIBUTION_TABLE,
    ASSEMBLY_DERIVATIVE_RULE, output_with_derivative,
    CHANNEL_DEFAULT_POLICY, CHANNEL_ZERO_POLICY, CHANNEL_HOLD_POLICY,
    CHANNEL_MODE_EXIT_POLICY, ADOPT_MISSING_DEFAULT,
    ADOPT_ABSOLUTE_COMMAND, ADOPT_TRIM_ONCE, ADOPT_ZERO, ADOPT_HOLD_LAST,
    ADOPT_MODE_EXIT,
)
