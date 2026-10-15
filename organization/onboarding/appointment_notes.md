# 任命来源与历史角色差异记录（I-180）

用途：逐项记录各岗位最新明确任命与历史角色差异，作为收置迁移的依据；不用文件修改时间代替授权依据。未获批准的差异保留来源并提交机主，不擅自改写身份与权限。

| 岗位 | 最新明确任命 | 历史角色或旧文差异 | 处理 |
|---|---|---|---|
| Anni | 机主任命经理（扑翼主线研究与组织推进）；来源：organization/contacts.json anni.source.basis（机主主线会话确认） | 无冲突 | 卡随收置迁移，正文未改 |
| Beth | 机主任命经理二号位（Agent 框架与指挥部组织）；来源：contacts.json beth.source.basis（机主明确任命、会话确认接任） | 无冲突 | 卡随收置迁移，正文未改 |
| Ciri | 机主身份确认与经理任命；来源：contacts.json ciri.source.basis（"亲，你就是Ciri啊"；Yulia 任命讨论明确 Anni、Beth、Ciri 为三位经理） | 卡标题仍为"前端视觉顾问"（2026-10-08 旧称谓）；协作架构 D-048 已取消 Beth 与 Ciri 的顾问称谓并预告 Ciri 的入职卡随首个经理职责任务重写 | 差异如实保留：现行身份为经理，卡标题沿用旧称谓；重写属 D-048 预告的后续动作，本次收置不擅自重写，提交机主知悉 |
| Doro | 机主任命经理（工具接口一致性、整合与验收）；来源：contacts.json doro.source.basis | 无冲突 | 卡随收置迁移，正文未改 |
| Elsa | 机主身份确认与经理任命；I-179 判据三指定 tag=manager；来源：contacts.json elsa.source.basis | 无冲突 | 卡随收置迁移，正文未改 |
| Fil | 机主任命经理；来源：contacts.json fil.source.basis（会话确认领取入职卡并接任） | I-179 工程备注曾提示"Fil 与 Gwen 的专业岗位身份不自动升级"；机主后续明确任命为经理并以通讯录为准 | 按最新任命登记 manager；本记录即差异来源 |
| Gwen | 机主任命经理；来源：contacts.json gwen.source.basis（会话确认到岗） | 同 Fil：I-179 工程备注的"不自动升级"提示与机主明确任命并存 | 按最新任命登记 manager；本记录即差异来源 |
| Helm | 机主任命经理（仿真与计算流体力学引擎）；来源：contacts.json helm.source.basis | 无冲突 | 卡随收置迁移，正文未改 |
| Iris | 机主任命经理（感知压缩与轨迹规划）；来源：contacts.json iris.source.basis | 无冲突 | 卡随收置迁移，正文未改 |
| Jane | 机主任命经理（三维资产与构型指导）；来源：contacts.json jane.source.basis | 无冲突 | 卡随收置迁移，正文未改 |
| Yulia | 机主任命 Advisor；I-179 判据三指定 tag=Advisor；来源：contacts.json yulia.source.basis | 无冲突 | 卡随收置迁移，正文未改 |
| Zoya | 机主任命技术顾问 Advisor；来源：contacts.json zoya.source.basis | 全库未发现独立 Zoya 入职卡 | 不虚构建卡；通讯录与索引按依据留空，如需建卡由机主明确后另行办理 |

根目录旧入口：全部改为兼容转向说明（指向本目录同名卡），不再保存可独立更新的现行正文；历史版本在版本库历史。述职卡_Ciri（20261011）不是入职卡，不属于本次收置范围，保留原位。

已知跨路线事项：board_ui/scripts/gen_overview_data.py（五队领地）硬编码读取根目录入职卡_Beth 与 入职卡_Anni 两个路径，收置后该路径解析到转向说明，重新生成总览页数据前需要五队把两处路径改指 organization/onboarding/；已按领地纪律提交待审申报，不由三队代改。
