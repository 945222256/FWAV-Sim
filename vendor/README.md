# vendor/ — 论文配套仿真资源与外部框架副本

随精读计划（`C:\Users\Anni\ZCodeProject\文献\00_精读准备.md`）引入的两份官方配套资源，
服务于本仓库 `modules/aero_solver.py`（非定常气动，待写）和传感器建模。
外部框架副本按定点跟踪纪律入位：本地全量副本，主库只记 gitlink 钉提交号。

## eko/ — Eko 智能体工作流框架（FellouAI，I-150 入位）

- 上游：https://github.com/FellouAI/eko ，钉定标签 **v4.1.0**（2025-12-29），
  提交 `a2a9cba581e07c6ad86ccab8f80061047a17edac`，许可证麻省理工许可证（版权 2024 FellouAI，
  再发布义务是保留版权声明，副本内 LICENSE 原样在位）
- 结构：TypeScript 单仓四包（eko-core 纯编排核、eko-nodejs 浏览器智能体绑定、
  eko-web、eko-extension）。**对本仓库的用法**：只用 eko-core，工作流生成与人工干预
  接口映射宿主服务操作与控制台动作，接口评估见 `docs/eko_接口评估报告_20261011.md`；
  eko-nodejs 依赖 playwright 浏览器栈，本项目不引入
- 版本出处登记：参数登记库 `eko.copy.version` 与 `eko.copy.path` 两键
- 上游对齐纪律：按自有智能体设施路线图第四期，定点跟踪分支每季度对齐一次，
  能引包不分叉，分叉须登记理由

## flappy_hummingbird/ — 必读2 官方仿真（Purdue, 2019）

- 论文：*Flappy Hummingbird: An Open Source Dynamic Simulation of Flapping Wing Robots and Animals*（arXiv:1902.09628）
- 上游：https://github.com/purdue-biorobotics/flappy （master, 2020-02 最后更新）
- 结构：Python（Gym 接口 + PID/DDPG 基线）+ **C++ 气动核心**（`flappy/envs/Wing.cpp` + SWIG 包装，基于 DART/pydart2 物理引擎）
- **对本仓库的用法**：
  - `flappy/envs/Wing.cpp/.hpp` = 准定常叶素气动模型参考实现（扑动角 ψ / 偏航角 φ / 旋转角 θ 三自由度翼运动学，C_N/C_T 力系数，参数含 R_w、d_s、空气密度、平均弦长等）→ 移植目标：`modules/aero_solver.py`
  - `flappy/envs/fwmav/simulation.py`：力映射测量 / 系统辨识（遗传算法拟合）流程参照
  - 参数拟合→单周期验证→随机化的方法论见论文 §III（文献夹必读2 MD）
- ⚠️ 直接运行需要 DART 物理引擎（pydart2），Linux (Ubuntu apt 装 libdart-dev) 顺畅，Windows 编译麻烦——**建议只移植数学，不跑原环境**

## fwaf_vid/ — SparrowHawk 同作者数据集（蒋骥舟/徐文福组, RA-L 2025）

- 论文：*FWAF-VID: A Flapping-Wing Aggressive Flight Benchmark Dataset for Visual-Inertial Localization*（RA-L 10(6), 2025；SparrowHawk TII 论文的参考文献[32]）
- 上游：https://github.com/JizhouJiang-HIT/FWAF-VID （稀疏检出，只留 calibr / sensor configuration / script / readme）
- 采集平台：HIT-Hawk 1.80 m / 770 g（与 SparrowHawk 同门平台）；28 段序列（室外激进飞行 12 + 室内 5 + 静态扑翼 4 + 标定 6）
- **对本仓库的用法**：
  - `calibr/*.yaml`：RealSense D435 + CUAV V5+ IMU 的**真实标定参数**（内参/外参/噪声特性）→ 直接喂 Isaac Sim 传感器噪声模型
  - `sensor configuration/`：全套驱动/SDK 源码包（librealsense、mavros、u-blox RTK、Linktrack UWB）127M，参考用
  - `script/`：bag2tum / GT 提取 / 卡尔曼平滑脚本
- 完整 rosbag 数据（每段 >1GB）不在仓库，在夸克网盘：`https://pan.quark.cn/s/6ed823d41de0`（按需下载，勿全拉）

## 其余两篇（Sci China 2023 / CJA 2022）

无公开配套代码（GitHub 检索确认）。建模/控制细节以论文正文为准（文献夹 MD）。
