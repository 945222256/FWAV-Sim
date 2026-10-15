# 参数登记库全量迁移映射表（T3.1 施工产物）

生成日期：2026-09-30。施工项：整改施工清单 T3.1。

## 一、编制基准与口径

行号基准：本表行号对应当日 07 时 20 分前后版 `config/sim_config.py`，全文 1068 行，MD5 值为 `78ef4f0526a5cfe07edc584da37d4e1c`。该文件在本次施工阅读期间被并行施工连续改动两次：第一次新增两项探针参数 `FREQ_PROBE_AMP` 与 `DA_PROBE_AMP`，第二次在探针族后新增 `ROLL_TRIM_M` 并使其后行号整体加三。本表全部行号与计数按最终版取，`ROLL_TRIM_M` 已收录入主表控制增益域。`config/unified_search_config.py` 全文 52 行，期间未变动。

定义计数基准：经语法树清点，`config/sim_config.py` 含模块级定义 179 处、类内属性定义 248 处，合计 427 处。此前调研口径为模块级约一百六十处、类内约二百四十处、合计约四百处，与 427 处同量级，差值来自上述三项新增参数与统计时点。`config/unified_search_config.py` 含数值参数 42 项。

六域界定：本表按施工清单锚点界定六域，另做两处最小扩展并在主表内逐行标注。第一处扩展为气动主域除 `AeroModelConfig` 外，收录路线 A 与路线 B 共用的 `HighARWingConfig`、路线 B 的 `DynamicStallConfig`、直接进入气动求解器的 `WingGeometryConfig` 翼几何、机身阻力两项与空气密度。第二处扩展为控制增益域除 `FlightControllerConfig` 全部数值属性外，收录模块级控制通路增益，即关节驱动增益两项、定高 DP 通路八项、手动映射增益五项与手动拍频界两项。激励探针域收录 PROBE 族与 MULTISINE 族、自动试验 EXP 族与台架项、以及 `unified_search_config.py` 的辨识搜索线全部数值项。

行计数规则：主表每个数值参数一行。数值参数指整型、浮点型、数值元组、数值列表与数值型字典项。布尔开关与字符串选择符不属数值参数，收入附表二。`WING_DRIVE_CONFIG` 字典本体一行定义展开为六个数值行与一个字符串行。`MULTISINE_FREQS` 在代码中为逗号分隔字符串，其内容为频率网格数值，本表按数值列表收录并建议登记为列表。

单位口径：单位优先取自行注释，行注释未明示者按通道语义推定并在单位栏标注「推定」二字。

来源口径：来源说明只写现行注释、登记库记录或库内文档能找到的依据。找不到依据、依据互相冲突、依据与现行值不一致者，主表来源栏写明并收入附表三。来源栏内「行注释」均指定义行及其紧邻注释块；「原注」指 `unified_search_config.py` 各条目自带的 source 字段原文译文。

## 二、主表：六域数值参数映射

### 2.1 气动主域，28 行

| 参数名 | 定义位置 | 所属域 | 建议登记键名 | 单位 | 来源说明 |
|---|---|---|---|---|---|
| AeroModelConfig.RHO_AIR | config/sim_config.py:292 | 气动主域 | aero.rho_air | kg/m³ | 行注释：海平面 15 摄氏度标准大气密度。 |
| AeroModelConfig.CN_FIT_AMP | config/sim_config.py:294 | 气动主域 | aero.wing.cn_fit_amp | 无量纲 | 类文档串：Robofly 实测极曲线拟合常数，取自 Sane 与 Dickinson 2002 年论文；方程文档 docs/AERO_MODEL.md。 |
| AeroModelConfig.CN_FIT_PHASE_DEG | config/sim_config.py:295 | 气动主域 | aero.wing.cn_fit_phase_deg | 度 | 类文档串：同上一行的文献口径。 |
| AeroModelConfig.CT_FIT_AMP | config/sim_config.py:296 | 气动主域 | aero.wing.ct_fit_amp | 无量纲 | 类文档串：同上。 |
| AeroModelConfig.CT_FIT_OFFSET | config/sim_config.py:297 | 气动主域 | aero.wing.ct_fit_offset | 无量纲 | 类文档串：同上。 |
| AeroModelConfig.C_ROT | config/sim_config.py:299 | 气动主域 | aero.wing.c_rot | 无量纲 | 行注释仅标经验值，未给文献或标定记录；来源待考，见附表三第 1 行。 |
| AeroModelConfig.ROT_REF_CHORD_FRAC | config/sim_config.py:300 | 气动主域 | aero.wing.rot_ref_chord_frac | 无量纲弦长比 | 行注释：旋转力参考点取半弦 0.5，属模型假设。 |
| AeroModelConfig.ADDED_MASS_COEFF | config/sim_config.py:302 | 气动主域 | aero.wing.added_mass_coeff | 无量纲 | 行注释标经验域 0.5 至 1.0，未给具体出处；来源待考，见附表三第 2 行。 |
| AeroModelConfig.WING_ROT_DAMP | config/sim_config.py:308 | 气动主域 | aero.wing.rot_damp | N·m·s/rad | 行注释：2026-09-19 第二次调整，取 0.005 保证两档采样率数值稳定；旧值 0.6 为离线扫描值，见 modules/aero_solver.py 相应注释；辨识线文档 docs/旋转阻尼专项辨识.md 系列。 |
| AeroModelConfig.MAX_WING_VEL | config/sim_config.py:314 | 气动主域 | aero.wing.max_wing_vel | m/s | 行注释：单叶素相对速度上限，数值保护自定。 |
| HighARWingConfig.LIFT_SLOPE_2D | config/sim_config.py:233 | 气动主域 | aero.highar.lift_slope_2d | 1/rad | 行注释：薄翼理论升力线斜率 2π；代码为表达式 2.0*np.pi，登记数值取 6.283185307179586。 |
| HighARWingConfig.OSWALD_EFFICIENCY | config/sim_config.py:234 | 气动主域 | aero.highar.oswald_efficiency | 无量纲 | 行注释：椭圆升力分布约 0.9 至 1.0，取 0.85。 |
| HighARWingConfig.PROFILE_DRAG_COEFF | config/sim_config.py:235 | 气动主域 | aero.highar.profile_drag_coeff | 无量纲 | 行注释：薄翼低阻翼型典型型阻系数。 |
| DynamicStallConfig.CN_ALPHA | config/sim_config.py:275 | 气动主域 | aero.stall.cn_alpha | 1/rad | 类文档串：参数量级取自 Leishman 与 Beddoes 1989 年论文的 NACA0012 振荡实验标定，该值为 NACA0012 法向力斜率约 5.73。 |
| DynamicStallConfig.ALPHA_DS_DEG | config/sim_config.py:276 | 气动主域 | aero.stall.alpha_ds_deg | 度 | 类文档串：同上，动态失速触发角。 |
| DynamicStallConfig.SEPARATION_S_DEG | config/sim_config.py:278 | 气动主域 | aero.stall.separation_s_deg | 度 | 类文档串：同上，分离过渡宽度。 |
| DynamicStallConfig.TAU_F | config/sim_config.py:279 | 气动主域 | aero.stall.tau_f | 半弦无量纲时间 | 类文档串：同上。 |
| DynamicStallConfig.TAU_V | config/sim_config.py:280 | 气动主域 | aero.stall.tau_v | 半弦无量纲时间 | 类文档串：同上。 |
| DynamicStallConfig.TAU_VL | config/sim_config.py:281 | 气动主域 | aero.stall.tau_vl | 半弦无量纲时间 | 类文档串：同上，涡寿命。 |
| DynamicStallConfig.VORTEX_DECAY | config/sim_config.py:282 | 气动主域 | aero.stall.vortex_decay | 1/半弦无量纲时间 | 类文档串：同上，涡脱后衰减率。 |
| DynamicStallConfig.MAX_WING_VEL | config/sim_config.py:284 | 气动主域 | aero.stall.max_wing_vel | m/s | 该行无注释；数值保护上限，与附着流路线同取 60.0。 |
| WingGeometryConfig.SPAN | config/sim_config.py:222 | 气动主域 | aero.geometry.span | m | 类文档串：2026-09-20 按论文口径重标注，按 fwav 机构碰撞盒取半展 0.32。 |
| WingGeometryConfig.ROOT_CHORD | config/sim_config.py:223 | 气动主域 | aero.geometry.root_chord | m | 类文档串：同上，翼根弦 0.20。 |
| WingGeometryConfig.TIP_CHORD | config/sim_config.py:224 | 气动主域 | aero.geometry.tip_chord | m | 类文档串：同上，翼尖弦 0.14。 |
| WingGeometryConfig.N_STRIPS | config/sim_config.py:225 | 气动主域 | aero.geometry.n_strips | 条 | 行注释：沿展向叶素数量，离散化自定。 |
| WingGeometryConfig.ROOT_OFFSET | config/sim_config.py:226 | 气动主域 | aero.geometry.root_offset | m 三分量 | 行注释：翼根相对质心偏移，现取零向量。 |
| FUSELAGE_DRAG_COEFF | config/sim_config.py:416 | 气动主域 | aero.fuselage.drag_coeff | 无量纲 | 行注释：圆柱迎风阻力系数，取圆柱绕流典型值。 |
| FUSELAGE_FRONTAL_AREA | config/sim_config.py:417 | 气动主域 | aero.fuselage.frontal_area | m² | 行注释：半径 0.035 m 圆柱正面积，几何换算值。 |

### 2.2 尾翼气动域，13 行

| 参数名 | 定义位置 | 所属域 | 建议登记键名 | 单位 | 来源说明 |
|---|---|---|---|---|---|
| TailAeroConfig.AREA | config/sim_config.py:397 | 尾翼气动域 | aero.tail.area | m² | 行注释：夜间调试第 6 轮由 0.030 改 0.015，随 SparrowHawk 尾面小面积口径。 |
| TailAeroConfig.LEVER_ARM | config/sim_config.py:398 | 尾翼气动域 | aero.tail.lever_arm | m | 行注释：夜间调试第 3 轮符号修正实录，尾压心位于重心之后故取负。 |
| TailAeroConfig.PANEL_Y | config/sim_config.py:399 | 尾翼气动域 | aero.tail.panel_y | m | 行注释仅标舵面半展用途，未给取值依据；来源待考，见附表三第 3 行。 |
| TailAeroConfig.CN_SLOPE | config/sim_config.py:400 | 尾翼气动域 | aero.tail.cn_slope | 1/rad | 行注释：平板小角法向力系数斜率。 |
| TailAeroConfig.STALL_ANGLE | config/sim_config.py:401 | 尾翼气动域 | aero.tail.stall_angle_deg | 度 | 行注释：法向力钳位角，平板失速量级。 |
| TailAeroConfig.VFIN_AREA | config/sim_config.py:409 | 尾翼气动域 | aero.tail.vfin.area | m² | 行注释：URDF 尺寸 0.07 乘 0.055 几何直读，2026-09-27 补全。 |
| TailAeroConfig.VFIN_LEVER | config/sim_config.py:410 | 尾翼气动域 | aero.tail.vfin.lever | m | 行注释：压心与尾面同位。 |
| TailAeroConfig.VFIN_CN_SLOPE | config/sim_config.py:411 | 尾翼气动域 | aero.tail.vfin.cn_slope | 1/rad | 行注释：平板小角同款。 |
| TailAeroConfig.VFIN_STALL_DEG | config/sim_config.py:412 | 尾翼气动域 | aero.tail.vfin.stall_deg | 度 | 行注释：平板小角同款。 |
| TailAeroConfig.VFIN_Z_CP | config/sim_config.py:413 | 尾翼气动域 | aero.tail.vfin.z_cp | m | 行注释：压心高取几何中心 0.0595。 |
| SLIP_DISK_AREA | config/sim_config.py:431 | 尾翼气动域 | aero.tail.slipstream.disk_area | m² | 行注释推导链采用 R 等于 1.15 m 与两倍 0.61 rad，属 2.3 m 旧机型口径，与现行 0.32 m 半展不符；来源待考，见附表三第 4 行。 |
| SLIP_V_PER_HZ | config/sim_config.py:432 | 尾翼气动域 | aero.tail.slipstream.v_per_hz | m/s/Hz | 行注释：2026-09-20 夜间 215 g 重标定，动量理论值 0.731 除以配平频率。 |
| SLIP_K | config/sim_config.py:435 | 尾翼气动域 | aero.tail.slipstream.k | 无量纲 | 行注释：一阶理论值 1.0，旧值 4.0 修正史在案，量级未经台架标定事项已声明。 |

### 2.3 附着流域，6 行

| 参数名 | 定义位置 | 所属域 | 建议登记键名 | 单位 | 来源说明 |
|---|---|---|---|---|---|
| AttachedFlowConfig.WAGNER_A1 | config/sim_config.py:250 | 附着流域 | aero.attached.wagner_a1 | 无量纲 | 类文档串：Jones 两项 Wagner 逼近系数，Phi of tau 约等于 1 减 0.165 乘 e 的负 0.0455 tau 次方再减 0.335 乘 e 的负 0.3 tau 次方。 |
| AttachedFlowConfig.WAGNER_T1 | config/sim_config.py:251 | 附着流域 | aero.attached.wagner_t1 | 半弦无量纲时间 | 类文档串：同上。 |
| AttachedFlowConfig.WAGNER_A2 | config/sim_config.py:252 | 附着流域 | aero.attached.wagner_a2 | 无量纲 | 类文档串：同上。 |
| AttachedFlowConfig.WAGNER_T2 | config/sim_config.py:253 | 附着流域 | aero.attached.wagner_t2 | 半弦无量纲时间 | 类文档串：同上。 |
| AttachedFlowConfig.ALPHA_STALL_DEG | config/sim_config.py:256 | 附着流域 | aero.attached.alpha_stall_deg | 度 | 行注释：取值已放宽至受控动态失速区，与攻角设定值提升相配合。 |
| AttachedFlowConfig.MAX_WING_VEL | config/sim_config.py:258 | 附着流域 | aero.attached.max_wing_vel | m/s | 行注释：翼面运动学速度上限，防数值发散。 |

### 2.4 控制增益域，136 行

前 120 行为 `FlightControllerConfig` 类内数值属性，后 16 行为模块级控制通路增益。

| 参数名 | 定义位置 | 所属域 | 建议登记键名 | 单位 | 来源说明 |
|---|---|---|---|---|---|
| FlightControllerConfig.B_ELEV | config/sim_config.py:586 | 控制增益域 | ctrl.eff.b_elev | N·m/rad | 行注释：2026-09-18 状态锁定辨识等于解析值。 |
| FlightControllerConfig.B_AIL | config/sim_config.py:587 | 控制增益域 | ctrl.eff.b_ail | N·m/rad | 行注释：同上，旧值 1.0 属测量伪影。 |
| FlightControllerConfig.B_RUD | config/sim_config.py:588 | 控制增益域 | ctrl.eff.b_rud | N·m/rad | 行注释：同上。 |
| FlightControllerConfig.K_B_TAIL_ELEV | config/sim_config.py:643 | 控制增益域 | ctrl.eff.sched_tail_elev | N·s²·rad⁻¹·m⁻²，推定 | 行注释：2026-09-19 台架真值，docs/IDENT_20260919.md，动压平方律，随尾面积减半改值。 |
| FlightControllerConfig.K_B_TAIL_AIL | config/sim_config.py:644 | 控制增益域 | ctrl.eff.sched_tail_ail | N·s²·rad⁻¹·m⁻²，推定 | 行注释：同上。 |
| FlightControllerConfig.K_B_TAIL_RUD | config/sim_config.py:645 | 控制增益域 | ctrl.eff.sched_tail_rud | N·s²·rad⁻¹·m⁻²，推定 | 行注释：同上。 |
| FlightControllerConfig.V_TAIL_MIN | config/sim_config.py:646 | 控制增益域 | ctrl.eff.v_tail_min | m/s | 行注释：低空速尾舵权限关闭阈值，防除零设计自定。 |
| FlightControllerConfig.B_DA_SLOPE | config/sim_config.py:886 | 控制增益域 | ctrl.eff.b_da_slope | N·m/rad/Hz² | 行注释：215 g 配平邻域等效，logs/reanchor_scheduling.json。 |
| FlightControllerConfig.B_DA_REF | config/sim_config.py:887 | 控制增益域 | ctrl.eff.b_da_ref | N·m/rad | 行注释：同上，f* 取 1.55 Hz 的实测参考点。 |
| FlightControllerConfig.B_DA_3D | config/sim_config.py:754 | 控制增益域 | ctrl.eff.b_da_3d | N·m/rad | 行注释：三维实测，巡航姿态俯仰负 13 度至正 3 度全程正号稳健。 |
| FlightControllerConfig.DA_MAX_3D | config/sim_config.py:756 | 控制增益域 | ctrl.limit.da_max_3d | rad | 行注释：参数总表编号 6 建议域，已验证域正负 0.10。 |
| FlightControllerConfig.KP_A | config/sim_config.py:590 | 控制增益域 | ctrl.gain.kp_a | 1/s | 行注释载 0.7、2.4、6.0 各段回退史，现值 2.6 的直接整定记录未载；来源待考，见附表三第 12 行。 |
| FlightControllerConfig.KI_A | config/sim_config.py:591 | 控制增益域 | ctrl.gain.ki_a | 1/s² | 行注释称 0.1 为唯一既消静差又不坠毁的取值，现值 0.15 与注释不一致；来源待考，见附表三第 13 行。 |
| FlightControllerConfig.KI_A_MAX | config/sim_config.py:594 | 控制增益域 | ctrl.gain.ki_a_max | rad·s | 行注释：积分权限推导，KI_A 乘 2.5 得 0.25 rad/s 覆盖在环稳态需求 0.23。 |
| FlightControllerConfig.PITCH_INT_GATE | config/sim_config.py:598 | 控制增益域 | ctrl.gate.pitch_int | rad | 行注释：E4 参数化，2026-09-28 机主签字，历史硬编码值即此默认。 |
| FlightControllerConfig.KP_R | config/sim_config.py:604 | 控制增益域 | ctrl.gain.kp_r | s，推定 | 行注释：0.5 软化至 0.25，俯仰双积分器唯一阻尼源的推导。 |
| FlightControllerConfig.DELTA_MAX | config/sim_config.py:608 | 控制增益域 | ctrl.limit.delta_max | rad | 行注释：0.22 放大百分之三十六至 0.30，0920_123505 实录。 |
| FlightControllerConfig.KP_Z | config/sim_config.py:612 | 控制增益域 | ctrl.gain.kp_z | Hz/m | 行注释仅标通道语义，未给取值依据；来源待考，见附表三第 14 行。 |
| FlightControllerConfig.KD_Z | config/sim_config.py:613 | 控制增益域 | ctrl.gain.kd_z | Hz/(m/s) | 行注释：1.2 降至 0.7 的在环理由，释放冲量与斜坡跟随。 |
| FlightControllerConfig.KP_V | config/sim_config.py:615 | 控制增益域 | ctrl.gain.kp_v | 1/s | 行注释：1.5 在环更差已回退，0.10 上调与串级带宽倒挂史。 |
| FlightControllerConfig.KP_XY | config/sim_config.py:619 | 控制增益域 | ctrl.gain.kp_xy | 1/s | 行注释：第八班退回 0.15，0.8 闭环发散实录。 |
| FlightControllerConfig.KI_XY | config/sim_config.py:622 | 控制增益域 | ctrl.gain.ki_xy | 1/s² | 行注释：0.08 提速至 0.15，消截距残差常值静差。 |
| FlightControllerConfig.XI_MAX | config/sim_config.py:624 | 控制增益域 | ctrl.limit.xi_max | m·s | 行注释：积分权限需覆盖 0.09 N 截距残差的推导。 |
| FlightControllerConfig.TILT_MAX | config/sim_config.py:627 | 控制增益域 | ctrl.limit.tilt_max | rad | 行注释：悬停配平前倾 0.206 rad 加机动余量，0.45 无效已回退。 |
| FlightControllerConfig.X_REF | config/sim_config.py:630 | 控制增益域 | ctrl.ref.x | m | 默认设定点零值，类注释称系留锚点附近定点悬停。 |
| FlightControllerConfig.Y_REF | config/sim_config.py:631 | 控制增益域 | ctrl.ref.y | m | 默认设定点零值。 |
| FlightControllerConfig.FREQ_SLEW | config/sim_config.py:632 | 控制增益域 | ctrl.slew.freq | Hz/s | 行注释：相位改为积分状态后可取高速率，旧值 1.0 试验架失败实录。 |
| FlightControllerConfig.K_DA | config/sim_config.py:635 | 控制增益域 | ctrl.gain.k_da | 无量纲 | 行注释：0.12 降至 0.04，防单步把角速率推过阻尼反转门槛。 |
| FlightControllerConfig.K_DA_R | config/sim_config.py:637 | 控制增益域 | ctrl.gain.k_da_r | s，推定 | 行注释：对 120 Hz 控制频率保留离散裕度。 |
| FlightControllerConfig.DA_MAX | config/sim_config.py:638 | 控制增益域 | ctrl.limit.da_max | rad | 行注释：满偏约 0.9 N·m 的分辨率说明。 |
| FlightControllerConfig.Z_REF | config/sim_config.py:639 | 控制增益域 | ctrl.ref.z | m | 行注释：与 DP_TARGET_Z 一致取 3.0，第 4 轮实录两参数不一致的后果。 |
| FlightControllerConfig.YAW_REF_DEG | config/sim_config.py:640 | 控制增益域 | ctrl.ref.yaw_deg | 度 | 默认设定点零值。 |
| FlightControllerConfig.CRUISE_VX | config/sim_config.py:647 | 控制增益域 | ctrl.ref.cruise_vx | m/s | 行注释引配平速度 4.5 出自 2026-09-21 重新标定，现值 3.95 与注释不一致；来源待考，见附表三第 15 行。 |
| FlightControllerConfig.KC_PZ | config/sim_config.py:654 | 控制增益域 | ctrl.gain.kc_pz | rad/m | 行注释：夜间调试第 3 轮在环 60 秒记录。 |
| FlightControllerConfig.KC_DZ | config/sim_config.py:655 | 控制增益域 | ctrl.gain.kc_dz | rad/(m/s) | 行注释：同上。 |
| FlightControllerConfig.KC_FV | config/sim_config.py:656 | 控制增益域 | ctrl.gain.kc_fv | Hz/(m/s) | 行注释：0.15 经试验无效已回退取 0.40。 |
| FlightControllerConfig.KC_IZ | config/sim_config.py:657 | 控制增益域 | ctrl.gain.kc_iz | rad/(m·s) | 行注释：0.004 收敛不足加倍至 0.008 的记录。 |
| FlightControllerConfig.TECS_KF_P | config/sim_config.py:666 | 控制增益域 | ctrl.tecs.kf_p | Hz/(J/kg) | 行注释：0.05 档极限环实录后取 0 关闭。 |
| FlightControllerConfig.TECS_KF_I | config/sim_config.py:668 | 控制增益域 | ctrl.tecs.kf_i | Hz/(J/kg·s) | 行注释：第九班补偿残余阻力史，现值 0。 |
| FlightControllerConfig.TECS_EI_MAX | config/sim_config.py:669 | 控制增益域 | ctrl.limit.tecs_ei | J/kg·s | 行注释：输出权限 0.3 Hz 的推导。 |
| FlightControllerConfig.TECS_VF_K | config/sim_config.py:670 | 控制增益域 | ctrl.tecs.vf_k | Hz/(m/s) | 行注释：候选 A 关闭理由，两被控对象拍频升力符号不一致。 |
| FlightControllerConfig.TECS_VMIN_K | config/sim_config.py:674 | 控制增益域 | ctrl.tecs.vmin_k | Hz/(m/s) | 行注释：f 大于 f* 时两被控对象均为正 Fx。 |
| FlightControllerConfig.TECS_VMIN_FMAX | config/sim_config.py:675 | 控制增益域 | ctrl.tecs.vmin_fmax | Hz | 行注释：在环负升力区下沿，kimi2 坠落实录。 |
| FlightControllerConfig.TECS_ZV_K | config/sim_config.py:677 | 控制增益域 | ctrl.tecs.zv_k | (m/s)/m | 行注释：2026-09-20 夜间开发由试验架确定。 |
| FlightControllerConfig.V_CMD_MIN | config/sim_config.py:684 | 控制增益域 | ctrl.prot.v_cmd_min | m/s | 行注释：第六班设定，与现行验收带下沿相差 0.5 的遗留未决事项在案。 |
| FlightControllerConfig.V_PITCH_FREE | config/sim_config.py:690 | 控制增益域 | ctrl.prot.v_pitch_free | m/s | 行注释：俯仰下压权限衰减起点 3.5。 |
| FlightControllerConfig.V_PITCH_LOCK | config/sim_config.py:691 | 控制增益域 | ctrl.prot.v_pitch_lock | m/s | 行注释：权限归零点 3.0。 |
| FlightControllerConfig.V_DIVE_ALLOW | config/sim_config.py:692 | 控制增益域 | ctrl.prot.v_dive_allow | rad | 行注释：低速浅俯冲余量，在环致命俯冲角负 0.55 实测。 |
| FlightControllerConfig.TECS_KP_E | config/sim_config.py:694 | 控制增益域 | ctrl.tecs.kp_e | rad/(J/kg) | 行注释：二三档证伪记录与第七班取值记录全文。 |
| FlightControllerConfig.TECS_KD_V | config/sim_config.py:700 | 控制增益域 | ctrl.tecs.kd_v | rad/(m/s) | 行注释：0.3 至 0.4 档继电器式振荡故大幅降低。 |
| FlightControllerConfig.TECS_EB_MAX | config/sim_config.py:701 | 控制增益域 | ctrl.limit.tecs_eb | J/kg | 行注释：俯仰指令增量权限封顶正负 0.24 rad。 |
| FlightControllerConfig.ALPHA_FF_GAIN | config/sim_config.py:704 | 控制增益域 | ctrl.tecs.alpha_ff_gain | 无量纲 | 行注释：第 10 轮标定乘 1.3。 |
| FlightControllerConfig.WING_MIX_RATIO | config/sim_config.py:711 | 控制增益域 | ctrl.mix.wing_ratio | s/rad，推定 | 行注释：实测效能比，docs/侧滑工况横侧通道效能.md。 |
| FlightControllerConfig.WING_MIX_DLAG_MAX | config/sim_config.py:712 | 控制增益域 | ctrl.mix.wing_dlag_max | s | 行注释：时机差动已验证域上限。 |
| FlightControllerConfig.BANK_TEST_DEG | config/sim_config.py:716 | 控制增益域 | ctrl.bank.test_deg | 度 | 行注释：任务目标自定值。 |
| FlightControllerConfig.BANK_TEST_T | config/sim_config.py:717 | 控制增益域 | ctrl.bank.test_t | s | 行注释：坡度指令生效时刻自定。 |
| FlightControllerConfig.YAW_FF_ADAPT_TAU | config/sim_config.py:719 | 控制增益域 | ctrl.bank.yaw_ff_adapt_tau | s | 行注释：自校准时间常数自定可调。 |
| FlightControllerConfig.PITCH_FB_FILT_TAU | config/sim_config.py:720 | 控制增益域 | ctrl.filt.pitch_fb_tau | s | 行注释：0 为直读历史行为，守门轮三整定值。 |
| FlightControllerConfig.BANK_SLEW | config/sim_config.py:724 | 控制增益域 | ctrl.bank.slew | rad/s | 行注释：999 为阶跃历史行为，0.3 为科目一整定自定值。 |
| FlightControllerConfig.YAW_FF_DIR | config/sim_config.py:730 | 控制增益域 | ctrl.bank.yaw_ff_dir | 无量纲 | 行注释：在环实测正 10 度坡度对应 wz 为负。 |
| FlightControllerConfig.COORD_KB | config/sim_config.py:732 | 控制增益域 | ctrl.bank.coord_kb | 1/s，推定 | 行注释：0 为纯前馈行为，2.0 为整定自定值。 |
| FlightControllerConfig.COORD_KI | config/sim_config.py:735 | 控制增益域 | ctrl.bank.coord_ki | 1/s²，推定 | 行注释：0 为纯比例行为，R48 后积分顶掉说明。 |
| FlightControllerConfig.COORD_I_MAX | config/sim_config.py:737 | 控制增益域 | ctrl.limit.coord_i | rad/s | 行注释：自定可调。 |
| FlightControllerConfig.BANK_FREQ_CAP | config/sim_config.py:738 | 控制增益域 | ctrl.bank.freq_cap | Hz | 行注释：归因测量定案，失效区边界约 2.4 Hz。 |
| FlightControllerConfig.BANK_PITCH_HOLD_DEG | config/sim_config.py:750 | 控制增益域 | ctrl.bank.pitch_hold_deg | 度 | 行注释：权限包络定案，负 10 度处正 0.053 N·m。 |
| FlightControllerConfig.DA3D_VMIN | config/sim_config.py:757 | 控制增益域 | ctrl.da3d.vmin | m/s | 行注释：0 为恒放行，2.5 为自定可调，R1 教训在案。 |
| FlightControllerConfig.DA3D_TMIN | config/sim_config.py:761 | 控制增益域 | ctrl.da3d.tmin | s | 行注释：R2 教训，与 BANK_TEST_T 对齐自定。 |
| FlightControllerConfig.DA_LPF_TAU | config/sim_config.py:766 | 控制增益域 | ctrl.filt.da_lpf_tau | s | 行注释：R3 教训，0.10 为整定自定值。 |
| FlightControllerConfig.ROLL_FILT_TAU | config/sim_config.py:770 | 控制增益域 | ctrl.filt.roll_tau | s | 行注释：R4 教训，0.12 为整定自定值。 |
| FlightControllerConfig.ROLL_BP_K | config/sim_config.py:776 | 控制增益域 | ctrl.damp.roll_bp | 无量纲 | 行注释：横侧模态带通阻尼增益，0 为关。 |
| FlightControllerConfig.ROLL_BP_F | config/sim_config.py:777 | 控制增益域 | ctrl.damp.roll_bp_f | Hz | 行注释：带通中心频率，整定自定值。 |
| FlightControllerConfig.ROLL_LEAD_STAGES | config/sim_config.py:778 | 控制增益域 | ctrl.damp.roll_lead_stages | 级 | 行注释：1 为单级旧行为，取 2。 |
| FlightControllerConfig.ROLL_TRIM_M | config/sim_config.py:786 | 控制增益域 | ctrl.trim.roll | N·m | 行注释：定常滚转矩前馈配平，G3 与 G4 准稳态窗测得约负 0.01 量级，I-005 源头调查并行，0 为关；2026-09-30 并行施工新增项。 |
| FlightControllerConfig.ROLL_LEAD_FMOD | config/sim_config.py:790 | 控制增益域 | ctrl.damp.roll_lead_fmod | Hz | 行注释：模态频率带通中心族。 |
| FlightControllerConfig.ROLL_LEAD_BP_K | config/sim_config.py:791 | 控制增益域 | ctrl.damp.roll_lead_bp_k | 无量纲 | 行注释：带通超前分量叠加增益。 |
| FlightControllerConfig.ROLL_LEAD_GATE | config/sim_config.py:792 | 控制增益域 | ctrl.damp.roll_lead_gate | rad | 行注释：大瞬态走原始路径防过猛。 |
| FlightControllerConfig.MODAL_CROSS_K | config/sim_config.py:794 | 控制增益域 | ctrl.damp.modal_cross | 无量纲 | 行注释：模态交叉阻尼增益，0 为关。 |
| FlightControllerConfig.PITCH_LEAD_TD | config/sim_config.py:796 | 控制增益域 | ctrl.damp.pitch_lead_td | s | 行注释：两轴联合阻尼，0 为关。 |
| FlightControllerConfig.ROLL_LEAD_TD | config/sim_config.py:798 | 控制增益域 | ctrl.damp.roll_lead_td | s | 行注释：横侧约 1 Hz 模态相位补偿，0 为关。 |
| FlightControllerConfig.ROLL_KI | config/sim_config.py:800 | 控制增益域 | ctrl.gain.roll_ki | 1/s，推定 | 行注释：转弯线整定自定值，0 为关。 |
| FlightControllerConfig.ROLL_INT_GATE | config/sim_config.py:803 | 控制增益域 | ctrl.gate.roll_int | rad | 行注释：自定可调。 |
| FlightControllerConfig.ROLL_I_MAX | config/sim_config.py:804 | 控制增益域 | ctrl.limit.roll_i | rad/s | 行注释：自定可调。 |
| FlightControllerConfig.DA3D_DIR | config/sim_config.py:805 | 控制增益域 | ctrl.da3d.dir | 无量纲 | 行注释：R5 与 R6 在环判别，离线标定差一个镜像。 |
| FlightControllerConfig.DM_PITCH_ZERO_DEG | config/sim_config.py:811 | 控制增益域 | ctrl.dm.pitch_zero_deg | 度 | 行注释：零穿越实测。 |
| FlightControllerConfig.DM_PITCH_FULL_DEG | config/sim_config.py:812 | 控制增益域 | ctrl.dm.pitch_full_deg | 度 | 行注释：门限自定可调。 |
| FlightControllerConfig.SD_KP | config/sim_config.py:820 | 控制增益域 | ctrl.sd.kp | 1/rad，推定 | 行注释未给取值依据；来源待考，见附表三第 20 行。 |
| FlightControllerConfig.SD_KD | config/sim_config.py:821 | 控制增益域 | ctrl.sd.kd | s/rad，推定 | 行注释未给取值依据；来源待考，见附表三第 21 行。 |
| FlightControllerConfig.SD_DEAD | config/sim_config.py:822 | 控制增益域 | ctrl.sd.dead | 无量纲 | 行注释：防小误差抖动死区。 |
| FlightControllerConfig.SD_ROLL_DIR | config/sim_config.py:823 | 控制增益域 | ctrl.sd.roll_dir | 无量纲 | 行注释：符号标定系数，首跑标定。 |
| FlightControllerConfig.DM_KP | config/sim_config.py:830 | 控制增益域 | ctrl.dm.kp | 1/rad，推定 | 行注释：自定初值待整定。 |
| FlightControllerConfig.DM_KD | config/sim_config.py:831 | 控制增益域 | ctrl.dm.kd | s/rad，推定 | 行注释：自定初值待整定。 |
| FlightControllerConfig.DM_DEAD | config/sim_config.py:832 | 控制增益域 | ctrl.dm.dead | 无量纲 | 行注释：连续口径死区。 |
| FlightControllerConfig.FD_KP | config/sim_config.py:837 | 控制增益域 | ctrl.fd.kp | Hz/rad，推定 | 行注释：自定初值，先纯阻尼。 |
| FlightControllerConfig.FD_KD | config/sim_config.py:838 | 控制增益域 | ctrl.fd.kd | Hz/(rad/s)，推定 | 行注释：自定初值待整定。 |
| FlightControllerConfig.FD_DIR | config/sim_config.py:839 | 控制增益域 | ctrl.fd.dir | 无量纲 | 行注释：待离线方向标定确认。 |
| FlightControllerConfig.FD_MAX | config/sim_config.py:842 | 控制增益域 | ctrl.fd.max | Hz | 行注释：f 为 1.8 时每翼不超 2.2。 |
| FlightControllerConfig.FREQ_DIFF_FLO | config/sim_config.py:843 | 控制增益域 | ctrl.fd.f_lo | Hz | 行注释：每翼频率下界，执行器域约束。 |
| FlightControllerConfig.FREQ_DIFF_FHI | config/sim_config.py:844 | 控制增益域 | ctrl.fd.f_hi | Hz | 行注释：每翼频率上界，失效区边界约束。 |
| FlightControllerConfig.GROUND_Z | config/sim_config.py:845 | 控制增益域 | ctrl.prot.ground_z | m | 行注释：0920_123505 坠地空转数据污染教训。 |
| FlightControllerConfig.FREQ_AP_MIN | config/sim_config.py:847 | 控制增益域 | ctrl.prot.freq_ap_min | Hz | 行注释：2026-09-20 第三次裁定全文，1.0 为手动频率域下沿。 |
| FlightControllerConfig.KP_CHI | config/sim_config.py:855 | 控制增益域 | ctrl.gain.kp_chi | 无量纲 | 行注释：0.6 带宽倒挂 25 秒发散后降低取值。 |
| FlightControllerConfig.TECS_KV_F | config/sim_config.py:858 | 控制增益域 | ctrl.tecs.kv_f | Hz/(m/s) | 行注释：第十一班极点配置，在环实测档。 |
| FlightControllerConfig.TECS_EB_V_GAIN | config/sim_config.py:860 | 控制增益域 | ctrl.tecs.eb_v_gain | (m/s)/(J/kg) | 行注释仅标通道语义，第十一班未附证据文件；来源待考，见附表三第 17 行。 |
| FlightControllerConfig.TECS_VZ_P | config/sim_config.py:861 | 控制增益域 | ctrl.tecs.vz_p | rad/(m/s) | 同上；来源待考，见附表三第 18 行。 |
| FlightControllerConfig.TECS_VZ_I | config/sim_config.py:862 | 控制增益域 | ctrl.tecs.vz_i | rad/(m·s) | 同上；来源待考，见附表三第 19 行。 |
| FlightControllerConfig.TECS_VZI_MAX | config/sim_config.py:863 | 控制增益域 | ctrl.limit.tecs_vzi | m/s | 行注释：防积分发散历史复现。 |
| FlightControllerConfig.AIL_AUTH | config/sim_config.py:864 | 控制增益域 | ctrl.ail.auth | 无量纲 | 行注释：第九班全额映射理由，0.15 折扣是权限泄漏点。 |
| FlightControllerConfig.YAW_DAMP | config/sim_config.py:866 | 控制增益域 | ctrl.damp.yaw | 无量纲 | 行注释：第七班收尾新增，2.0 在环不足以压制发散。 |
| FlightControllerConfig.ROLL_DAMP | config/sim_config.py:867 | 控制增益域 | ctrl.damp.roll | 无量纲 | 行注释：67 班独立标度，run3 频谱与饱和率证据。 |
| FlightControllerConfig.PITCH_DAMP | config/sim_config.py:872 | 控制增益域 | ctrl.damp.pitch | 无量纲 | 行注释：第九班浮沉治理新增，1.0 为原行为取值。 |
| FlightControllerConfig.RATE_FILT_TAU | config/sim_config.py:873 | 控制增益域 | ctrl.filt.rate_tau | s | 行注释：0.15 在环更差已回退，截止频率推导在案。 |
| FlightControllerConfig.OUTER_FILT_TAU | config/sim_config.py:876 | 控制增益域 | ctrl.filt.outer_tau | s | 行注释：0.15 试验架失败改 0.08 折中。 |
| FlightControllerConfig.DFX_DTHETA | config/sim_config.py:889 | 控制增益域 | ctrl.deriv.dfx_dtheta | N/rad | 行注释：215 g 新配平点邻域重新推导，logs/reanchor_scheduling.json。 |
| FlightControllerConfig.DFX_DV | config/sim_config.py:892 | 控制增益域 | ctrl.deriv.dfx_dv | N/(m/s) | 行注释：同上，旧值 3.17 属 2.3 m 口径。 |
| FlightControllerConfig.PITCH_TRIM | config/sim_config.py:894 | 控制增益域 | ctrl.ref.pitch_trim | rad | 行注释称符号修正后力矩配平于零并引配平俯仰负 0.05，现值负 0.143246 与注释不一致；来源待考，见附表三第 16 行。 |
| FlightControllerConfig.FREQ_HOVER | config/sim_config.py:896 | 控制增益域 | ctrl.ref.freq_hover | Hz | 行注释：等于 f* 配平点，docs/TRIM_SCAN_215g.md；f* 口径分裂见附表三第 5 行。 |
| FlightControllerConfig.FREQ_MAX_AP | config/sim_config.py:898 | 控制增益域 | ctrl.prot.freq_max_ap | Hz | 行注释：2.26 乘 f* 爬升余量与弹射权限说明。 |
| FlightControllerConfig.PITCH_TGT_FILT_TAU | config/sim_config.py:901 | 控制增益域 | ctrl.filt.pitch_tgt_tau | s | 行注释：第十班候选方案甲，0.5 慢斜坡反面记录。 |
| FlightControllerConfig.PITCH_DES_SLEW | config/sim_config.py:902 | 控制增益域 | ctrl.slew.pitch_des | rad/s | 行注释：232852 run 单变量验证与下限说明。 |
| FlightControllerConfig.DFX_FF_MAX | config/sim_config.py:910 | 控制增益域 | ctrl.limit.dfx_ff | N | 行注释：约 0.11 rad 等效俯仰与饱和区泵能教训。 |
| JOINT_DRIVE_STIFFNESS | config/sim_config.py:43 | 控制增益域 | ctrl.drive.stiffness | N·m/rad | 行注释：2026-09-20 夜间增益扫描取 K 等于 60，docs/驱动重整定_215g.md，30 点扫描矩阵见 logs/drive_sweep 条目。 |
| JOINT_DRIVE_DAMPING | config/sim_config.py:44 | 控制增益域 | ctrl.drive.damping | N·m·s/rad | 行注释：同上，D 取 1.0 为保真与在环可行的折中方案。 |
| DP_TARGET_Z | config/sim_config.py:479 | 控制增益域 | ctrl.dp.target_z | m | 行注释：1.5 改 3.0，夜间调试第 4 轮巡航瞬态高度余量。 |
| DP_KP | config/sim_config.py:480 | 控制增益域 | ctrl.dp.kp | Hz/m | 行注释未给取值依据，且该通路 2026-09-20 起视为失效状态；来源待考，见附表三第 29 行。 |
| DP_KD | config/sim_config.py:481 | 控制增益域 | ctrl.dp.kd | Hz/(m/s) | 同上；来源待考，见附表三第 30 行。 |
| DP_FREQ_NOMINAL | config/sim_config.py:482 | 控制增益域 | ctrl.dp.freq_nominal | Hz | 行注释：等于 f* 配平频率，旧 1.90 为 700 g 口径；f* 口径分裂见附表三第 5 行。 |
| DP_FREQ_MIN | config/sim_config.py:490 | 控制增益域 | ctrl.dp.freq_min | Hz | 行注释：按 f* 取 1.55 的旧比例 0.9 倍设定。 |
| DP_FREQ_MAX | config/sim_config.py:491 | 控制增益域 | ctrl.dp.freq_max | Hz | 行注释：按 1.16 倍设定。 |
| DP_SLEW | config/sim_config.py:492 | 控制增益域 | ctrl.dp.slew | Hz/s | 行注释：防泵能输出斜坡措施。 |
| DP_FILT_TAU | config/sim_config.py:493 | 控制增益域 | ctrl.dp.filt_tau | s | 行注释：输入 EWMA 时间常数防泵能措施。 |
| MANUAL_FREQ_MIN | config/sim_config.py:446 | 控制增益域 | ctrl.manual.freq_min | Hz | 行注释：215 g 配平点覆盖下探。 |
| MANUAL_FREQ_MAX | config/sim_config.py:447 | 控制增益域 | ctrl.manual.freq_max | Hz | 行注释：2.26 乘 f* 爬升余量。 |
| MANUAL_ELEV_GAIN | config/sim_config.py:448 | 控制增益域 | ctrl.manual.elev_gain | rad | 行注释仅标满杆偏角语义；来源待考，见附表三第 25 行。 |
| MANUAL_AIL_GAIN | config/sim_config.py:449 | 控制增益域 | ctrl.manual.ail_gain | rad | 行注释仅标满杆偏角语义；来源待考，见附表三第 26 行。 |
| MANUAL_RUDD_GAIN | config/sim_config.py:450 | 控制增益域 | ctrl.manual.rud_gain | rad | 行注释仅标满杆偏角语义；来源待考，见附表三第 27 行。 |
| MANUAL_KEY_RATE | config/sim_config.py:451 | 控制增益域 | ctrl.manual.key_rate | 1/s | 行注释仅标键盘速率语义；来源待考，见附表三第 28 行。 |

### 2.5 波形运动学域，16 行

| 参数名 | 定义位置 | 所属域 | 建议登记键名 | 单位 | 来源说明 |
|---|---|---|---|---|---|
| FlapKinematicsConfig.FREQUENCY | config/sim_config.py:191 | 波形运动学域 | wave.kinematics.frequency | Hz | 行注释标等于 f* 新配平点；库内注释另载 f* 等于 1.55，口径分裂；来源待考，见附表三第 5 行。 |
| FlapKinematicsConfig.AMPLITUDE | config/sim_config.py:193 | 波形运动学域 | wave.kinematics.amplitude | 度 | 行注释仅存 700 g 口径 50 度与 35 度历史，现行 17.73 依据未载；来源待考，见附表三第 6 行。 |
| FlapKinematicsConfig.BIAS | config/sim_config.py:194 | 波形运动学域 | wave.kinematics.bias | 度 | 拍动角零偏置默认。 |
| FlapKinematicsConfig.FEATHER_AMPLITUDE | config/sim_config.py:195 | 波形运动学域 | wave.kinematics.feather_amplitude | 度 | 行注释标 flip 模式用，现行 PITCH_MODE 为 aero 不启用，取值依据未载；来源待考，见附表三第 11 行。 |
| FlapKinematicsConfig.FEATHER_BIAS | config/sim_config.py:196 | 波形运动学域 | wave.kinematics.feather_bias | 度 | 翼面俯仰零偏置默认。 |
| FlapKinematicsConfig.ALPHA_SET | config/sim_config.py:207 | 波形运动学域 | wave.kinematics.alpha_set | 度 | 类注释载重搜索四参数为 40 度、0.25、负 0.02、0，与现行四值不一致；来源待考，见附表三第 7 行。 |
| FlapKinematicsConfig.PHASE_OFFSET_LR | config/sim_config.py:208 | 波形运动学域 | wave.kinematics.phase_offset_lr | 度 | 左右翼零相位差默认。 |
| FlapKinematicsConfig.SPLIT | config/sim_config.py:209 | 波形运动学域 | wave.kinematics.split | 无量纲 | 类注释同第 7 行不一致事项；来源待考，见附表三第 8 行。 |
| FlapKinematicsConfig.PITCH_LAG_S | config/sim_config.py:210 | 波形运动学域 | wave.kinematics.pitch_lag_s | s | 类注释同上；来源待考，见附表三第 9 行。 |
| FlapKinematicsConfig.MEAN | config/sim_config.py:211 | 波形运动学域 | wave.kinematics.mean | 度 | 类注释同上，旧负 15 度作废说明在案；来源待考，见附表三第 10 行。 |
| WING_DRIVE_CONFIG.frequency | config/sim_config.py:67 | 波形运动学域 | wave.drive.frequency | Hz | 行注释标 700 g 与 2.3 m 大翼机型取值并称与 FlapKinematicsConfig 同步修改，现行 2.5 对 1.8 不一致；来源待考，见附表三第 22 行。 |
| WING_DRIVE_CONFIG.amplitude_deg | config/sim_config.py:68 | 波形运动学域 | wave.drive.amplitude_deg | 度 | 行注释：与 FlapKinematicsConfig.AMPLITUDE 同步，两处数值一致。 |
| WING_DRIVE_CONFIG.alpha_set_deg | config/sim_config.py:70 | 波形运动学域 | wave.drive.alpha_set_deg | 度 | 该项与 wave.kinematics.alpha_set 的 32.3169 度不一致；来源待考，见附表三第 23 行。 |
| WING_DRIVE_CONFIG.fold_amp | config/sim_config.py:71 | 波形运动学域 | wave.drive.fold_amp | rad | 行注释：上拍段折翼幅度。 |
| WING_DRIVE_CONFIG.tail_yaw_amp | config/sim_config.py:72 | 波形运动学域 | wave.drive.tail_yaw_amp | rad | 行注释：演示用途，控制律接管后置零。 |
| WING_DRIVE_CONFIG.tail_trim | config/sim_config.py:73 | 波形运动学域 | wave.drive.tail_trim | rad | 行注释：尾舵配平。 |

### 2.6 激励探针域，79 行

前 7 行为 PROBE 族与 MULTISINE 族，第 8 至 37 行为自动试验与台架激励族，第 38 至 79 行为 `unified_search_config.py` 辨识搜索线。

| 参数名 | 定义位置 | 所属域 | 建议登记键名 | 单位 | 来源说明 |
|---|---|---|---|---|---|
| FlightControllerConfig.AIL_PROBE_AMP | config/sim_config.py:779 | 激励探针域 | probe.ail.amp | rad | 行注释：差动正弦注入口幅值，0 为关；main_sim.py 支持环境变量覆盖。 |
| FlightControllerConfig.ELEV_PROBE_AMP | config/sim_config.py:780 | 激励探针域 | probe.elev.amp | rad | 行注释：升降舵正弦注入口幅值，0 为关。 |
| FlightControllerConfig.RUD_PROBE_AMP | config/sim_config.py:781 | 激励探针域 | probe.rud.amp | rad | 行注释：方向舵正弦注入口幅值，0 为关。 |
| FlightControllerConfig.MULTISINE_FREQS | config/sim_config.py:783 | 激励探针域 | probe.multisine.freqs | Hz 列表 | 行注释：多正弦网格；代码为逗号分隔字符串，建议登记为数值列表；取值依据未载，见附表三第 24 行。 |
| FlightControllerConfig.FREQ_PROBE_AMP | config/sim_config.py:784 | 激励探针域 | probe.freq.amp | Hz | 行注释：拍频调制注入口幅值，五通道之四，0 为关；2026-09-30 并行施工新增项。 |
| FlightControllerConfig.DA_PROBE_AMP | config/sim_config.py:785 | 激励探针域 | probe.da.amp | rad | 行注释：翼幅值差动注入口幅值，五通道之五，0 为关；2026-09-30 并行施工新增项。 |
| FlightControllerConfig.AIL_PROBE_FREQ | config/sim_config.py:789 | 激励探针域 | probe.ail.freq | Hz | 行注释：注入频率取模态频。 |
| EXP_SETTLE_S | config/sim_config.py:534 | 激励探针域 | probe.exp.settle_s | s | 行注释：试验前稳态时间，试验线自定约定。 |
| EXP_SWEEP_FMIN | config/sim_config.py:535 | 激励探针域 | probe.exp.sweep_fmin | Hz | 行注释：扫频下限，试验线自定约定。 |
| EXP_SWEEP_FMAX | config/sim_config.py:536 | 激励探针域 | probe.exp.sweep_fmax | Hz | 行注释：3.5 改 6.0，按 0.6 m 翼面积折算预计 0.7 kg 机体悬停约 4.7 Hz。 |
| EXP_SWEEP_STEP | config/sim_config.py:537 | 激励探针域 | probe.exp.sweep_step | Hz | 行注释：扫频步长，试验线自定约定。 |
| EXP_DWELL_S | config/sim_config.py:538 | 激励探针域 | probe.exp.dwell_s | s | 行注释：每频率点最短驻留，试验线自定约定。 |
| EXP_MIN_CYCLES | config/sim_config.py:539 | 激励探针域 | probe.exp.min_cycles | 周期 | 行注释：每频率点至少扑动周期数，试验线自定约定。 |
| EXP_STEP_ELEV | config/sim_config.py:540 | 激励探针域 | probe.exp.step_elev | rad | 行注释：升降舵阶跃幅值，试验线自定约定。 |
| EXP_STEP_AIL | config/sim_config.py:541 | 激励探针域 | probe.exp.step_ail | rad | 行注释：差动舵阶跃幅值，试验线自定约定。 |
| EXP_STEP_RUD | config/sim_config.py:542 | 激励探针域 | probe.exp.step_rud | rad | 行注释：方向舵阶跃幅值，试验线自定约定。 |
| EXP_STEP_S | config/sim_config.py:543 | 激励探针域 | probe.exp.step_s | s | 行注释：阶跃保持时长，试验线自定约定。 |
| EXP_DA_FREQS | config/sim_config.py:545 | 激励探针域 | probe.exp.da_freqs | Hz 列表 | 行注释：dastep 试验辨识 B_dA，用于取得滚转主通道真值。 |
| EXP_DA_AMPS | config/sim_config.py:546 | 激励探针域 | probe.exp.da_amps | rad 列表 | 行注释：同上。 |
| EXP_DA_STEP_S | config/sim_config.py:547 | 激励探针域 | probe.exp.da_step_s | s | 行注释：每阶保持，试验线自定约定。 |
| EXP_DA_GAP_S | config/sim_config.py:548 | 激励探针域 | probe.exp.da_gap_s | s | 行注释：回零间隔，试验线自定约定。 |
| EXP_DA_SETTLE_S | config/sim_config.py:549 | 激励探针域 | probe.exp.da_settle_s | s | 行注释：每频率点稳定时长，试验线自定约定。 |
| EXP_GAP_S | config/sim_config.py:550 | 激励探针域 | probe.exp.gap_s | s | 行注释：阶跃间隔回零，试验线自定约定。 |
| BENCH_PITCH_DEG | config/sim_config.py:555 | 激励探针域 | probe.exp.bench_pitch_deg | 度 | 行注释：台架俯仰偏置，trim 试验逐段改写。 |
| EXP_TRIM_PITCHES | config/sim_config.py:557 | 激励探针域 | probe.exp.trim_pitches | 度列表 | 行注释：trim 试验俯仰偏置扫阶。 |
| EXP_TRIM_STEP_S | config/sim_config.py:558 | 激励探针域 | probe.exp.trim_step_s | s | 行注释：每角度保持，试验线自定约定。 |
| EXP_TRIM_GAP_S | config/sim_config.py:559 | 激励探针域 | probe.exp.trim_gap_s | s | 行注释：回零间隔，试验线自定约定。 |
| EXP_WIND_SPEEDS | config/sim_config.py:561 | 激励探针域 | probe.exp.wind_speeds | m/s 列表 | 行注释：windcal 试验验证动压规律。 |
| EXP_WIND_STEP_S | config/sim_config.py:562 | 激励探针域 | probe.exp.wind_step_s | s | 行注释：每风速驻留，试验线自定约定。 |
| EXP_KICK_SETTLE_S | config/sim_config.py:566 | 激励探针域 | probe.exp.kick_settle_s | s | 行注释：释放前状态锁定稳态段时长。 |
| EXP_KICK_FREE_S | config/sim_config.py:567 | 激励探针域 | probe.exp.kick_free_s | s | 行注释：释放后自由衰减记录段。 |
| EXP_KICK_W | config/sim_config.py:568 | 激励探针域 | probe.exp.kick_w | rad/s | 行注释：初始角速率脉冲。 |
| EXP_KICK_HOLD_S | config/sim_config.py:569 | 激励探针域 | probe.exp.kick_hold_s | s | 行注释：脉冲段时长。 |
| EXP_RATES | config/sim_config.py:571 | 激励探针域 | probe.exp.rates | rad/s 列表 | 行注释：定速台架测阻尼导数；kick 系列自由衰减法已判不可用的事项在案。 |
| EXP_RATE_SETTLE_S | config/sim_config.py:572 | 激励探针域 | probe.exp.rate_settle_s | s | 行注释：定速前锁定稳态段时长，试验线自定约定。 |
| EXP_RATE_STEP_S | config/sim_config.py:573 | 激励探针域 | probe.exp.rate_step_s | s | 行注释：每速率驻留，试验线自定约定。 |
| EXP_RUDV_SPEEDS | config/sim_config.py:575 | 激励探针域 | probe.exp.rudv_speeds | m/s 列表 | 行注释：尾翼舵效动压调度试验，零点为速度基准。 |
| UNIFIED_SEARCH.hard.split_lt | config/unified_search_config.py:4 | 激励探针域 | search.hard.split_lt | 无量纲 | 原注：第四十九班交换实验加解析，偏差 0.05%。 |
| UNIFIED_SEARCH.hard.drive_limit_deg | config/unified_search_config.py:5 | 激励探针域 | search.hard.drive_limit_deg | 度 | 原注：驱动关节限位实测。 |
| UNIFIED_SEARCH.hard.freq_band | config/unified_search_config.py:6 | 激励探针域 | search.hard.freq_band | Hz 二元组 | 原注：在环执行域，代码钳位。 |
| UNIFIED_SEARCH.hard.my_dc_coef | config/unified_search_config.py:7 | 激励探针域 | search.hard.my_dc_coef | N·m，推定 | 原注：第四十四班三段拟合。 |
| UNIFIED_SEARCH.hard.release_drop_rad | config/unified_search_config.py:8 | 激励探针域 | search.hard.release_drop_rad | rad | 原注：run17 实测 17 ms 掉高。 |
| UNIFIED_SEARCH.hard.ripple_mps | config/unified_search_config.py:9 | 激励探针域 | search.hard.ripple_mps | m/s | 原注：第四十四班实测。 |
| UNIFIED_SEARCH.hard.elev_auth | config/unified_search_config.py:10 | 激励探针域 | search.hard.elev_auth | N·s²·rad⁻¹·m⁻²，推定 | 原注：第十五与十六班回归，动压调度口径。 |
| UNIFIED_SEARCH.hard.elev_travel | config/unified_search_config.py:11 | 激励探针域 | search.hard.elev_travel | rad | 原注：硬件行程签核。 |
| UNIFIED_SEARCH.hard.ixx | config/unified_search_config.py:12 | 激励探针域 | search.hard.ixx | kg·m² | 原注：配置注释口径；与登记库 vehicle.inertia.composite.body_frame 现行值不一致，见附表三第 33 行。 |
| UNIFIED_SEARCH.hard.iyy | config/unified_search_config.py:13 | 激励探针域 | search.hard.iyy | kg·m² | 原注：run9 回归对 PhysX 差 2.6%；与登记库合成值不一致，见附表三第 34 行。 |
| UNIFIED_SEARCH.hard.izz | config/unified_search_config.py:14 | 激励探针域 | search.hard.izz | kg·m² | 原注：按 iyy 类近似，未验证；见附表三第 35 行。 |
| UNIFIED_SEARCH.hard.wing_damp_matrix | config/unified_search_config.py:15 | 激励探针域 | search.hard.wing_damp_matrix | N·m·s/rad 方阵，推定 | 原注：第二十三班新点复测，omega 等于 0.5。 |
| UNIFIED_SEARCH.domain.alpha_set | config/unified_search_config.py:19 | 激励探针域 | search.domain.alpha_set | 度 二元组 | 原注：数值边界非物理，48.5 度为 C_N 峰非墙。 |
| UNIFIED_SEARCH.domain.mean | config/unified_search_config.py:20 | 激励探针域 | search.domain.mean | 度 二元组 | 原注：数值边界，真约束为关节逐点限幅。 |
| UNIFIED_SEARCH.domain.amp | config/unified_search_config.py:21 | 激励探针域 | search.domain.amp | 度 二元组 | 原注：关节逐点边界。 |
| UNIFIED_SEARCH.domain.split | config/unified_search_config.py:22 | 激励探针域 | search.domain.split | 无量纲 二元组 | 原注：运动学钳位加硬约束 c7 解析。 |
| UNIFIED_SEARCH.domain.lag | config/unified_search_config.py:23 | 激励探针域 | search.domain.lag | s 二元组 | 原注自标无数值依据；来源待考，见附表三第 31 行。 |
| UNIFIED_SEARCH.domain.freq | config/unified_search_config.py:24 | 激励探针域 | search.domain.freq | Hz 二元组 | 原注：执行域。 |
| UNIFIED_SEARCH.domain.speed | config/unified_search_config.py:25 | 激励探针域 | search.domain.speed | m/s 二元组 | 原注自标无数值依据；来源待考，见附表三第 32 行。 |
| UNIFIED_SEARCH.domain.ctrl_pos_dims | config/unified_search_config.py:26 | 激励探针域 | search.domain.ctrl_pos_dims | 对数界 二元组 | 原注：数值安全界，增益按反馈方向物理为正。 |
| UNIFIED_SEARCH.domain.ctrl_signed_dims | config/unified_search_config.py:27 | 激励探针域 | search.domain.ctrl_signed_dims | 对数界 二元组 | 原注：数值安全界。 |
| UNIFIED_SEARCH.time.stroke_calib_n | config/unified_search_config.py:30 | 激励探针域 | search.time.stroke_calib_n | 点 | 原注：第十六班纪律。 |
| UNIFIED_SEARCH.time.closed_loop_dt | config/unified_search_config.py:31 | 激励探针域 | search.time.closed_loop_dt | s | 原注：既定。 |
| UNIFIED_SEARCH.time.anchor_dfx_window | config/unified_search_config.py:32 | 激励探针域 | search.time.anchor_dfx_window | s 二元组 | 原注：第四十九班拟合段。 |
| UNIFIED_SEARCH.time.prescreen_s | config/unified_search_config.py:33 | 激励探针域 | search.time.prescreen_s | s | 原注：运行惯例，误杀风险已记录。 |
| UNIFIED_SEARCH.time.final_s | config/unified_search_config.py:34 | 激励探针域 | search.time.final_s | s | 原注：验收。 |
| UNIFIED_SEARCH.criteria.need_frac | config/unified_search_config.py:37 | 激励探针域 | search.criteria.need_frac | 无量纲 | 原注：四十五与五十班预登记，历史签核。 |
| UNIFIED_SEARCH.criteria.lift_min | config/unified_search_config.py:37 | 激励探针域 | search.criteria.lift_min | 体重倍数 | 原注：同上。 |
| UNIFIED_SEARCH.criteria.z_band | config/unified_search_config.py:37 | 激励探针域 | search.criteria.z_band | m 二元组 | 原注：同上。 |
| UNIFIED_SEARCH.criteria.th_tol | config/unified_search_config.py:37 | 激励探针域 | search.criteria.th_tol | rad，推定 | 原注：同上。 |
| UNIFIED_SEARCH.criteria.elev_rms_frac | config/unified_search_config.py:37 | 激励探针域 | search.criteria.elev_rms_frac | 无量纲 | 原注：同上。 |
| UNIFIED_SEARCH.criteria.freq_dev_tol | config/unified_search_config.py:38 | 激励探针域 | search.criteria.freq_dev_tol | Hz | 原注：同上。 |
| UNIFIED_SEARCH.criteria.dfx_gt | config/unified_search_config.py:38 | 激励探针域 | search.criteria.dfx_gt | N | 原注：同上。 |
| UNIFIED_SEARCH.engine.batch | config/unified_search_config.py:42 | 激励探针域 | search.engine.batch | 个 | 原注：用户指令，顺序定向批量并行。 |
| UNIFIED_SEARCH.engine.exploit | config/unified_search_config.py:42 | 激励探针域 | search.engine.exploit | 个 | 原注：同上。 |
| UNIFIED_SEARCH.engine.explore | config/unified_search_config.py:42 | 激励探针域 | search.engine.explore | 个 | 原注：同上。 |
| UNIFIED_SEARCH.engine.n_batches | config/unified_search_config.py:42 | 激励探针域 | search.engine.n_batches | 批 | 原注：同上。 |
| UNIFIED_SEARCH.engine.n_ctrl_inner | config/unified_search_config.py:42 | 激励探针域 | search.engine.n_ctrl_inner | 轮 | 原注：同上。 |
| UNIFIED_SEARCH.engine.procs | config/unified_search_config.py:42 | 激励探针域 | search.engine.procs | 进程 | 原注：同上。 |
| UNIFIED_SEARCH.anchor.dFx_df_A | config/unified_search_config.py:48 | 激励探针域 | search.anchor.dfx_df_a | N/Hz，含容差与波形七元组 | 原注：期望 0.327，容差 0.05。 |
| UNIFIED_SEARCH.anchor.dFx_df_B | config/unified_search_config.py:49 | 激励探针域 | search.anchor.dfx_df_b | N/Hz，含容差与波形七元组 | 原注：期望负 0.4495，容差 0.05。 |
| UNIFIED_SEARCH.anchor.my_dc | config/unified_search_config.py:50 | 激励探针域 | search.anchor.my_dc | N·m，含容差 | 原注：期望负 0.0186，容差 0.002。 |

## 三、附表一：已迁移参数表，登记库已有键，35 行

本表逐键列出参数登记库 `params_registry/params.json` 现有全部键。数值栏中长小数按有效位截断，全值以 params.json 为准。

| 登记键名 | 数值 | 单位 | 状态 | sim_config 侧对应与备注 |
|---|---|---|---|---|
| vehicle.mass.total | 0.215 | kg | current | 对应 config/sim_config.py:159 的 MASS，数值一致；来源为 2026-09-20 用户工作令裁定。 |
| vehicle.inertia.ixx.reference | 0.00036 至 0.00076 | kg·m² | reference | 无现行 sim_config 行，论文真值区间。 |
| link.base_link | 8.138e-05 等 四元组 | kg 与 kg·m² | current | 登记库过渡键，新条目为 vehicle.inertia.link.base_link。 |
| link.flapconn_L | 1.454e-06 等 | kg 与 kg·m² | current | 同上。 |
| link.flapconn_R | 1.454e-06 等 | kg 与 kg·m² | current | 同上。 |
| link.innerwing_L | 3.2416e-05 等 | kg 与 kg·m² | current | 同上。 |
| link.innerwing_R | 3.2416e-05 等 | kg 与 kg·m² | current | 同上。 |
| link.outerwing_L | 1.920675e-05 等 | kg 与 kg·m² | current | 同上。 |
| link.outerwing_R | 1.920675e-05 等 | kg 与 kg·m² | current | 同上。 |
| link.tail_base | 8.0837e-06 等 | kg 与 kg·m² | current | 同上。 |
| link.tailpanel_L | 6.222e-06 等 | kg 与 kg·m² | current | 同上。 |
| link.tailpanel_R | 6.222e-06 等 | kg 与 kg·m² | current | 同上。 |
| vehicle.mass.link.base_link | 0.135 | kg | current | 自已删除的 MASS_TABLE 迁移，I-002 施工，docs/INERTIA_TABLE_215g.md。 |
| vehicle.mass.link.flapconn_L | 0.006 | kg | current | 同上。 |
| vehicle.mass.link.flapconn_R | 0.006 | kg | current | 同上。 |
| vehicle.mass.link.innerwing_L | 0.012 | kg | current | 同上。 |
| vehicle.mass.link.innerwing_R | 0.012 | kg | current | 同上。 |
| vehicle.mass.link.outerwing_L | 0.009 | kg | current | 同上。 |
| vehicle.mass.link.outerwing_R | 0.009 | kg | current | 同上。 |
| vehicle.mass.link.tail_base | 0.01 | kg | current | 同上。 |
| vehicle.mass.link.tailpanel_L | 0.008 | kg | current | 同上。 |
| vehicle.mass.link.tailpanel_R | 0.008 | kg | current | 同上。 |
| vehicle.inertia.link.base_link | 8.138e-05 等 三元组 | kg·m² | current | 几何法重算 2026-09-30，工具 tools/inertia_geometric_recalc.py。 |
| vehicle.inertia.link.flapconn_L | 1.454e-06 等 | kg·m² | current | 同上。 |
| vehicle.inertia.link.flapconn_R | 1.454e-06 等 | kg·m² | current | 同上。 |
| vehicle.inertia.link.innerwing_L | 3.2416e-05 等 | kg·m² | current | 同上。 |
| vehicle.inertia.link.innerwing_R | 3.2416e-05 等 | kg·m² | current | 同上。 |
| vehicle.inertia.link.outerwing_L | 1.920675e-05 等 | kg·m² | current | 同上。 |
| vehicle.inertia.link.outerwing_R | 1.920675e-05 等 | kg·m² | current | 同上。 |
| vehicle.inertia.link.tail_base | 8.0837e-06 等 | kg·m² | current | 同上。 |
| vehicle.inertia.link.tailpanel_L | 6.222e-06 等 | kg·m² | current | 同上。 |
| vehicle.inertia.link.tailpanel_R | 6.222e-06 等 | kg·m² | current | 同上。 |
| base_link.inertia.ixx | 8.138e-05 | 空单位 | current | 登记库单列键，几何法重算产物。 |
| vehicle.cg.body_frame | 负 0.0546 乘 0 乘 0 | m | current | 零位形合成，重力矩账锚点。 |
| vehicle.inertia.composite.body_frame | 0.0027、0.0042、0.0068 | kg·m² | current | 零位形整机合成，对照论文 Table I。 |

## 四、附表二：派生量与死参数表，不该迁移项，52 行

第一组为别名与派生量，7 行。

| 参数名 | 定义位置 | 类别 | 不迁理由 |
|---|---|---|---|
| _RES_CRIT | config/sim_config.py:57 | 派生量 | 由准则式 2.2 乘步频现算的中间量，数值不应入库。 |
| JOINT_DRIVE_GAINS_RESCALED | config/sim_config.py:58 | 派生量 | 由公式 K 等于 I 乘 omega_n 平方、D 约等于 2 倍根号 KI 乘 1.9 按关节惯量现算；关节惯量已在登记库 vehicle.inertia.link 族，再存数值即抄写。 |
| FLAP_FREQUENCY | config/sim_config.py:318 | 别名 | FlapKinematicsConfig.FREQUENCY 的引用副本，迁主键一处即可。 |
| FLAP_AMPLITUDE | config/sim_config.py:319 | 别名 | FlapKinematicsConfig.AMPLITUDE 的引用副本。 |
| FEATHER_AMPLITUDE | config/sim_config.py:320 | 别名 | FlapKinematicsConfig.FEATHER_AMPLITUDE 的引用副本。 |
| FLAP_PHASE_OFFSET | config/sim_config.py:321 | 别名 | FlapKinematicsConfig.PHASE_OFFSET_LR 的引用副本。 |
| AIR_DENSITY | config/sim_config.py:322 | 别名 | AeroModelConfig.RHO_AIR 的引用副本，且全库无引用点。 |

第二组为死参数，6 行。死参数判定依据为全库检索，检索范围覆盖 main_sim.py、modules、tools、perception、rl、scripts、eagle_shell、items_registry 与 config 本身。

| 参数名 | 定义位置 | 类别 | 不迁理由 |
|---|---|---|---|
| AIR_DENSITY | config/sim_config.py:108 | 死参数 | 同名定义在第 322 行被重绑定覆盖，本行数值永不生效；全库亦无 AIR_DENSITY 引用点。 |
| AIR_KINEMATIC_VISCOSITY | config/sim_config.py:109 | 死参数 | 全库无引用点，任何求解器均未读取。 |
| WINGSPAN | config/sim_config.py:145 | 死参数 | 全库无引用点；翼几何现行有效来源为 WingGeometryConfig。 |
| WING_CHORD | config/sim_config.py:147 | 死参数 | 同上。 |
| WING_AREA | config/sim_config.py:149 | 死参数 | 同上。 |
| FLIGHT_STREAM_LOG | config/sim_config.py:523 | 死参数 | 同名定义在第 964 行被重绑定覆盖，本行布尔值永不生效；两处均为布尔开关，本就不属数值迁移范围。 |

第三组为布尔开关与字符串选择符，39 行。此类项为行为开关，非数值参数，按 T3.3 口径留驻配置层，不进入登记库。

| 参数名 | 定义位置 | 类别 | 不迁理由 |
|---|---|---|---|
| DRIVE_RESCALE_ON | config/sim_config.py:56 | 布尔开关 | 环境变量驱动的重标定总开关。 |
| WING_DRIVE_CONFIG.pitch_mode | config/sim_config.py:69 | 字符串选择符 | 俯仰模式选择，取值 flip 或 aero。 |
| AERO_MODEL | config/sim_config.py:176 | 字符串选择符 | 气动路线选择符，取值 attached_flow、dynamic_stall、sane_dickinson 族。 |
| FlapKinematicsConfig.PITCH_MODE | config/sim_config.py:197 | 字符串选择符 | 俯仰模式选择，现行 aero。 |
| WingGeometryConfig.HINGE_AXIS | config/sim_config.py:227 | 字符串选择符 | 拍动铰链轴选择，现行 Y。 |
| AttachedFlowConfig.ENABLE_WAGNER | config/sim_config.py:254 | 布尔开关 | Wagner 时域项开关，关时退化准定常。 |
| AttachedFlowConfig.ENABLE_NONCIRC | config/sim_config.py:255 | 布尔开关 | 非环量惯性力开关。 |
| DynamicStallConfig.ENABLE_VORTEX | config/sim_config.py:283 | 布尔开关 | 涡环量项开关。 |
| AeroModelConfig.ENABLE_TRANSLATIONAL | config/sim_config.py:310 | 布尔开关 | 平动力开关。 |
| AeroModelConfig.ENABLE_ROTATIONAL | config/sim_config.py:311 | 布尔开关 | 旋转力开关。 |
| AeroModelConfig.ENABLE_ADDED_MASS | config/sim_config.py:312 | 布尔开关 | 附加质量力开关。 |
| TailAeroConfig.VFIN_AERO_ON | config/sim_config.py:408 | 布尔开关 | 固定垂尾气动开关，默认关。 |
| ENABLE_FUSELAGE_DRAG | config/sim_config.py:415 | 布尔开关 | 机身阻力开关。 |
| ENABLE_TAIL_SLIPSTREAM | config/sim_config.py:430 | 布尔开关 | 拍动滑流开关。 |
| MANUAL_INPUT_ENABLED | config/sim_config.py:445 | 布尔开关 | 手动输入总开关。 |
| DP_HEIGHT_HOLD | config/sim_config.py:478 | 布尔开关 | 定高通路开关。 |
| EXPERIMENT_MODE | config/sim_config.py:533 | 字符串选择符 | 试验模式选择符，空串为关闭。 |
| BENCH_FREE | config/sim_config.py:553 | 字符串元组 | 台架释放轴选择，取值为轴名子集，由试验模式自动覆盖。 |
| FlightControllerConfig.TECS_VFLOOR_ON | config/sim_config.py:672 | 布尔开关 | 低速防能量耗散保护开关。 |
| FlightControllerConfig.WING_MIX_ON | config/sim_config.py:710 | 布尔开关 | 偏航抵消组合通道开关。 |
| FlightControllerConfig.BANK_TEST_ON | config/sim_config.py:715 | 布尔开关 | 定坡度盘旋试验开关。 |
| FlightControllerConfig.YAW_FF_ADAPT | config/sim_config.py:718 | 布尔开关 | 偏航前馈慢自校准开关。 |
| FlightControllerConfig.BANK_FREQ_FREEZE | config/sim_config.py:722 | 布尔开关 | 盘旋段拍频冻结开关。 |
| FlightControllerConfig.YAW_FF_TURN | config/sim_config.py:727 | 布尔开关 | 协调转弯率前馈开关。 |
| FlightControllerConfig.BANK_PITCH_HOLD_ON | config/sim_config.py:749 | 布尔开关 | 坡度段俯仰保持开关。 |
| FlightControllerConfig.DA_SCHED_3D_ON | config/sim_config.py:753 | 布尔开关 | 翼差动三维效能映射开关。 |
| FlightControllerConfig.ATT_QUAT_ERR | config/sim_config.py:774 | 布尔开关 | 姿态误差四元数化开关。 |
| FlightControllerConfig.MULTISINE_ON | config/sim_config.py:782 | 布尔开关 | 多正弦激励模式开关。 |
| FlightControllerConfig.ROLL_DBG | config/sim_config.py:808 | 布尔开关 | 滚转环指令链探针开关。 |
| FlightControllerConfig.DM_COEXIST_ON | config/sim_config.py:809 | 布尔开关 | 幅值与密度分幅共存开关。 |
| FlightControllerConfig.DUTY_PITCH_GATE_ON | config/sim_config.py:810 | 布尔开关 | 密度通道俯仰门控开关。 |
| FlightControllerConfig.STROKE_DIFF_ON | config/sim_config.py:819 | 布尔开关 | 逐拍差动开关。 |
| FlightControllerConfig.DUTY_MOD_ON | config/sim_config.py:829 | 布尔开关 | 占空比连续调制开关。 |
| FlightControllerConfig.FREQ_DIFF_ON | config/sim_config.py:836 | 布尔开关 | 频率差动偏航通道开关。 |
| FlightControllerConfig.FD_ROLL_SOURCE | config/sim_config.py:840 | 布尔开关 | 频率差动滚转源档开关。 |
| FlightControllerConfig.LAT_CHI_CASCADE | config/sim_config.py:854 | 布尔开关 | 横向级联开关。 |
| FlightControllerConfig.YAW_HOLD | config/sim_config.py:856 | 布尔开关 | 航向保持开关。 |
| FlightControllerConfig.TECS_EB_TO_VZ | config/sim_config.py:859 | 布尔开关 | 能量分配通道结构开关。 |
| FLIGHT_CONTROLLER_ENABLED | config/sim_config.py:919 | 布尔开关 | 飞控总开关。 |

## 五、附表三：来源待考表，35 行

本表逐行列出注释与文档无法给出取值依据、依据互相冲突、或依据与现行值不一致的参数。每行给出待考原因。此类参数仍列入主表并给出建议键名，迁移前宜逐条裁定。

| 序号 | 参数名 | 定义位置 | 待考原因 |
|---|---|---|---|
| 1 | AeroModelConfig.C_ROT | config/sim_config.py:299 | 行注释仅标经验值；类文档串的 Sane 与 Dickinson 2002 年口径只覆盖极曲线拟合常数，未覆盖旋转力系数。 |
| 2 | AeroModelConfig.ADDED_MASS_COEFF | config/sim_config.py:302 | 行注释标经验域 0.5 至 1.0，未给具体文献或标定记录。 |
| 3 | TailAeroConfig.PANEL_Y | config/sim_config.py:399 | 行注释仅标差动滚转力臂用途，未给几何来源或取值依据。 |
| 4 | SLIP_DISK_AREA | config/sim_config.py:431 | 行注释的面积推导链采用 R 等于 1.15 m，属已退役的 2.3 m 机型口径，与现行 0.32 m 半展几何冲突。 |
| 5 | FlapKinematicsConfig.FREQUENCY | config/sim_config.py:191 | 注释称等于 f*，但库内另一批注释载 f* 等于 1.55 而 FREQUENCY、FREQ_HOVER、DP_FREQ_NOMINAL 均为 1.8，配平频率口径分裂。 |
| 6 | FlapKinematicsConfig.AMPLITUDE | config/sim_config.py:193 | 行注释仅存 700 g 口径的 50 度与 35 度历史，现行 17.73 度的取值依据未载。 |
| 7 | FlapKinematicsConfig.ALPHA_SET | config/sim_config.py:207 | 类注释载 215 g 重搜索四参数为 40 度、0.25、负 0.02、0，与现行 32.3169 度不一致，现行值来源未载。 |
| 8 | FlapKinematicsConfig.SPLIT | config/sim_config.py:209 | 同第 7 行，注释值 0.25 对现行 0.176。 |
| 9 | FlapKinematicsConfig.PITCH_LAG_S | config/sim_config.py:210 | 同第 7 行，注释值负 0.02 对现行负 0.0674。 |
| 10 | FlapKinematicsConfig.MEAN | config/sim_config.py:211 | 同第 7 行，注释值 0 对现行 19.8105。 |
| 11 | FlapKinematicsConfig.FEATHER_AMPLITUDE | config/sim_config.py:195 | flip 模式参数，现行 PITCH_MODE 为 aero 不启用，40 度取值依据未载。 |
| 12 | FlightControllerConfig.KP_A | config/sim_config.py:590 | 注释载 0.7、2.4、6.0 各段回退史，现值 2.6 的直接整定记录未载。 |
| 13 | FlightControllerConfig.KI_A | config/sim_config.py:591 | 注释称 0.1 为唯一既消静差又不坠毁的取值，现值 0.15 与注释结论不一致。 |
| 14 | FlightControllerConfig.KP_Z | config/sim_config.py:612 | 行注释仅标通道语义，未给取值依据；库内文档仅讨论该通道结构。 |
| 15 | FlightControllerConfig.CRUISE_VX | config/sim_config.py:647 | 注释引配平速度 4.5 出自 2026-09-21 重新标定，现值 3.95 与注释值不一致。 |
| 16 | FlightControllerConfig.PITCH_TRIM | config/sim_config.py:894 | 注释引配平俯仰负 0.05，现值负 0.143246 与注释值不一致，两者相差约两倍。 |
| 17 | FlightControllerConfig.TECS_EB_V_GAIN | config/sim_config.py:860 | 行注释仅标通道语义，第十一班整定记录未附证据文件。 |
| 18 | FlightControllerConfig.TECS_VZ_P | config/sim_config.py:861 | 同第 17 行。 |
| 19 | FlightControllerConfig.TECS_VZ_I | config/sim_config.py:862 | 同第 17 行。 |
| 20 | FlightControllerConfig.SD_KP | config/sim_config.py:820 | 行注释仅标通道语义，8.0 取值依据未载。 |
| 21 | FlightControllerConfig.SD_KD | config/sim_config.py:821 | 同第 20 行，1.5 取值依据未载。 |
| 22 | WING_DRIVE_CONFIG.frequency | config/sim_config.py:67 | 注释称与 FlapKinematicsConfig 同步修改，现行 2.5 对 FREQUENCY 的 1.8，两处不一致。 |
| 23 | WING_DRIVE_CONFIG.alpha_set_deg | config/sim_config.py:70 | 现行 12.0 度对 ALPHA_SET 的 32.3169 度，两处口径不一致且无裁决记录。 |
| 24 | FlightControllerConfig.MULTISINE_FREQS | config/sim_config.py:783 | 频率网格取值未附依据，注释仅标 Hz 网格。 |
| 25 | MANUAL_ELEV_GAIN | config/sim_config.py:448 | 行注释仅标满杆偏角语义，0.35 取值依据未载。 |
| 26 | MANUAL_AIL_GAIN | config/sim_config.py:449 | 同第 25 行，0.30 取值依据未载。 |
| 27 | MANUAL_RUDD_GAIN | config/sim_config.py:450 | 同第 25 行，0.40 取值依据未载。 |
| 28 | MANUAL_KEY_RATE | config/sim_config.py:451 | 行注释仅标键盘速率语义，1.5 取值依据未载。 |
| 29 | DP_KP | config/sim_config.py:480 | 取值依据未载，且注释声明该通路 2026-09-20 起在环复验前视为失效状态。 |
| 30 | DP_KD | config/sim_config.py:481 | 同第 29 行。 |
| 31 | UNIFIED_SEARCH.domain.lag | config/unified_search_config.py:23 | 原注自标 NUMERICAL 且 no evidence found，即无数值依据。 |
| 32 | UNIFIED_SEARCH.domain.speed | config/unified_search_config.py:25 | 原注自标 NUMERICAL 且 no evidence found。 |
| 33 | UNIFIED_SEARCH.hard.ixx | config/unified_search_config.py:12 | 原注来源仅为配置注释口径；0.0024 与登记库 vehicle.inertia.composite.body_frame 的 0.0027 不一致，两者需裁定取舍。 |
| 34 | UNIFIED_SEARCH.hard.iyy | config/unified_search_config.py:13 | 原注为 run9 回归值 0.00516，与登记库几何重算合成值 0.0042 不一致，两口径需对账。 |
| 35 | UNIFIED_SEARCH.hard.izz | config/unified_search_config.py:14 | 原注自称按 iyy 类近似且未验证；0.0024 与登记库合成值 0.0068 相差近三倍。 |

## 六、对账与统计

六域参数个数：气动主域 28 行，尾翼气动域 13 行，附着流域 6 行，控制增益域 136 行，波形运动学域 16 行，激励探针域 79 行。主映射表合计 278 行。

三张附表行数：附表一 35 行，附表二 52 行，附表三 35 行。

与此前调研的四百处量级对照：语法树清点 `config/sim_config.py` 现有模块级定义 179 处、类内属性定义 248 处、合计 427 处，与此前调研的约一百六十处加约二百四十处、合计约四百处同量级。427 处的处置路径如下：主表收录 230 处定义，其中 `WING_DRIVE_CONFIG` 一处字典定义展开为六个数值行计入主表；附表二收录 51 处定义，另含该字典内一个字符串项；已迁移 1 处，即 MASS，对应登记库 vehicle.mass.total；其余 145 处属六域之外，分类为传感器五类与开关 52 处、路径与端口 19 处、环境物理与光照 12 处、障碍物 11 处、网络与可视化与记录 17 处、系留与发射与启动与风 11 处、机体盒几何与初始位姿 5 处、仿真时间步 2 处、门洞感知 7 处、仿真应用启动配置 1 处、无人艇兼容别名 7 处、字典容器本体 1 处，合计 145 处。`config/unified_search_config.py` 的 42 项数值参数全部计入主表激励探针域。

验收自查：主表行数 278 等于六域清点计数之和；主表建议登记键名 278 个经查无重复，且与登记库现有 35 键无重名；附表三 35 行均已同步在主表来源栏标注待考字样。
