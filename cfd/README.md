# 计算流体力学线工作目录（L9 线，I-119 起建）

本目录是计算流体力学线的独占领地。求解器为 OpenFOAM 二五零六版，
运行环境在 Windows 的 Linux 子系统（Ubuntu 24.04）内，选型理由见
docs/选型论证_求解器.md。

## 目录布局

- tools/：自研工具。og_mesh_gen.py 生成二维 O 型网格的 blockMeshDict
  （椭圆或端圆平板剖面，可带滑移界面圆柱区，两侧环网格周向分布
  配对）；bench1_analyze.py 与 bench2_analyze.py 是两基准的斜率
  拟合、归一化与发表值对照分析；collect_results.sh 把运行产物拷
  回本仓库。
- cases/bench1_steady/：基准一（平板法向力斜率）。template/ 是算例
  模板（定常平移扫掠攻角），run_bench1.py 是建网格、并行扫掠、收力
  的驱动器。算例目录带网格签名（域半径、径向数、周向数）防碰撞。
- cases/bench2_flapping/：基准二（Wang 2004 二维悬停扑翼复现）。
  模板含表驱动刚体转动动网格与随时间振荡的远场来流（拍动手性由
  STROKE_SIGN 选定，见基准二报告）；run_bench2.py 生成运动表与
  来流表并驱动；run_all_phi.sh 三相位角并行。
- docs/：选型论证、文献发表值登记、两基准报告、分阶段施工方案与
  算力预算。
- results/：从运行环境拷回的日志、力数据与分析产物（只读档案）。
- _literature/：基准文献原文与文本抽取。

## 长任务运行须知（Windows 的 Linux 子系统）

后台化进程随发起会话退出被回收，长任务必须以常驻会话方式挂住
（前台 wait 直到求解器退出）；经 /mnt/g 拷贝只用纯英文名。

## 运行方式（Windows 侧发起）

wsl.exe -d Ubuntu -- bash -lc 'source
/usr/lib/openfoam/openfoam2506/etc/bashrc && ...'

算例源码在本目录（唯一事实源），运行前拷入子系统家目录
~/cfd_work/，跑完把日志与力数据拷回 results/。文件名保持纯英文，
经 /mnt/g 通道拷贝时中文名会损坏。

## 基准判据口径

见 docs/文献来源与发表值.md 末节。
