# 扑翼气动力学模型文档 (Aero Model)

> 模块: `modules/aero/` 包（`aero_solver.py` 为兼容 shim）
> 实现: 2026-07-21（三流型重构）

---

## 零、三流型架构总览

按物理流型分三个模型，全部纯 NumPy（零 Isaac 依赖），共用叶素网格/运动学/回调层：

| 模型 | 流型 | 物理机制 | 切换 |
|---|---|---|---|
| **AttachedFlowModel** (路线A) | 高Re(≳10⁴)、大展弦比(AR≥5)、**惯性力主导、湍流涡脱不占主导** | 附着流环量升力（升力线+AR修正）+ Wagner 失速延迟 + 非环量惯性力 | `AERO_MODEL="attached_flow"`（默认） |
| **DynamicStallModel** (路线B) | 大攻角受控涡（LEV 是工具不是混乱脱落） | LB 三状态：分离点滞后 + 涡环量累积 + 涡寿命到期受控脱落 | `AERO_MODEL="dynamic_stall"` |
| **SaneDickinsonModel** (路线C) | 低Re、小展弦(AR~1-2)昆虫/蜂鸟 | Robofly 极曲线三分量 + 翻转俯仰 | `AERO_MODEL="sane_dickinson"` |

```
modules/aero/
├── common.py          — 基类(叶素网格/compute_forces 模板)+坐标辅助
├── kinematics.py      — WingKinematics:flip(昆虫)/aero(附着流)两种俯仰模式
├── attached_flow.py   — 路线A
├── dynamic_stall.py   — 路线B
├── sane_dickinson.py  — 路线C
├── callback.py        — AeroPhysicsCallback + 三模型工厂(唯一接触 Isaac)
└── run_tests.py       — 独立单元测试(python -m modules.aero.run_tests)
```

**默认几何已切换为大扑翼**：总展 0.60m、AR=6、质量 300g、频率 5Hz——
这是路线A/B 的目标流型。跑路线C 时需覆盖昆虫级几何（见 run_tests 的 C 测试）。

**单元测试结果（2026-07-21 全部通过）**：
- A1 静态升力 vs 升力线理论：比值 0.90 ✓
- A2 Wagner 起动渐近 ✓ / A3 攻角限幅生效 ✓
- B1 小攻角全附着(f=0.95) / B2 大攻角分离+涡累积(Cv=0.93) / B3 涡寿命后衰减 ✓
- B4 俯仰振荡迟滞环（动态失速标志）✓
- C 昆虫几何悬停升力 0.94×体重 ✓

---

## 一、模型选型与依据（路线C 原始记录）

### 为什么选 Sane & Dickinson 2002

| 模型 | 实现成本 | 非定常捕获 | 精度 | 选用 |
|---|---|---|---|---|
| DeLaurier 1993 | 中 | 部分（失速延迟） | 中 | ✗ |
| BEMT 叶素动量 | 高 | 无（需外加） | 中 | ✗ |
| **Sane & Dickinson 2002** | **低** | **LEV + 旋转 + 附加质量** | **高（Robofly 实测拟合）** | **✓** |
| Walker-Smith QSTM | 中 | 部分 | 中 | ✗ |

**核心理由**：
1. **实测拟合系数**：C_N/C_T 来自 Robofly 机器人（Sane&Dickinson 的摆翼实验装置）的真实测量，隐式包含了前缘涡（LEV）增升效应
2. **三分量清晰**：平动 + 旋转 + 附加质量，每项物理意义明确
3. **实时性好**：纯代数运算，无迭代/CFD，120Hz 物理步轻松
4. **蜂鸟尺度验证充分**：Harvard RoboBee、TU Delft 等大量 MAV 研究的标准模型

**文献**：Sane & Dickinson 2002, "The aerodynamic effects of wing rotation and a revised quasi-steady model of flapping flight", *J. Exp. Biol.* 205:1087-1096. (PubMed: https://pubmed.ncbi.nlm.nih.gov/11919268/)

---

## 二、核心方程

### 总气动力分解

```
F_total = F_translational + F_rotational + F_added_mass
```

### 1. 平动力（主导项，~80%）

沿翼展做叶素积分（strip theory）。对每个叶素：

```
v_rel = -(v_body_induced + v_flap)              # 气流相对翼面速度
v_body_induced = v_body + ω × r_body            # 机体诱导（含转动）
v_flap = φ̇ · r · (cos φ, sin φ, 0)              # 拍动诱导（水平面内）

α_eff = α + atan2(v_rel_z, |v_rel_x|)           # 有效攻角

C_N(α) = 3.59 · sin(2α + 9.1°)                  # 法向力系数（Robofly 拟合）
C_T(α) = 1.86 · cos(2α + 9.1°) - 0.86          # 切向力系数

dF_normal = ½ρ|v_rel|² · |C_N| · c · dr         # 法向力大小
dF_vertical = dF_normal · cos(α_eff)            # 垂直分量（向上升力）
dF_tangent = ½ρ|v_rel|² · C_T · c · dr · v̂_rel # 切向力（沿来流=阻力）
```

**关键设计：翻转俯仰（feathering）**
翼面俯仰角 α(t) 与拍动角速度 φ̇(t) **同号耦合**（用 `cos(ωt)`）：
- 上半拍 φ̇>0 时 α>0：气流从下方冲击翼面 → 升力向上
- 下半拍 φ̇<0 时 α<0：翼面翻转，气流仍从下方冲击 → 升力仍向上

这是蜂鸟悬停的物理本质：**两次半拍都产生向上升力**。

### 2. 旋转力（Sane&Dickinson，~10%）

```
dF_rot = C_rot · ρ · c · |v_rel| · α̇ · dr · n̂
```
Magnus 型力，与翼面俯仰角速度 α̇ 成正比。系数 C_rot = 1.55（实测）。

### 3. 附加质量力（~10%，高频拍动重要）

```
dF_added = -ρ · V_added · a_flap
V_added = strip_area · ADDED_MASS_COEFF
a_flap = φ̈ · r                                    # 拍动加速度
```
流体惯性反作用，与翼面加速度成正比。

### 坐标变换

体坐标系算力 → 一次旋转到世界系：
```
R_bw = quat_to_rotation_matrix(quat_world)       # body→world
F_world = R_bw @ F_body
T_world = R_bw @ T_body
```

### 力矩累加（关于 CG，复用 USV 范式）

```
F_body = Σ dF                                    # 所有叶素求和
T_body = Σ (r_body × dF)                         # 关于 CG 的力矩
```

---

## 三、参数与默认值

### 翼几何（`WingGeometryConfig`，校准到 20g 蜂鸟尺度）
| 参数 | 值 | 说明 |
|---|---|---|
| SPAN | 0.10 m | 半展长（总翼展 0.20m） |
| ROOT_CHORD | 0.06 m | 翼根弦长 |
| TIP_CHORD | 0.03 m | 翼尖弦长 |
| N_STRIPS | 8 | 沿展向叶素数 |
| 单翼面积 | 45 cm² | 自动计算 |

### 拍动运动学（`FlapKinematicsConfig`）
| 参数 | 值 | 说明 |
|---|---|---|
| FREQUENCY | 30 Hz | 蜂鸟量级 25~40Hz |
| AMPLITUDE | 35° | 拍动角单侧峰值（蜂鸟实测 30~40°） |
| FEATHER_AMPLITUDE | 40° | 翼面俯仰幅度（需 > 失速角） |
| PHASE_OFFSET_LR | 0° | 左右翼相位差（0=同步悬停） |

### 气动系数（`AeroModelConfig`，Robofly 拟合）
| 参数 | 值 | 来源 |
|---|---|---|
| CN_FIT_AMP | 3.59 | Sane&Dickinson 2002 |
| CN_FIT_PHASE_DEG | 9.1° | 同上 |
| CT_FIT_AMP | 1.86 | 同上 |
| CT_FIT_OFFSET | 0.86 | 同上 |
| C_ROT | 1.55 | 旋转力系数 |
| ADDED_MASS_COEFF | 0.7 | 附加质量系数 |
| RHO_AIR | 1.225 kg/m³ | 海平面 15°C |

### 质量
| 参数 | 值 |
|---|---|
| MASS | 0.020 kg（20g） |
| 体重 | 0.196 N |

---

## 四、单元测试验证（2026-07-21 通过）

运行 `python modules/aero_solver.py`（无需 Isaac Sim）：

```
[几何] 单翼展长=0.1m, 根弦=0.06m, 尖弦=0.03m, 叶素数=8
[几何] 单翼面积=45.00 cm²
[拍动] 频率=30.0Hz, 幅度=35.0°, 俯仰幅度=40.0°

测试 1 静止悬停（v=0）:
  周期平均升力 = +0.264 N
  升力/体重 = 1.35x   ✓（悬停略过冲，PD 控制器会自动降频）

测试 2 前飞 v_x=5 m/s:
  关拍动: Fx = -0.135 N  ✓（纯阻力，方向正确）
  开拍动: Fx = +0.52 N   ✓（拍动推力 > 阻力，扑翼加速前飞）
  周期平均净推力 = +0.037 N  ✓（扑翼前飞特性）
```

**物理合理性确认**：
- 升力方向：上下半拍都向上（翻转俯仰生效）✓
- 阻力方向：反对来流 ✓
- 量级：升力 ≈ 体重（悬停条件满足）✓

---

## 五、调参指南

### 悬停升力不足（升力/体重 < 0.8）
增大下列任一项：
- `FlapKinematicsConfig.FREQUENCY`（最有效，升力 ∝ f²）
- `FlapKinematicsConfig.AMPLITUDE`（升力 ∝ amp²）
- `WingGeometryConfig.ROOT_CHORD` / `TIP_CHORD`（增大翼面积）

### 悬停升力过大（> 2x）
减小上述参数。注意 PD 控制器（`HoverPDController`）会自动调频，所以静态过冲 1.3~1.5x 是正常的。

### 前飞推力不足
- 减小 `FlapKinematicsConfig.FEATHER_AMPLITUDE`（减小俯仰，增加水平分量）
- 或通过 `set_wing_command(pitch_bias_deg=...)` 加俯仰偏置

### 数值不稳定
- 检查 `AeroModelConfig.MAX_WING_VEL`（默认 50 m/s 限幅）
- 降低 `FREQUENCY` 或 `AMPLITUDE`

---

## 六、架构（Model / Callback 分离）

严格镜像 USV `FossenUSVModel` / `HydroPhysicsCallback` 范式：

```
modules/aero_solver.py
├── FlapperAeroModel          ← 纯 NumPy，零 Isaac 依赖，可独立测试
│   ├── WingKinematics        ← 拍动运动学生成器
│   ├── compute_forces()      ← 核心：状态 → [F_world, T_world]
│   └── _wing_forces()        ← 单翼叶素积分
├── AeroPhysicsCallback       ← 薄适配层
│   ├── __init__(world, prim, view, config)
│   ├── __call__(step_size)   ← try/except + 崩溃日志
│   └── _call_inner(dt)       ← 读 PhysX → 调 model → 写回力
├── HoverPDController         ← 内嵌高度/姿态 PD（纯 NumPy）
└── create_aero_callback()    ← 工厂函数
```

**Isaac-free 验证**：`FlapperAeroModel` 不 import 任何 `isaacsim/omni/pxr/carb`，
可通过 `python aero_solver.py` 完全独立运行单元测试（已验证）。

**力注入机制**（与 USV 一致）：
- 读：`RigidPrimView.get_world_poses()` + `get_velocities()`（零 Fabric 锁热路径）
- 写：`apply_forces_and_torques_at_pos(forces, torques, is_global=True)`（不传 positions，PhysX 按 CG 施加）
- PhysX 积分刚体动力学，气动力作为外力（不覆盖位姿/速度）

---

## 七、已知局限与未来扩展

### 当前简化
- ❌ 未建模：前缘涡精细动力学（用 Robofly 系数隐式捕获）
- ❌ 未建模：尾迹捕获（wake capture）
- ❌ 未建模：翼面柔性变形
- ❌ 运动学驱动（无物理铰链，φ/α 作为输入而非 Joint）

### 升级路径
1. **真实资产**：扑翼 USD（Articulation + Joints）→ 改 `_wing_forces` 读关节状态
2. **更精细非定常**：加 Wagner 函数（失速延迟）、LEV 增升模型
3. **柔性翼**：FEM 或集中参数铰链
4. **独立控制器**：`HoverPDController` 拆成 `flapper_controller.py` + UDP（仿 USV）
5. **Warp 加速**：叶素积分用 NVIDIA Warp CUDA kernel（多翼面/多机体场景）

---

## 附录：气动力系数适用范围与不确定性标注（处置保留一至四，2026-09-20 夜间开发）

本附录按《动工计划声明_修订条款与估算处置清单.md》处置类别一（保留七项中
的前四项）的要求，对保留系数标注适用范围与不确定性。

1. **法向力系数幅值 3.59 与切向力系数幅值 1.86**（含相位 9.1° 与常数项
   0.86）：来源为 Sane & Dickinson (2002) 对果蝇尺度机器翼（Robofly）的
   风洞实验拟合。该系数与本机（0.6 m 翼展、215 g、Re 约 10⁴~10⁵）的
   尺度差异未经修正；本项目台架与模型使用同一套模型，项目内不存在修正通道，
   真机风洞或高保真 CFD 之前不确定性定性不变。
2. **旋转力系数 C_ROT=1.55**：同为文献经验值，且与波形运动学参数耦合，
   无独立观测通道，适用范围同上。
3. **附加质量系数 0.7**：经验值（0.5~1.0 区间取值）。敏感性预警（处置
   保留第四项）：附加质量力相对 215 g 机体的等效作用按质量比放大
   3.26 倍；新配平点周期内瞬时力 RMS 占比实测 18.6%
   （`logs/reanchor_scheduling.json`），回归监测列自 2026-09-20 起纳入。
4. **有符号延拓的适用边界**：`modules/aero_solver.py` 的镜像延拓只在
   深反向来流（|v_x|>1.2·2πf·amp·⟨r⟩）激活；已辨识包线内逐位不变。
   反区数值保证力方向正确（保守阻力近似），不保证数值真度。
   推导与四条限制：`docs/有符号C_N推导.md`。
