# perception/ — 独立门感知组件

SparrowHawk（Jiang et al., *IEEE TII* 22(2), 2026）§V.A "自适应 Hough 门检测"
的复刻与工程化。**目标：无先验、免预训练、CPU 实时的圆形穿越门检测**，
输出论文式(18)的归一化像素误差，直接可供视觉伺服控制消费。

## 目录

```
perception/
├── core/                  算法核（纯 numpy+cv2，零 Isaac 依赖）
│   ├── detector.py        AdaptiveHoughDetector（Algorithm 1 参数级复刻）
│   ├── metrics.py         CPE RMSE / 有效率 / 时延（Table II 口径）
│   └── （evaluator/buffer 逻辑内联在 detector.py）
├── sim_adapter.py         Isaac 适配：D435 标定注入/GT 投影/光照预设/传感器退化
├── live.py                UDP 检测服务（在线随行 + 数据集回放两种模式）
├── eval/
│   ├── baselines.py       朴素 Hough 基线（论文 Table II 传统对照）
│   └── run_eval.py        数据集评测 → Table II 风格报告
└── tests/
    └── test_detector_synthetic.py   合成图自测（三工况 + 式18 sanity）

modules/gate_manager.py    圆门 USD 程序化生成（黑色剪影环，80cm 孔）
tools/gate_dataset.py      独立采集器（脚本化接近轨迹 + 同帧 GT，headless 可跑）
config/sim_config.py §8f   开关与几何/端口（ENABLE_GATE_PERCEPTION，默认关）
main_sim.py 5.11b          随行挂载钩子（相机骑扑动机构收真实振动退化）
```

## 快速开始

```bash
# 1. 合成自测（系统 Python 即可，需 numpy+opencv）
python perception/tests/test_detector_synthetic.py

# 2. 仿真实采（Isaac Python；clean | clutter | lowlight）
g:/Playground/isaac_sim_5.1.0/python.bat tools/gate_dataset.py \
    --scenario clean --frames 40 --headless --out data/gate_clean

# 3. 评测（Table II 口径：adaptive vs plain_hough，可选 --yolo-weights）
python perception/eval/run_eval.py --dataset data/gate_clean

# 4. 回放联调（控制组无需 Isaac：检测流发 udp:127.0.0.1:9012）
python -m perception.live --dataset data/gate_clean
```

在线模式：`config.sim_config.ENABLE_GATE_PERCEPTION = True` 后跑 main_sim，
自动生成门、左目相机换 D435 标定、随行采集到 `data/ridealong/`、
检测流 JSON 发 `PERCEPTION_UDP_PORT`（默认 9012）：

```json
{"type":"gate","t":…,"valid":true,"u":319.0,"v":239.0,"r":35.0,"fit":0.95,
 "e_u":-0.0016,"e_v":-0.0021,"e_d":0.874,"latency_ms":2.3}
```

## 论文给定参数（原值保留）vs 本实现选定（论文未给）

| 项 | 值 | 来源 |
|---|---|---|
| param1 / param2₀ | 30 / 55 | 论文 §V.A 原文 |
| fitDegree / minFitDegree / maxFitNum | 0.8 / 0.5 / 3 | 论文 §V.A 原文 |
| period / capacity | 3 / 7 | 论文 §V.A 原文 |
| param2 比例律 | `clip(55·r/r̄, 18, 110)`，r̄=工作高度/6 | **自选**：累加器票数 ∝ 周长 ∝ r |
| ROI 掩码 | Canny 边缘膨胀 2px | **自选**：匹配容差带 |
| 滑窗聚合 | 缓冲中位数 | **自选**：论文只说"stabilize" |
| 完整圆周归一化 | 分母=2πr 离散像素（非图内可见弧） | **自选**：防图像边缘假圆（见下） |
| 时序重排+散度门 | fit·exp(−d²/2r̄²) 重排；d>1.5r̄ 拒收；连拒 5 帧冲刷 | **自选**：防杂乱假圆（见下） |

三处自选决策都有实采证据（data/gate_* 数据集）：
- 完整圆周归一化：合成杂乱集 RMSE 87→4.8px（图像边缘的部分弧候选分母虚高）。
- 时序重排+散度门：实采 clutter 集 RMSE 76.9→**2.07px**（柱边+地平线可拼出
  fit≈1.0 的假圆，纯 fit 排序被劫持；散度门把离群候选挡在缓冲外）。
- 曾试"环对伴生边检验"（候选 1.2~1.5×r 处须有伴生边）——实采反而劣化
  （候选常锁环带中段，伴生边单侧找不到），已移除，记录在此防重蹈。

## 已探索并否决：Hough 投票的点云化改写（vote_cloud，负结果存档）

`core/vote_cloud.py` + `eval/vote_recall_eval.py` 把候选生成显式改写为
参数空间 (u,v,r) 投票点云的密度模态（高斯核 KDE + 局部极大，显著性=
圆周支持率 = 峰值/(2πr̂·k₀)，k₀ 为核稀释因子）。实采三数据集对比
（data/vote_recall_report.md，2026-09-19）：

| 工况 | 累加器召回@少候选 | 点云召回 | 点云候选数/帧 | 点云耗时 |
|---|---|---|---|---|
| clean | **0.95** @1.0 | 0.84 | 8.0 | 11.3ms（累加器 2.2ms） |
| clutter | **0.92** @1.0 | 0.87 | 8.0 | 18.1ms |
| lowlight | **0.72** @0.8 (p2=20) | 0.38 | 8.0 | 8.2ms |

端到端（同评估器/滑窗，仅换候选源）：点云版 clean RMSE 121.6px vs
累加器 1.67px。**结论：否决**。两条结构性原因：①支持率按 2πr 归一
天然偏向小圆（r=5 只需 31 个支持像素，纹理噪声轻松凑满），恰是
param2∝r 问题的对偶——要修就得再加半径惩罚，等于绕回自适应 param2；
②累加器的 minDist 抑制是承重的（clutter 集 param2 降到 20 时召回反而
0.92→0.69，假峰会把真圆挤掉），密度模态没有这层质量筛选。点云化改写
的适用区在高维/连续参数空间（椭圆、3D 圆、任意形状 GHT）——本问题
只有 3 个自由度且分辨率需求低，固定网格累加器已近最优。
代码保留供复现，不进默认管线。

## 实测结果（RTX 实渲染帧，非合成；CPE 口径=Table II）

| 数据集 | 方法 | CPE RMSE (px) | CPE mean | 有效率 | 均值时延 |
|---|---|---|---|---|---|
| clean | **adaptive** | **1.67** | 1.55 | 100% | 1.7ms |
| clean | plain Hough | 7.75 | 3.35 | 100% | 2.8ms |
| clutter | **adaptive** | **2.07** | 1.75 | 97% | 2.1ms |
| clutter | plain Hough | 6.78 | 3.22 | 97% | 2.9ms |
| lowlight | **adaptive** | 5.92 | 5.69 | **20.5%** | 2.4ms |
| lowlight | plain Hough | 4.33 | 4.33 | 2.6% | 2.4ms |

弱光工况（dome 45/sun 0 + ×0.35 增益 + σ13 噪声 + 9px 运动模糊）下覆盖率
从 100% 崩到 20%——与论文"常光 80% → 弱光 60%"的退化方向一致；该档是
难度旋钮，采集时调 `LIGHT_PRESETS` 即可。

## 传感器与标定（vendor/fwaf_vid/calibr，实测值非默认）

- D435 infra 左目：fx=fy≈386 @ 640×480（注入 Isaac Camera 时像素尺寸取
  100µm，见踩坑 3；主点按渲染真实值 (W/2,H/2)，与实测 326.3/228.9 差
  <1.5%，记录在案）
- 渲染图像自带相机骑扑动机构的振动（在线模式）；运动模糊为采集侧合成
  （渲染器 motion blur 默认关），核宽可按 FWAF-VID 真机序列标定后收紧
- 注意：这是 FWAF-VID/HIT-Hawk 平台的标定；SparrowHawk 本机为 DJI O4
  单目（论文未公开内参），"同款传感器"的说法不准确，但作为真实参数注入
  优于默认值

## Isaac Sim 5.1.0 踩坑记录（团队资产，改采集链路前必读）

1. **`Camera.get_current_frame()` 的键名是 `"rgb"`**（4 通道），旧代码/
   文档的 `"rgba"` 在 5.1.0 拿到 None——`modules/sensor_setup.py` 的
   `get_camera_data()` 现存此问题。用 `sim_adapter.frame_to_bgr()` 兼容两者。
2. **仓库 `config/` 包必须在 SimulationApp 之前 import**：kit 把
   `pip_prebundle` 路径排进 sys.path，其中 `cv2/config.py` 会把顶层
   `config` 名字遮蔽（`NameError: LOADER_DIR`）。main_sim / gate_dataset
   均已按"先 config 后 isaacsim"组织导入。
3. **亚毫米焦距/光圈触发渲染管线精度地板**：按物理 3µm 像素尺寸换算的
   f=1.16mm 会让门成像放大 ~12×（fx_eff≈4654，实测反推）。像素尺寸取
   100µm（绝对值不影响 FOV，只有 f/aperture 比值有意义）后正常。
4. **`XformCommonAPI.SetRotate(0, yaw, 0)` 在 yaw=0 时不产生旋转**——若
   网格生成本身假设了旋转，物体会保持局部朝向（我们的门因此平躺过，
   渲染成一条横带）。gate_manager 现在把朝向直接烘焙进顶点，不走父级旋转。
5. **穹顶光为主光源时漫反射亮度 ∝ albedo ≤ 天空亮度**：白环对天空的灰度
   对比天花板 ~7%；`UsdPreviewSurface.emissiveColor` 在本 RTX 实时路径不
   生效。门外观取**哑光黑剪影**（albedo 0.03，两侧 Δ≥120 灰阶）。
6. **渲染相机主点恒为像面中心**（不可配置）；GT 投影用 (W/2,H/2)。
7. **RTX 时间降噪器冷启动**：相机移动后首帧偏暗偏糊——静止位姿连渲 4 帧
   取末帧（gate_dataset 已内置）。
8. Isaac 自带 Python 的 cv2=4.11、系统 Python 亦可跑算法核（cv2 4.13）。

## 已知限制（交接清单）

- **半径锁边歧义**：检测 r 常锁环的外沿（≈1.2-1.4× 孔半径），半径误差
  ~10-15px；CPE（论文主指标）不受影响，e_d 有系统性偏差——控制器侧可
  用固定比例标定，或后续在评估器里做双沿均值。
- lowlight 覆盖率 20%：预设偏苛（有意保留难度梯度），`LIGHT_PRESETS`
  可调。
- param2 比例律 / 模糊核宽 / 噪声参数均未与 FWAF-VID 真机序列对齐
  （L2 标定是下一步，见任务规划）。
- YOLOv11 基线未装 ultralytics（`run_eval --yolo-weights` 待环境就绪）。
- 尺度错配提醒：现仿真载体 700g/2.3m 翼展，物理上穿不过 80cm 门；对
  感知评测无影响，闭环穿门集成前需切 60cm 级参数或放大门。
