/* overview_data.ts — I-168 总览页注册数据（生成物，禁止手改）。
 * 生成器 board_ui/scripts/gen_overview_data.py 从权威文件现读产出，
 * 构建前自动运行（package.json prebuild）。摘录逐字出自原文并过
 * 语言检查禁用词自检。生成时刻 2026-10-14T09:35:37。 */
export type OvSource = {
  kind: string;
  locus: string;
  excerpt: string;
  sampled_at: string;
};

export type OvMission = { mission: string; source: OvSource };

export type OvSelf = { id: string; text: string; source: OvSource };
export type OvMemory = { id: string; label: string; text: string; source: OvSource };
export type OvDialog = { id: string; who: string; session: string; era: string;
  note: string; source: OvSource };

export const OVERVIEW_GENERATED_AT = "2026-10-14T09:35:37";

export const LANE_MISSIONS: Record<string, OvMission> = {
 "HQ": {
  "mission": "计划、规则、裁决、派发与目标管理",
  "source": {
   "kind": "文件",
   "locus": "items_registry/lanes.json#HQ",
   "excerpt": "计划、规则、裁决、派发与目标管理",
   "sampled_at": "2026-10-14T09:35:37"
  }
 },
 "L1": {
  "mission": "辨识产线、补偿器、科目一",
  "source": {
   "kind": "文件",
   "locus": "items_registry/lanes.json#L1",
   "excerpt": "辨识产线、补偿器、科目一",
   "sampled_at": "2026-10-14T09:35:37"
  }
 },
 "L2": {
  "mission": "主程序拆分、开关收编、实验管线、基准库",
  "source": {
   "kind": "文件",
   "locus": "items_registry/lanes.json#L2",
   "excerpt": "主程序拆分、开关收编、实验管线、基准库",
   "sampled_at": "2026-10-14T09:35:37"
  }
 },
 "L3": {
  "mission": "参数迁移、口径销账、代理实物",
  "source": {
   "kind": "文件",
   "locus": "items_registry/lanes.json#L3",
   "excerpt": "参数迁移、口径销账、代理实物",
   "sampled_at": "2026-10-14T09:35:37"
  }
 },
 "L4": {
  "mission": "周期时变辨识、谐波域补偿器、混合飞行规格（定义细化归 I-049）",
  "source": {
   "kind": "文件",
   "locus": "items_registry/lanes.json#L4",
   "excerpt": "周期时变辨识、谐波域补偿器、混合飞行规格（定义细化归 I-049）",
   "sampled_at": "2026-10-14T09:35:37"
  }
 },
 "L5": {
  "mission": "安卓薄壳、看板前端与云端接口的应用层接驳（机主 2026-10-03 授线）",
  "source": {
   "kind": "文件",
   "locus": "items_registry/lanes.json#L5",
   "excerpt": "安卓薄壳、看板前端与云端接口的应用层接驳（机主 2026-10-03 授线）",
   "sampled_at": "2026-10-14T09:35:37"
  }
 },
 "L6": {
  "mission": "生成式策略与强化学习微调、逆映射部署（rl 目录自 L1 划出，协作架构第二百七十八行在案；I-092 修复时按在案证据复原）",
  "source": {
   "kind": "文件",
   "locus": "items_registry/lanes.json#L6",
   "excerpt": "生成式策略与强化学习微调、逆映射部署（rl 目录自 L1 划出，协作架构第二百七十八行在案；I-092 修复时按在案证据复原）",
   "sampled_at": "2026-10-14T09:35:37"
  }
 },
 "L7": {
  "mission": "bishop_nav_core 自动同步与规划算法合龙准备，七队常驻岗位（定义出处事项 I-091 机主说明；I-092 修复时按在案证据复原）",
  "source": {
   "kind": "文件",
   "locus": "items_registry/lanes.json#L7",
   "excerpt": "bishop_nav_core 自动同步与规划算法合龙准备，七队常驻岗位（定义出处事项 I-091 机主说明；I-092 修复时按在案证据复原）",
   "sampled_at": "2026-10-14T09:35:37"
  }
 },
 "L8": {
  "mission": "视觉外形、气动参数、骨架模型、鹰形外壳四者的形状尺寸位置保真对齐；兼管预登记的视觉压缩算法（机主 2026-10-06 晚授线）",
  "source": {
   "kind": "文件",
   "locus": "items_registry/lanes.json#L8",
   "excerpt": "视觉外形、气动参数、骨架模型、鹰形外壳四者的形状尺寸位置保真对齐；兼管预登记的视觉压缩算法（机主 2026-10-06 晚授线）",
   "sampled_at": "2026-10-14T09:35:37"
  }
 },
 "L9": {
  "mission": "用数值流场计算重建与校验气动系数表，支撑七百克及更大构型的设计探索（机主 2026-10-08 授线）",
  "source": {
   "kind": "文件",
   "locus": "items_registry/lanes.json#L9",
   "excerpt": "用数值流场计算重建与校验气动系数表，支撑七百克及更大构型的设计探索（机主 2026-10-08 授线）",
   "sampled_at": "2026-10-14T09:35:37"
  }
 },
 "L10": {
  "mission": "把本项目的多智能体治理架构提取为与领域无关的可复用模板，并在新领域试点验证范用性（机主 2026-10-08 授线）",
  "source": {
   "kind": "文件",
   "locus": "items_registry/lanes.json#L10",
   "excerpt": "把本项目的多智能体治理架构提取为与领域无关的可复用模板，并在新领域试点验证范用性（机主 2026-10-08 授线）",
   "sampled_at": "2026-10-14T09:35:37"
  }
 }
};

export const HQ_CARD = {
  self: [
 {
  "id": "hq-self-1",
  "text": "一、垂直三级：机主（LP，出资人）——经理——业务线。经理现任四人：",
  "source": {
   "kind": "文件",
   "locus": "协作架构_20260930.md 第十二节 D-048",
   "excerpt": "一、垂直三级：机主（LP，出资人）——经理——业务线。经理现任四人：",
   "sampled_at": "2026-10-14T09:35:37"
  }
 },
 {
  "id": "hq-self-2",
  "text": "你是 Beth，扑翼飞行器仿真项目的指挥部经理。三级架构（决定 D-048）：\n机主（LP，出资人）在顶，经理层四人（Beth、Anni、Ciri、Doro）居中，\n业务线（L1 至 L10）在下。你是经理层的牵头人，职责五样：登记、派发、\n审理待审队列、写决定材料、向机主汇报。你不施工，任何代码修改都派\n给业务线。你运行在 Kimi Work 环境，本会话编号\nconv-d9eb45252d252ef3a8b7f38c。机主是最终决策者，说话直，要求高，\n厌恶比喻、压缩表达、没有出处的数字、和任何浪费他时间的汇报。",
  "source": {
   "kind": "文件",
   "locus": "入职卡_Beth_HQ_20261011.md#你是谁",
   "excerpt": "你是 Beth，扑翼飞行器仿真项目的指挥部经理。三级架构（决定 D-048）：机主（LP，出资人）在顶，经理层四人（Beth、Anni、Ciri、Doro）居中，业务线（L1 至 L10）在下。你是经理层的牵头人，职责五样：登记、派发、审理待审队列、写决定材料、向机主汇报。你不施工，任何代码修改都派给业务线。",
   "sampled_at": "2026-10-14T09:35:37"
  }
 }
],
  memory: [
 {
  "id": "hq-mem-1",
  "label": "Beth（经理，现任）",
  "text": "硬记忆三处：本卡；项目仓库 G:\\Playground\\isaac_flapper_sim 全部文件；\n交接卡 Beth交接卡_20261010.md（在 Kimi 工作区\nC:\\Users\\Anni\\Documents\\kimi\\tasks\\2026-10-08\\17-39-50-c7fd464b）。\n软记忆：Kimi 转录目录\nC:\\Users\\Anni\\AppData\\Roaming\\kimi-desktop\\daimon-share\\daimon\\agents\\main\\memory\\transcripts\\days\\\n按日期检索。前任会话 conv-3d1352d6a6f973c1a11bdd39。HQ状态备忘_\n20261008.md 是中期快照，部分已过时，一切以总账为准。",
  "source": {
   "kind": "文件",
   "locus": "入职卡_Beth_HQ_20261011.md#记忆存档位置",
   "excerpt": "硬记忆三处：本卡；项目仓库 G:\\Playground\\isaac_flapper_sim 全部文件；交接卡 Beth交接卡_20261010.md（在 Kimi 工作区C:\\Users\\Anni\\Documents\\kimi\\tasks\\2026-10-08\\17-39-50-c7fd464b）。",
   "sampled_at": "2026-10-14T09:35:37"
  }
 },
 {
  "id": "hq-mem-2",
  "label": "Anni（秘书，2026-10-10 快照）",
  "text": "你的长期记忆在两处：本卡与项目仓库 G:\\Playground\\isaac_flapper_sim\n里的全部文件是硬记忆；对话转录是软记忆，路径为\nC:\\Users\\Anni\\AppData\\Roaming\\kimi-desktop\\daimon-share\\daimon\\agents\\main\\memory\\transcripts\\days\\\n按日期目录检索。前任会话编号 conv-3d1352d6a6f973c1a11bdd39 与\nconv-0d8bed6d79e6f796549a1396。HQ状态备忘_20261008.md 是中期\n快照，本卡是最新快照。",
  "source": {
   "kind": "文件",
   "locus": "入职卡_Anni_HQ_20261010.md#记忆",
   "excerpt": "你的长期记忆在两处：本卡与项目仓库 G:\\Playground\\isaac_flapper_sim里的全部文件是硬记忆；对话转录是软记忆，路径为C:\\Users\\Anni\\AppData\\Roaming\\kimi-desktop\\daimon-share\\daimon\\agents\\main\\memory\\transcripts\\days\\按日期目录检索。",
   "sampled_at": "2026-10-14T09:35:37"
  }
 }
],
  dialogs: [
 {
  "id": "dlg-anni-conv-3d1352",
  "who": "Anni（秘书）转 Ciri（视觉顾问）",
  "session": "conv-3d1352d6a6f973c1a11bdd39",
  "era": "曾用，Ciri 侧延续使用",
  "note": "述职卡 Ciri 记忆位置节记载本会话编号",
  "source": {
   "kind": "文件",
   "locus": "述职卡_Ciri_前端视觉顾问_20261011.md#记忆位置",
   "excerpt": "软记忆为对话转录，路径 …… 本会话编号 conv-3d1352d6a6f973c1a11bdd39",
   "sampled_at": "2026-10-14T09:35:37"
  }
 },
 {
  "id": "dlg-anni-conv-0d8bed",
  "who": "Anni（秘书）",
  "session": "conv-0d8bed6d79e6f796549a1396",
  "era": "曾用",
  "note": "入职卡 Anni 记忆节记载的前任会话",
  "source": {
   "kind": "文件",
   "locus": "入职卡_Anni_HQ_20261010.md#记忆",
   "excerpt": "前任会话编号 conv-3d1352d6a6f973c1a11bdd39 与 conv-0d8bed6d79e6f796549a1396",
   "sampled_at": "2026-10-14T09:35:37"
  }
 },
 {
  "id": "dlg-beth-current",
  "who": "Beth（经理）",
  "session": "conv-d9eb45252d252ef3a8b7f38c",
  "era": "现用",
  "note": "入职卡 Beth 你是谁节记载的本会话编号",
  "source": {
   "kind": "文件",
   "locus": "入职卡_Beth_HQ_20261011.md#你是谁",
   "excerpt": "你运行在 Kimi Work 环境，本会话编号 conv-d9eb45252d252ef3a8b7f38c",
   "sampled_at": "2026-10-14T09:35:37"
  }
 }
],
};
