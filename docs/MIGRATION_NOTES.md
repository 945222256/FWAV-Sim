# 迁移笔记：isaac_usv_sim → isaac_flapper_sim

> 完成时间：2026-07-20
> 源项目: `G:\Playground\isaac_usv_sim` (USV 水动力仿真)
> 目标项目: `G:\Playground\isaac_flapper_sim` (扑翼 MAV 仿真)

---

## 一、复用分级总表

### 🟢 A 类：原样复用（平台无关，零改动）
| 文件 | 功能 | 复用度 |
|---|---|---|
| `modules/sensor_setup.py` | 立体相机/环视鱼眼/LiDAR/HUD/topdown + CUDA 共享内存取图 | ~85%（安装位置需按 MAV 尺寸重标定） |
| `modules/obstacle_manager.py` | 程序化生成柱状障碍（已用作树干/柱子） | 100% |
| `modules/usv_network.py` | UDP JSON 收发（控制器↔仿真解耦） | 100% |
| `modules/telemetry_udp.py` | 遥测 UDP | 100% |
| `modules/hud_pipeline.py` | HUD 可视化管线 | ~90% |
| `modules/hud_subscribers.py` | HUD 调试订阅者 | 100% |
| `modules/trajectory_viz.py` / `trajectory_ribbon.py` | 轨迹带可视化 | ~90% |
| `modules/lidar_diag.py` | LiDAR 诊断 | 100% |
| `modules/wake_particles.py` | 粒子系统（可用于扑翼尾迹涡可视化） | ~70% |
| `modules/bev/*` (整包) | 环视 BEV 拼接 + IMU 姿态补偿 | ~90% |

### 🟡 B 类：改写（保留框架，去水耦合）
| 文件 | 改动 |
|---|---|
| `modules/env_builder.py` | 删水面/波浪/海底/潜艇/055；保留地形+光照+重力+边界墙；加默认地面平面 |
| `modules/flapper_loader.py`（原 usv_loader） | 删 HydroPhysicsCallback；加占位机构生成；setup_hydro→setup_aero |
| `config/sim_config.py` | 删 Fossen 水动力段；加扑翼几何/气动/拍动参数；加向后兼容别名 |
| `main_sim.py` | 删波浪/尾迹/HUD 管线耦合；保留 Timeline 守护/warmup/UDP/视口 |

### 🔴 C 类：未实现（必须新写）
| 需要新写 | 状态 | 说明 |
|---|---|---|
| ~~气动模型~~ | ✅ **三流型完成 (2026-07-21)** | `modules/aero/` 包：attached_flow(高AR附着流/Wagner) + dynamic_stall(LB受控涡) + sane_dickinson(昆虫式)，全部零 Isaac 依赖，单元测试通过。见 `docs/AERO_MODEL.md` |
| 翼面 Articulation + Joints | 🔴 待做 | 拍动铰链链（机身+左翼+右翼+尾翼），需真实扑翼 USD 资产 |
| `flapper_controller.py` | 🟡 部分 | 内嵌 `HoverPDController`（modules/aero/callback.py），后续拆独立进程 + UDP |
| 6-DOF 全自由度动力学 | ✅ 已具备 | PhysX 处理 6-DOF 刚体积分，气动力作为外力注入（与 USV 一致） |
| 柔性翼变形（可选） | 🔴 未做 | FEM / 质量-弹簧网络 |

---

## 二、关键耦合点处理记录

### 1. env_builder 的波浪依赖
原文件 import：
```python
from modules.wave_visual import create_wave_mesh, WaveVisualUpdater
from modules.wave_model import JONSWAPWaveField
from modules.wave_warp import WaveVisualUpdaterWarp   # 函数内
```
处理：**全部删除**，`build_environment` 不再返回 wave_updater。新增 `_create_ground_plane` 兜底。

### 2. usv_loader 的 hydro 依赖
```python
from modules.usv_hydro import HydroPhysicsCallback
...
def setup_hydro(self): self.hydro_callback = HydroPhysicsCallback(...)
```
处理：**删除 import 和 setup_hydro**，改为 `setup_aero()`（调用 `modules.aero_solver.create_aero_callback`，模块未实现时 try/except 跳过）。

### 3. config 向后兼容
直接复用的模块里仍引用旧符号名（`USV_PRIM_PATH`、`WATER_LEVEL`、`BUOY_*` 等）。
在 config 末尾加别名段重定向到新变量：
```python
WATER_LEVEL = GROUND_LEVEL
USV_PRIM_PATH = FLAPPER_PRIM_PATH
USV_INITIAL_POSITION = INITIAL_POSITION
...
```
→ 让照搬的模块无需改动即可 import。**后续逐步清理模块里的 USV/WATER 字眼后，此段可删。**

### 4. main_sim 主循环硬骨头（必须保留）
- ✅ `simulation_app` 必须最先启动，再 import isaacsim
- ✅ `world.reset()` 会断开物理回调订阅 → 必须 `remove_physics_callback` 再 `add`
- ✅ Timeline 默认 endTimeCode 8 秒后冻结物理 → 强制 `set_end_time(100000)` + `SetEndTimeCode(1e8)`
- ✅ Camera 必须 `world.scene.add()`，否则被 `world.step` 当垃圾回收
- ✅ `RigidPrim` 张量视图直写 PhysX（零 Fabric 锁）
- ✅ 永久 `play()` 守护：检测 `is_playing` 掉 False 自动恢复

---

## 三、API 验证记录

### 已修复的不匹配（迁移过程中发现并修正）
| 位置 | 原误写 | 实际 API（来自 usv_network / lidar_diag） |
|---|---|---|
| `main_sim._recv_commands` | `receiver.recv_nonblocking()` | `receiver.receive()` |
| `main_sim._send_telemetry` | `sender.send(bytes)` | `sender.send(dict)`（JSON） |
| `main_sim._lidar_diag` | `lidar_diag.LidarDiagnostic()` | `lidar_diag.LidarDiag()` |

### 静态验证结果
- AST 语法检查：**20 个文件全部通过**
- config 符号交叉检查：sensor_setup/obstacle_manager/env_builder **所有引用符号均已定义**
- config 独立 import：**成功**（纯 Python，无 Isaac Sim 依赖）

---

## 四、与原项目的物理差异速查

| 维度 | USV（原） | 扑翼（目标） |
|---|---|---|
| 流体介质 | 水（密度 1025 kg/m³） | 空气（1.225 kg/m³） |
| 自由度 | 3-DOF（surge/sway/yaw） | 6-DOF（全） |
| 力学模型 | Fossen 准定常 + Savitsky 升力 | 非定常（LEV/尾迹捕获/旋转增升） |
| 刚体拓扑 | 单体 | 多刚体铰接（Articulation） |
| 主要激励 | 螺旋桨推力 | 翼面拍动（20-40 Hz） |
| 环境场 | JONSWAP 波浪场 | 风场/湍流场 |
| 典型质量 | 4000 kg | 30 g |
| 典型速度 | 6-10 m/s | 1-10 m/s |

---

## 五、下一步 TODO（按优先级）

1. ~~**[最高] 实现 `modules/aero_solver.py`**~~ ✅ **已完成 (2026-07-21)**
   - Sane & Dickinson 2002 三分量模型
   - Model/Callback 分离架构，FlapperAeroModel 可独立单元测试
   - 升力/体重 1.35x（悬停合理），阻力方向正确，前飞推力 > 阻力
   - 内嵌 HoverPDController（高度+姿态 PD）
2. **[最高] 集成测试**：在 Isaac Sim 内验证扑翼从 1m 高度稳定悬停
3. **[高] 获取扑翼 USD 资产**：RCmags/ornithopter-hover → FreeCAD 导出 STEP → URDF Importer 转 USD（见 `docs/ASSET_SOURCES.md`）
4. **[高] 翼面 Articulation 配置**：拍动铰链 Joints、驱动模式、PD 增益、力矩限幅
5. **[高] 拆出 `flapper_controller.py`**：内嵌 PD → 独立进程 + UDP（仿 usv_controller）
6. **[中] 森林场景接入**：joevento 程序化森林生成器 或 Rivermark + 树木包
7. **[中] 清理 config 别名段**：逐步把模块里的 USV/WATER 字眼改成 FLAPPER/GROUND 后删除别名
