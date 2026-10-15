# -*- coding: utf-8 -*-
"""
contracts.py 硬件抽象层的四个抽象基类
======================================

本文件为整改第六件事的契约模块。抽象基类的英文全称为 Abstract Base
Class，缩写为 ABC。抽象基类的定义为：声明了至少一个抽象方法并且不允
许直接实例化的类，程序只能创建其具体子类的实例。

四个基类的名称与总职责如下。AeroOperator 为气动算子，它计算机翼、尾
翼与机身受到的气动力与气动力矩。Actuator 为执行器，它把控制器输出映
射为关节目标与舵偏。StateSource 为状态源，它读取机体与关节的状态并
标注每个量的坐标系籍贯。SimBackend 为仿真后端，它管理仿真世界的建立、
复位、步进与关闭。

坐标系的定义如下。机体系的英文全称为 body frame。机体系以机体质心为
原点，x 轴指向机头前方，y 轴沿左翼展向，z 轴垂直于机体对称面向上。世
界系的英文全称为 world frame。世界系为仿真世界的惯性参考系，其 z 轴竖
直向上，机体高度取世界系的 z 坐标。坐标系籍贯的定义为：一个物理量所
隶属的坐标系的名称，本模块把它记录在数据结构中名为 frame 的字段里。

本模块的五条设计约束如下。第一，本模块只依赖 Python 标准库，不导入
Isaac Sim 的任何类型，因此纯 Python 模拟后端与 Isaac 后端共用同一份
契约。第二，方法的输入与输出一律使用纯 Python 字典，键名在类与方法
的说明文字中逐个声明。第三，接口签名取自现行调用面，本模块不发明现
行代码中不存在的调用方式。第四，机体系与世界系之间的向量换算集中在
StateSource 一层，控制器侧禁止自行换算。第五，本契约冻结后任何接口
改动必须先上报并获得批准，冻结条款见 docs/契约_四个抽象基类.md。

迁移总依据为整改施工清单二零二六零九三零版第六件事 T6.1 的批准记录。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Protocol, runtime_checkable

__all__ = [
    "AeroOperator",
    "Actuator",
    "StateSource",
    "SimBackend",
    "FRAME_WORLD",
    "FRAME_BODY",
    "FRAME_JOINT",
    "FRAME_BODY_TO_WORLD",
    "STATE_QUANTITY_FRAMES",
    "AERO_RESULT_COMPONENTS",
    "AERO_RESULT_VECTOR_KEYS",
    "ACTUATOR_JOINT_NAMES",
    "COMMAND_KEYS",
    "MOTION_ACTION_TYPES_MODULE",
    "LEGACY_RECIPE_ID",
    "import_motion_action_types",
    "ActionFrameConsumer",
]


# ============================================================
# 坐标系籍贯常量与结构常量
# ============================================================

# 世界系籍贯。位置、线速度与角速度的默认读数隶属世界系。
FRAME_WORLD = "world"
# 机体系籍贯。体轴分量的读数隶属机体系。
FRAME_BODY = "body"
# 关节空间籍贯。关节角与关节速度隶属关节空间，不隶属任何直角坐标系。
FRAME_JOINT = "joint"
# 四元数的籍贯。该常量表示四元数为机体系到世界系的旋转。
FRAME_BODY_TO_WORLD = "body_to_world"

# 状态源 read 返回结构中必须携带坐标系籍贯字段的量及其籍贯取值。
# 缺少任一键或 frame 字段取值不符时，verify_frames 必须报告问题。
STATE_QUANTITY_FRAMES: Dict[str, str] = {
    "position": FRAME_WORLD,
    "quaternion": FRAME_BODY_TO_WORLD,
    "velocity_linear": FRAME_WORLD,
    "velocity_linear_body": FRAME_BODY,
    "angular_velocity": FRAME_WORLD,
    "angular_velocity_body": FRAME_BODY,
    "joint_positions": FRAME_JOINT,
    "joint_velocities": FRAME_JOINT,
}

# 气动算子 compute 返回结构中必须包含的部件名。total 为其余四部件之和。
AERO_RESULT_COMPONENTS = ("wing_left", "wing_right", "tail", "fuselage", "total")

# 每个部件字典中必须包含的两个向量键，单位分别为牛与牛米，均取世界系。
AERO_RESULT_VECTOR_KEYS = ("force_world", "torque_world")

# 执行器必须映射的九个关节名。该名单取自 _drive_wing_joints 的目标字典，
# 前六项为左右翼的拍动、扭转与折叠关节，后三项为尾翼的偏航与左右舵面。
ACTUATOR_JOINT_NAMES = (
    "flap_L", "flap_R",
    "twist_L", "twist_R",
    "fold_L", "fold_R",
    "tail_yaw", "tail_L", "tail_R",
)

# 指令字典的核心十二键（事项 I-138 契约升格）。前三组九键为悬停时代
# 冻结的原始键，语义在 AeroOperator 与 Actuator 两处保持一致。后三键
# 为波形族连续通道（事项 I-138 新增，与 I-073 冻结包络编码对齐），
# 默认值即现行常数：mean 默认取登记库波形中置偏置现行值，行为不变；
# amp_frac 默认一点零即幅值不受调，行为不变；duty 默认一点零即全拍，
# 行为不变。三键只在包络级决策层（I-073 复合动作空间）主动写入时生
# 效，现行自动驾驶仪路径不写入即取默认。
COMMAND_KEYS = (
    "freq", "dfreq", "dA", "dlag", "gate_L", "gate_R",
    "elevator", "aileron", "rudder",
    "mean", "amp_frac", "duty",
)

# 波形族三键的默认常量来源。默认值必须与现行配置值逐位相等，保证
# 不写入新键时执行路径行为逐位不变（回归铁律）。
WAVE_DEFAULTS = {
    "mean": None,       # 运行时从登记库波形中置偏置现行值取，此处无硬编码
    "amp_frac": 1.0,    # 幅值分数为乘数，一点零即不调
    "duty": 1.0,        # 占空比一点零即全拍（I-050 占空比语义切割第一条）
}


# ============================================================
# 完整动作表达的可选消费协议引用（事项 I-219；Fil 审计 §4.2）
# ============================================================

# 完整动作类型所在的模块名。本模块不在运行时导入它，名单与键的引用
# 一律经 import_motion_action_types 的函数内导入完成，避免运行时循环
# 依赖；类型检查工具经本常量定位新类型。
MOTION_ACTION_TYPES_MODULE = "modules.motion_action.types"

# 旧配方标识。取该值（或配方选择为空）时生产入口不装载任何新配方对
# 象，旧驱动路径保持默认，行为与基线逐位一致（回归铁律）。
LEGACY_RECIPE_ID = "legacy_current"


def import_motion_action_types():
    """函数内导入完整动作类型包，返回 modules.motion_action 模块。

    本函数是契约层引用新类型的唯一入口；调用发生在消费方需要新类型
    时，不在模块导入期执行，因此契约模块保持零新增运行时依赖，旧抽
    象执行器的实例化不因新类型的引入而受影响。
    """
    from modules import motion_action
    return motion_action


# ============================================================
# 气动算子契约
# ============================================================

class AeroOperator(ABC):
    """气动算子契约。

    职责：本类按给定的机体状态与翼面指令计算机翼、尾翼与机身受到的气
    动力与气动力矩，并返回按部件分解的合力与合力矩。重力与刚体惯性不
    属于本类的职责，它们由仿真后端的物理求解提供。

    调用时序：仿真后端在初始化阶段调用 initialize 一次，完成叶素网格
    与气动参数的构建。仿真每次复位时后端调用 reset 一次，清空拍动相位
    积分状态与速度滤波保持状态。每个物理步后端调用 compute 一次，调用
    前后端必须把最新机体状态与最新翼面指令写入 state 字典。时间轴在
    整个运行期全局单调推进，复位调用只清空滤波与保持状态，不许把时间
    重置或回跳。

    迁移来源：本类迁移自 modules/aero_solver.py 的 FlapperAeroModel 类
    与 TailFuselageAero 类，翼面气动的多流型实现迁移自 modules/aero
    包内继承 AeroModelBase 的各模型类。施加力与力矩的物理调用不随本
    类迁移，该职责划归仿真后端。
    """

    @abstractmethod
    def initialize(self, config: dict) -> None:
        """构建叶素网格与气动参数，本方法在每个运行期只调用一次。

        config 字典含两个键。"model_name" 为字符串，取值为流型名称，
        现行合法取值为 sane_dickinson、attached_flow、dynamic_stall
        与 sane_dickinson_3d。"params" 为参数覆盖字典，其键与
        FlapperAeroModel 构造参数同名同义，缺省键按现行配置取值。

        迁移来源：本方法迁移自 FlapperAeroModel 与 AeroModelBase 的
        构造函数中的参数读取与网格构建两段。
        """

    @abstractmethod
    def reset(self, state: dict) -> None:
        """清空内部积分状态与滤波状态，仿真复位时调用一次。

        state 字典的结构与 compute 的 state 字典一致。复位必须把拍动
        相位积分状态归零，必须把速度滤波状态置为复位时刻的输入值，必
        须把保持寄存器置为复位时刻的输入值。

        迁移来源：本方法迁移自 AeroPhysicsCallback 中复位后重建的相位
        累加器与滤波字段，包括 _pl_phase、_v_air_hold 与 _ctrl_vel_filt
        三个状态的初始值语义。
        """

    @abstractmethod
    def compute(self, state: dict) -> dict:
        """计算机翼、尾翼与机身的气动力与气动力矩，每个物理步调用一次。

        state 字典含七个键。"t" 为仿真时间，单位秒。"position_world"
        为长度三的世界系位置列表，单位米。"quaternion_world" 为长度四
        的单位四元数列表，元素顺序为 w、x、y、z，表示机体系到世界系
        的旋转。"velocity_linear_world" 为长度三的世界系线速度列表，
        单位米每秒。"angular_velocity_world" 为长度三的世界系角速度列
        表，单位弧度每秒。"wind_world" 为长度三的世界系风速列表，取
        None 表示无风。"commands" 为指令字典，其键为 COMMAND_KEYS 所
        列十二键。前九键的语义为：freq 为集体拍频，单位赫兹；dfreq 为每
        翼频率差动，左翼取 freq 加 dfreq，右翼取 freq 减 dfreq，单位
        赫兹；dA 为翼幅差动，左翼幅值加 dA，右翼幅值减 dA，单位弧度；
        dlag 为翼面俯仰滞后差动，左翼滞后加 dlag，右翼滞后减 dlag，
        单位秒；gate_L 与 gate_R 为左右翼拍动门控，取值一表示本拍拍动，
        取值零表示本拍停拍；elevator、aileron 与 rudder 为尾翼三舵的
        偏角，单位弧度。后三键为波形族连续通道（事项 I-138 与 I-073
        冻结包络编码对齐）：mean 为拍动中置偏置，单位度，缺省取登记
        库现行值即行为不变；amp_frac 为振幅分数，即相对登记幅值的乘
        数，量纲一，缺省一点零即行为不变；duty 为占空比，量纲一，取
        值零到一，缺省一点零即全拍行为不变，占空比持续为零不构成滑
        翔模态（I-050 占空比语义切割第一条）。

        返回字典含 AERO_RESULT_COMPONENTS 所列五键。前四键各映射一个
        部件字典，部件字典含 AERO_RESULT_VECTOR_KEYS 所列两键，两键各
        为长度三的世界系向量列表。"total" 键映射四部件之力与力矩的合
        成。返回字典可附加 "diagnostics" 键，其值为实现自定义的诊断字
        典，现行记录列中的攻角统计与限幅标志归入该键。

        迁移来源：本方法迁移自 FlapperAeroModel.compute_forces 与
        TailFuselageAero.compute 两个求值入口，输入口径沿用
        AeroPhysicsCallback 中相对风速合成与试验台状态锁定的语义。
        """


# ============================================================
# 执行器契约
# ============================================================

class Actuator(ABC):
    """执行器契约。

    职责：本类把控制器输出或手动通道输入映射为九个关节的位置目标与
    尾翼三舵偏角，并把翼面指令转发给气动算子。现行代码的两个事实由
    本类承接。第一个事实为拍动门控：控制器输出的 gate_L 与 gate_R 两
    键决定左右翼本拍是否拍动。第二个事实为关节独立驱动：九个关节各
    有独立的位置目标，拍动、扭转、折叠与尾舵互不共用目标。

    调用时序：仿真后端在每次复位后调用 reset 一次，清空软启动斜坡状
    态与门控状态。每个物理步后端在控制器产出指令之后调用 apply 一次，
    传入指令字典与本步步长。apply 内部依次完成三件事：计算九关节目
    标，写出关节目标，转发翼面指令与舵偏。

    迁移来源：本类迁移自 main_sim.py 的 _drive_wing_joints 函数，尾
    翼舵偏的写出迁移自该函数对 AeroPhysicsCallback.tail_body 的
    set_command 调用链。
    """

    @abstractmethod
    def reset(self) -> None:
        """清空软启动斜坡状态与门控状态，仿真复位时调用一次。

        复位后首个物理步的拍幅斜坡必须从零重新起步，门控状态必须回
        到全拍取值。

        迁移来源：本方法迁移自 _drive_wing_joints 中按仿真时刻计算软
        启动斜坡的起点语义。
        """

    @abstractmethod
    def apply(self, commands: dict, dt: float) -> dict:
        """把指令映射为关节目标与舵偏并写出，每个物理步调用一次。

        commands 字典含 "source" 键与指令体。"source" 的取值为
        "autopilot"、"manual" 或 "idle" 三者之一，三值分别对应自动驾
        驶仪模式、手动模式与怠速模式。取 "autopilot" 时指令体为
        FlightController.update 的返回字典，其键覆盖 COMMAND_KEYS 所
        列九键。取 "manual" 时指令体为手动通道字典，其键为 throttle、
        elevator、aileron、rudder 与 da 五项，throttle 取值负一到正
        一，da 为翼幅差动的直接指令，单位弧度。取 "idle" 时指令体为
        空字典，关节目标取尾翼配平值与零拍幅。dt 为本步步长，单位秒，
        现行 Isaac 后端取一百二十分之一秒。

        返回字典含四键。"joint_targets" 为字典，其键覆盖
        ACTUATOR_JOINT_NAMES 所列九名，其值为各关节位置目标，单位弧
        度。"tail" 为字典，其键为 elevator、aileron 与 rudder，其值
        为三舵偏角，单位弧度。"wing_state" 为字典，其键为 freq_L、
        freq_R、gate_L、gate_R 与 dlag 五项，供记录器落盘。"mode_cache"
        为整数，取值零、一、二，分别对应动力定位模式、自动驾驶仪模式
        与手动模式，语义与现行记录列 mode 一致。

        迁移来源：本方法迁移自 _drive_wing_joints 的主体，含频率同步、
        频率差动注入、翼幅差动注入、门控注入、滞后差动注入、九关节目
        标计算与尾舵指令写出各段。
        """


@runtime_checkable
class ActionFrameConsumer(Protocol):
    """可选动作帧消费协议（事项 I-219；Fil 审计 §4.2）。

    职责：实现本协议的消费方按帧消费完整动作表达。frame 的类型为
    modules.motion_action.types 的 StepAction（当步动作，帧发布后只
    读），返回值为同模块的 ConsumptionResult（消费结果，记录实际采
    用与跳过的关节）。两个类型只在本协议的说明文字中出现，本模块不
    在运行时导入它们；需要类型检查时经 MOTION_ACTION_TYPES_MODULE
    定位。

    可选性：本协议不是新的抽象基类，不要求 Actuator 的任何现有实现
    修改或继承；旧抽象执行器保持可实例化，既有调用者继续使用原指令
    字典（COMMAND_KEYS 十二键）。新配方的消费方以运行时鸭子类型满
    足本协议（typing.Protocol 的 isinstance 检查按方法存在性判定），
    未实现 apply_frame 的对象不参与新路径，不产生接口破坏。
    """

    def apply_frame(self, frame) -> dict:
        """消费一帧完整动作并返回消费结果。

        frame 为 StepAction 只读帧；返回结构须可转换为
        ConsumptionResult（consumer、step、adopted_joints、
        skipped_joints、note 五字段的字典或对象）。实现方不得在消费
        时再次推进节律或重新生成基元；帧发布后只读。
        """
        ...


# ============================================================
# 状态源契约
# ============================================================

class StateSource(ABC):
    """状态源契约。

    职责：本类负责读取机体刚体状态与关节状态，为每个量标注坐标系籍
    贯字段，并集中提供机体系与世界系之间的换算方法。控制器侧禁止自
    行换算坐标系，该禁令的依据为 docs/交接说明_纪律与下一步.md 第一
    节第四条的口径检查要求，以及二零二六年九月二十九日转弯线对世界
    系滤波速度被误作体轴速度使用的修复记录。

    调用时序：仿真后端在 setup 与 reset 之后即可调用 read。控制器在
    每个物理步调用 read 一次并取用返回的位姿与速度，记录器在每个记
    录节拍调用 read 一次并取用返回的全量。同一步内多个消费者必须得
    到同一份数据，该要求的依据为二零二六年九月二十九日双源姿态读数
    分歧的对账记录。

    迁移来源：本类迁移自 modules/flapper_loader.py 的 get_pose 与
    get_velocity 两个读取方法，以及 AeroPhysicsCallback 中经
    RigidPrimView 读取位姿与速度的分支。控制器输入速度的滤波口径迁
    移自 AeroPhysicsCallback 的 held_body_vel 字段。
    """

    @abstractmethod
    def read(self) -> dict:
        """读取机体状态与关节状态并标注坐标系籍贯。

        返回字典含九个必备键与至多一个可选键。"t" 为仿真时间，单位
        秒。八个必备状态量各映射一个字典，该字典含 "value" 键
        与 "frame" 键，value 为取值列表，frame 为坐标系籍贯字符串。
        "position" 的 value 为长度三的世界系位置列表，单位米。
        "quaternion" 的 value 为长度四的单位四元数列表，元素顺序为
        w、x、y、z，表示机体系到世界系的旋转。"velocity_linear" 的
        value 为长度三的世界系线速度列表，单位米每秒。
        "velocity_linear_body" 为 velocity_linear 的机体系换算结果，
        换算必须由本层完成。"angular_velocity" 的 value 为长度三的
        世界系角速度列表，单位弧度每秒。"angular_velocity_body" 为
        angular_velocity 的机体系换算结果，换算必须由本层完成。
        "joint_positions" 与 "joint_velocities" 的 value 为关节位置
        与关节速度列表，两字典各附加 "names" 键，其值为关节名列表，
        两份列表的顺序一致，单位分别为弧度与弧度每秒。返回字典可附
        加 "velocity_linear_filtered" 键，其值为滤波后线速度的机体系
        或世界系读数，其 frame 字段必须如实标注滤波口径的坐标系。

        各量 frame 字段的取值必须与 STATE_QUANTITY_FRAMES 的声明一致，
        实现方与消费方可调用 verify_frames 做校验。

        迁移来源：本方法迁移自 flapper_loader 的两个读取方法与
        AeroPhysicsCallback 的状态读取分支，关节两列表迁移自
        main_sim 对 Articulation 关节接口的调用。
        """

    @staticmethod
    def world_to_body(vector_world: List[float],
                      quaternion: List[float]) -> List[float]:
        """把世界系向量换算到机体系，本方法为集中换算的唯一入口之一。

        vector_world 为长度三的世界系向量列表。quaternion 为长度四的
        单位四元数列表，元素顺序为 w、x、y、z，表示机体系到世界系的
        旋转。返回值为长度三的机体系向量列表。控制器侧禁止自带此换
        算的副本，控制器必须调用本方法或状态源返回的既有体轴字段。
        """
        w, x, y, z = (float(quaternion[0]), float(quaternion[1]),
                      float(quaternion[2]), float(quaternion[3]))
        vx, vy, vz = (float(vector_world[0]), float(vector_world[1]),
                      float(vector_world[2]))
        return [
            (1.0 - 2.0 * (y * y + z * z)) * vx
            + 2.0 * (x * y + w * z) * vy
            + 2.0 * (x * z - w * y) * vz,
            2.0 * (x * y - w * z) * vx
            + (1.0 - 2.0 * (x * x + z * z)) * vy
            + 2.0 * (y * z + w * x) * vz,
            2.0 * (x * z + w * y) * vx
            + 2.0 * (y * z - w * x) * vy
            + (1.0 - 2.0 * (x * x + y * y)) * vz,
        ]

    @staticmethod
    def body_to_world(vector_body: List[float],
                      quaternion: List[float]) -> List[float]:
        """把机体系向量换算到世界系，本方法为集中换算的另一入口。

        vector_body 为长度三的机体系向量列表。quaternion 的语义与
        world_to_body 的同名参数一致。返回值为长度三的世界系向量列
        表。旋转矩阵的构造与 modules/aero_solver.py 的同名辅助函数
        同式，两处必须保持逐项一致。
        """
        w, x, y, z = (float(quaternion[0]), float(quaternion[1]),
                      float(quaternion[2]), float(quaternion[3]))
        vx, vy, vz = (float(vector_body[0]), float(vector_body[1]),
                      float(vector_body[2]))
        return [
            (1.0 - 2.0 * (y * y + z * z)) * vx
            + 2.0 * (x * y - w * z) * vy
            + 2.0 * (x * z + w * y) * vz,
            2.0 * (x * y + w * z) * vx
            + (1.0 - 2.0 * (x * x + z * z)) * vy
            + 2.0 * (y * z - w * x) * vz,
            2.0 * (x * z - w * y) * vx
            + 2.0 * (y * z + w * x) * vy
            + (1.0 - 2.0 * (x * x + y * y)) * vz,
        ]

    @classmethod
    def verify_frames(cls, reading: dict) -> List[str]:
        """校验 read 返回结构的坐标系籍贯字段，返回问题清单。

        reading 为 read 的返回字典。校验规则为两条：STATE_QUANTITY_FRAMES
        所列八量必须存在；每量的 frame 字段取值必须与声明一致。返回值
        为问题描述列表，空列表表示结构合法。实现方在开发期必须调用本
        方法自检，消费方在接入期必须调用本方法验收。
        """
        problems: List[str] = []
        for name, frame_expected in STATE_QUANTITY_FRAMES.items():
            entry = reading.get(name)
            if not isinstance(entry, dict):
                problems.append(f"{name} 缺失或不是字典")
                continue
            frame_actual = entry.get("frame")
            if frame_actual != frame_expected:
                problems.append(
                    f"{name} 的 frame 取值为 {frame_actual}，"
                    f"声明取值为 {frame_expected}")
        return problems


# ============================================================
# 仿真后端契约
# ============================================================

class SimBackend(ABC):
    """仿真后端契约。

    职责：本类负责建立仿真世界与关节样机、复位仿真、推进一个物理步
    并返回观测、关闭仿真。后端组合其余三个契约部件：状态源供读数，
    执行器供关节目标与舵偏写出，气动算子供气动力求值与施加。Isaac 类
    型只允许出现在后端实现内部，契约层与控制器层不得接触 Isaac 类型。

    调用时序：程序入口先调用 setup 一次，随后调用 reset 一次，之后每
    个物理步调用 step 一次，程序结束前调用 close 一次。step 的 actions
    参数为本步控制输入，其结构为 None 或 Actuator.apply 的 commands 字
    典。step 的返回值为观测字典，其结构与 StateSource.read 的返回结构
    一致。气动力施加、关节目标写出与状态读取在 step 内部的相对次序由
    实现自定，实现必须保证同一步返回的观测已含本步施加结果的积分效
    果，该要求对应现行主循环中先步进后读数的次序。

    迁移来源：本类迁移自 main_sim.py 的主循环骨架与世界生命周期管理，
    含 World 构造、world.reset 前后的回调重注册、world.step 单驱动时
    间线与退出清理各段，力施加迁移自 AeroPhysicsCallback 的写出分支。
    本类为将来的两个实现留位：Isaac 后端封装 Isaac Sim，纯 Python 模
    拟后端以简化刚体积分替代 PhysX 求解。
    """

    @abstractmethod
    def setup(self, config: dict) -> None:
        """建立仿真世界与关节样机，每个运行期只调用一次。

        config 字典含五个键。"physics_dt" 为物理步长，单位秒，现行取
        一百二十分之一秒。"scene" 为场景配置字典，其键与现行环境构建
        参数同名同义。"aero" 为气动算子初始化字典，其结构为
        AeroOperator.initialize 的 config。"assets" 为资产路径字典。
        "headless" 为布尔值，取真时不创建渲染窗口。

        迁移来源：本方法迁移自 main_sim.py 的环境桥接段，含
        SimulationApp 启动、World 构造、样机加载与传感器装配各段。
        """

    @abstractmethod
    def reset(self, seed: Optional[int] = None) -> None:
        """复位仿真世界，seed 为随机种子，取 None 表示不做随机化。

        复位必须重建物理回调订阅，必须把仿真时间归零，必须通知三个
        部件同步复位。复位后时间轴从零开始单调推进，后续运行期不许
        再次调用本方法以外的任何时间重置。

        迁移来源：本方法迁移自 main_sim.py 中 world.reset 前后移除并
        重注册物理回调的段落。
        """

    @abstractmethod
    def step(self, actions: Optional[dict] = None) -> dict:
        """推进一个物理步并返回观测。

        actions 为本步控制输入，取 None 表示本步无控制输入，后端按怠
        速处理。返回值为观测字典，其结构与 StateSource.read 的返回结
        构一致。

        迁移来源：本方法迁移自主循环的单步段，含 world.step 调用、控
        制器调度、_drive_wing_joints 调用与看板快照组装的编排次序。
        """

    @abstractmethod
    def close(self) -> None:
        """关闭仿真并释放资源，每个运行期只调用一次。

        关闭前必须落盘未写完的记录并断开网络发送端。

        迁移来源：本方法迁移自 main_sim.py 退出段的记录关闭与应用关
        闭调用。
        """
