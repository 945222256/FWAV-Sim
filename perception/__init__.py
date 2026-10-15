# perception — 独立感知组件（SparrowHawk TII 2026 自适应 Hough 门检测）
#
# 分层：
#   core/      算法核，纯 numpy + cv2，零 Isaac 依赖（本机/工控机/仿真机均可跑）
#   sim_adapter.py  Isaac 侧适配（D435 标定注入、GT 投影、光照预设）
#   live.py    UDP 检测服务（仿真在线 / 数据集离线回放两种模式）
#   eval/      基线对比与 Table II 复刻
#
# 论文出处：Jiang et al., "A Compact Autonomous Flapping-Wing Aerial Vehicle:
# Design, Modeling, and Vision-Based Control for Narrow Gap Traversal",
# IEEE TII 22(2), 2026, §V.A Algorithm 1。
