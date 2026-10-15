# -*- coding: utf-8 -*-
"""i130_close.py — I-130 销项：判据核验入册并关闭。"""
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"G:\Playground\isaac_flapper_sim\items_registry")
import ledger

criterion_check = [
    {"判据": "纵向级联五区布局（核心/作动与结构/气动与力律/飞行数据/控制，级联轨道五刻度对应）",
     "结果": "过", "数值出处": "云端截图 logs/i130_check/shot1-3 与 cloud_shot1；zones=5；判据核验表见 docs/I-130_参数实验室页面阶段一_20261008.md 第四节"},
    {"判据": "核心区三维展示区播放固定转台序列",
     "结果": "过", "数值出处": "24 帧 6fps 循环 4 秒，assets/lab/manifest.json frames=24 fps=6.0 duration_s=4.0；渲染产物 assets/lab/frame_00-23.webp"},
    {"判据": "机型选项卡含七百克锁定态",
     "结果": "过", "数值出处": "final_checks.js 实测 700g-tab disabled=true 带下一形态提示；payload.samples[1].locked=true"},
    {"判据": "教学层四块全文默认展开",
     "结果": "过", "数值出处": "shot4_drawer.png 完整定义/它影响什么/改大了会怎样/历史取值四块全展开，无折叠控件；抽屉正文 15px 行高 25.5px"},
    {"判据": "页面全部数值实时来自登记库只读接口",
     "结果": "过", "数值出处": "/api/lab/params 本地直读 params_registry、云端读镜像表（上行通道摘要对账往返一致：315 键 5 图节点）；api_lab.py 与 lp_board/server.py 数据纪律注释"},
    {"判据": "前端源码无硬编码参数值",
     "结果": "过", "数值出处": "产物 dist/assets/index-*.js 检索已知参数值（0.215/1.225 等）零命中"},
    {"判据": "四稿第九章阶段一相关判据逐条核验通过（第一至十五条与第十七条，第十六条属阶段二）",
     "结果": "过", "数值出处": "docs/I-130_参数实验室页面阶段一_20261008.md 第四节核验表逐条数值；首屏云端实测 1055 毫秒；usd/urdf 网络请求 0"},
]

owner_note = (
    "做了什么：给参数实验室页面做完阶段一陈列表示。看板顶栏新增入口，进入后是一列纵向级联的五个区，"
    "最上面核心区中央转着整机三维模型（从 fwav_15dof 离线导出的二十四帧转台，四秒一循环），"
    "两侧环绕底层参数卡；往下依次是作动与结构、气动与力律、飞行数据、控制四区，三百一十五个参数全部上墙，"
    "每张卡带状态灯和一句话解释，点开抽屉是教学层四块全文：完整定义、它影响什么、改大了会怎样、历史取值与理由。"
    "七百克选项卡按设计保持锁定，悬停能看到下一形态的完整工作量叙事。"
    "结果是什么：四稿第九章阶段一相关判据逐条核验全部通过，核验数值都留在交付文档第四节；"
    "页面在云端站点直接可用，手机壳加载同一站点同样生效。"
    "为什么是这个结果：施工中发现并修好一处结构性数据缺陷——云端参数镜像表是扁平七列，"
    "存不下 I-127 的图结构字段，快照下行每次都把本地盘点数据冲掉；本次加了完整镜像列和摘要对账的上行通道，"
    "本地与云端往返逐字节一致，这病根除了，以后盘点数据不会再丢。"
    "下一步是：指挥部盘点落库后，暂定定级自动换成正式级别；教学文本逐参数补齐；"
    "阶段二沙盘与传导对比等本页评审通过后另行立项。"
)

evidence = [
    "docs/I-130_参数实验室页面阶段一_20261008.md",
    "assets/lab/export_turntable.py assets/lab/postprocess_turntable.py assets/lab/manifest.json",
    "lp_board/lab_payload.py cloud_board/server/api_lab.py lp_board/server.py /api/lab/params 路由",
    "cloud_board/server/db.py repo.py api_agent.py params extra 镜像列与 /api/agent/params 上行通道；lp_sync/sync_agent.py 摘要对账",
    "board_ui/src/lab/LabPage.tsx lab.css types.ts deeplink.ts App.tsx 顶栏跳转控件",
    "logs/i130_check/shot1-4 与 cloud_shot1 截图、计算样式判据输出、final_checks.js 顶栏跳转/锁定态/单路由/首屏 1055ms 实测",
]

r = ledger.close("I-130", evidence, closer="L5队", conclusion="判据通过",
                 criterion_check=criterion_check, owner_note=owner_note)
print("close result:", r)
