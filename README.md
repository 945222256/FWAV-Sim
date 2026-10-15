# isaac_flapper_sim — 扑翼 (Flapping-Wing) MAV 仿真

基于 **NVIDIA Isaac Sim 5.1.0** 的扑翼微型飞行器 (MAV) 仿真骨架。
从 `isaac_usv_sim`（无人艇水动力仿真）迁移平台层而来，剥离水动力，保留传感器/控制/可视化架构。

> **被控样机（2026-09-15 确定选用）**：徐文福组提供的 `fwav_15dof`（浮动基座6+驱动关节9，
> REP-103 x前，约 430g）→ `assets/flapper/fwav_15dof_from_group.urdf`。
> **已导入 Isaac**：`tools/import_fwav_urdf.py` 转 `fwav_15dof.usd`（浮动基座+POSITION驱动，
> K=30/D=3 由 flapper_loader 写入），main_sim 加载为 Articulation 并用 WingKinematics
> 驱动 9 关节拍动（实测 82fps，关节动作已截图验证）。
> 拍动预览（pybullet 轻量版）：`python tools/preview_fwav_15dof.py [频率Hz] [时长s]`。
> 结构对比图：`docs/compare_15dof.png`。我方早期 15 全驱动版留作生成器工具，已弃用。
> 已知坑：kit 会吞 python logger 输出（print+flush 可见）；URDF Importer 的 dest_path
> 直存会写空壳，必须"内存导入→stage.Export"。

> ⚠️ **当前状态**：骨架已搭好，可启动 Isaac Sim 跑通主循环（占位胶囊体+双翼）。
> 气动力学 `modules/aero_solver.py` **已实现并通过单元测试**（Sane & Dickinson 2002 三分量
> 准定常 + 三流型架构，见 `docs/AERO_MODEL.md`）；真实扑翼 USD 资产与翼面 Articulation 尚未接入。

---

## 快速开始

```bash
# 用 Isaac Sim 自带 Python 启动
g:\Playground\isaac_sim_5.1.0\python.bat main_sim.py
```

启动后会看到：地面平面 + HDRI 天空 + 太阳光 + 占位扑翼机构（机身+左右翼）+ 柱状障碍。

---

## ROS2 桥接循迹联调（bishop_planner ↔ Isaac）

WSL 里的 bishop_planner 负责避障轨迹生成（30Hz pos_cmd），本仿真当被控对象做循迹跟踪。
桥接走 UDP（实测双向通）：桥脚本在 `bishop_planner/launch/isaac_bridge.py`。

```bash
# 1. [Windows] 先起 Isaac 仿真（等 GUI 出来）
g:\Playground\isaac_sim_5.1.0\python.bat main_sim.py

# 2. [WSL] 起 bishop 规划 + 桥（SO3 四旋翼仿真器不启动，被控对象 = Isaac）
source /opt/ros/jazzy/setup.bash
source ~/ego-planner-swarm-ros2/install/setup.bash
source ~/xtnav_hpma/install/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ros2 launch ~/xtnav_hpma/src/bishop_planner/launch/run_with_isaac.launch.py

# 3. [WSL] 发目标点，开始循迹
ros2 topic pub -1 /move_base_simple/goal geometry_msgs/msg/PoseStamped \
  '{header: {frame_id: world}, pose: {position: {x: 15.0, y: 10.0, z: 1.2}}}'
```

数据流：pos_cmd→UDP:9002→`modules/flapper_controller.py`，按 `config.TRACKING_MODE` 分流——
`kinematic`（默认，供联调使用）：位置P环+速度前馈直写占位刚体速度；`aero`：高度送入 aero_solver 的
HoverPD（速度直写会覆盖气动积分，两种模式严禁混用）。水平位置环+TECS+串级PID 为后续增量。
Isaac 位姿→UDP:9001→桥→odom 话题回喂规划器（对端地址自动学习，WSL IP 漂移无需配置）。
误差统计：终端 `[TRACK]` 行 + `logs/track_error.csv`。桥每 5s 打印收发统计。

---

## 目录结构

```
isaac_flapper_sim/
├── main_sim.py                 # 主入口：启动 SimulationApp → World → 主循环
├── config/
│   └── sim_config.py           # 全局参数（路径/物理/扑翼气动/传感器/障碍/网络）
├── modules/
│   ├── env_builder.py          # 🟡 改写：地面+光照+边界墙（去水）
│   ├── flapper_loader.py       # 🟡 改写：扑翼加载（原 usv_loader，去 hydro）
│   ├── sensor_setup.py         # 🟢 复用：立体相机/鱼眼/LiDAR/HUD
│   ├── obstacle_manager.py     # 🟢 复用：柱状障碍程序化生成
│   ├── usv_network.py          # 🟢 复用：UDP JSON 收发
│   ├── telemetry_udp.py        # 🟢 复用：遥测
│   ├── hud_pipeline.py         # 🟢 复用：HUD 可视化
│   ├── trajectory_viz.py       # 🟢 复用：轨迹带
│   ├── lidar_diag.py           # 🟢 复用：LiDAR 诊断
│   ├── wake_particles.py       # 🟢 复用：粒子系统（可改作涡可视化）
│   ├── bev/                    # 🟢 复用：环视 BEV 拼接整包
│   ├── flapper_controller.py   # 🟢 新增：桥接循迹（TRACKING_MODE 分流）
│   └── aero_solver.py          # ✅ 已实现：S&D 2002 准定常三流型 + HoverPD（docs/AERO_MODEL.md）
├── assets/
│   ├── flapper/                # 扑翼 USD 资产（待放入）
│   ├── forest/                 # 森林场景 USD（待放入）
│   ├── obstacles/              # （空，障碍程序化生成）
│   └── hdri/                   # ✅ 已有 4K 天空 EXR
├── data/                       # 传感器数据输出
├── logs/                       # 运行日志
├── vendor/                     # 📦 论文配套资源（见 vendor/README.md）
│   ├── flappy_hummingbird/     #   Purdue 官方仿真（Wing.cpp 准定常气动 = aero_solver 参考实现）
│   └── fwaf_vid/               #   SparrowHawk 同作者数据集（D435/IMU 真实标定 YAML）
└── docs/
    ├── ASSET_SOURCES.md        # 资产来源清单（扑翼/森林/柱状）
    └── MIGRATION_NOTES.md      # 从 USV 迁移的详细笔记
```

---

## 从 USV 项目复用了什么

| 类别 | 内容 |
|---|---|
| 🟢 **原样复用** | sensor_setup / obstacle_manager / usv_network / telemetry / hud / trajectory / lidar_diag / wake_particles / bev 整包 |
| 🟡 **改写** | env_builder（去水→地面）、flapper_loader（去 hydro→aero）、sim_config、main_sim |
| ✅ **已实现** | aero_solver（S&D 2002 准定常 + 三流型 + HoverPD，单测通过 2026-07-21） |
| 🔴 **待新写** | 翼面 Articulation、TECS+串级PID 前飞循迹（SparrowHawk V.B）、6-DOF 精细模型 |

详见 [`docs/MIGRATION_NOTES.md`](docs/MIGRATION_NOTES.md)。

---

## 关键架构特点（继承自 USV 项目）

1. **物理与控制解耦**：控制器独立进程，UDP（9001 遥测 / 9002 指令）通信，控制器崩溃不影响物理引擎
2. **零 Fabric 锁**：`RigidPrim` 张量视图直写 PhysX GPU 缓冲区
3. **Timeline 无限时长**：强制 `endTimeCode=1e8`，避免默认 8 秒后物理冻结
4. **传感器异步渲染**：物理 120Hz / 渲染 30Hz，`render_interval` 子步进
5. **永久 play() 守护**：自动恢复 Timeline 偶发的 `is_playing=False`

---

## 下一步开发优先级

1. **实现 `modules/aero_solver.py`** — DeLaurier / BEMT 非定常气动力（参考 `vendor/flappy_hummingbird/flappy/envs/Wing.cpp` 的准定常叶素实现与参数辨识流程）
2. **获取扑翼 USD** — 见 [`docs/ASSET_SOURCES.md`](docs/ASSET_SOURCES.md)（RCmags/ornithopter-hover）
3. **翼面 Articulation 配置** — 拍动铰链 Joints + PD 控制
4. **写 `flapper_controller.py`** — 姿态/位置/拍动相位控制
5. **接入森林场景** — joevento 程序化森林生成器

---

## 运行依赖

- NVIDIA Isaac Sim **5.1.0**（`g:\Playground\isaac_sim_5.1.0`）
- Python 3.10（Isaac Sim 自带）
- NumPy（Isaac Sim 自带）
- 无需额外安装 —— 所有依赖随 Isaac Sim 提供

---

## 手动飞行 + 在线观测（2026-09-16）

**手动输入**（`modules/manual_input.py`，零依赖 ctypes）：摇杆自动识别（winmm，
左杆油门/方向、右杆升降/副翼），无摇杆退化为键盘（W/S 油门保持，↑↓ 升降、
←→ 副翼、Q/E 方向，松开回中）。映射按课题组真机逻辑（CJA2022）：
油门→拍频 1.5–3.5Hz，升降→尾同向，副翼→尾差动，方向→方向舵偏转；舵偏同步送入
`TailFuselageAero` 产生真实气动力矩。

**附加气动**（`aero_solver.TailFuselageAero`）：尾翼平板（同向俯仰/差动滚转/
方向舵偏航，均经 0.45m 力臂出力矩）+ 机身阻力（`ENABLE_FUSELAGE_DRAG` 开关）。

**在线观测**（均为 UDP 本机流，窗随开随关）：
- `python tools/joint_scope.py` — 9 关节波形（实线实际/虚线目标，:9010）
- `python tools/ctrl_scope.py` — 手动行程（上：四通道杆量，下：拍频/尾舵，:9011）

**质量统一**：加载时 base_link 质量自动补差到 `cfg.MASS`（700g），其余连杆保留 URDF 值。
**气动标定**：700g/2.3m 级（`python modules/aero_solver.py` 测试4 验收，升力/体重 1.17x）。
