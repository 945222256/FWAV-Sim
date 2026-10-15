# 各岗位现行入职资料索引（I-180 公共入口）

维护规则：本索引是各岗位现行有效入职资料的唯一公共入口；现行正文统一保存在 `organization/onboarding/` 目录；项目根目录同名文件仅作旧路径兼容入口。岗位权限以入职卡与机主明确授权为准，本索引与目录收置不改变任何权限。每项记录：人员标识（与通讯录键一致）、现行卡、任命来源摘要、批准边界、当前动作入口。

| 人员标识 | 姓名 | 分类 | 现行卡 | 任命来源摘要 | 缺失与差异记录 |
|---|---|---|---|---|---|
| anni | Anni | manager | 入职卡_Anni_HQ_20261010.md | 机主任命经理（通讯录 anni.source.basis：主线研究与会话确认） | 无待决差异 |
| beth | Beth | manager | 入职卡_Beth_HQ_20261011.md | 机主任命经理二号位（通讯录 beth.source.basis） | 无待决差异 |
| ciri | Ciri | manager | 入职卡_Ciri_前端视觉顾问_20261008.md | 机主身份确认与经理任命（通讯录 ciri.source.basis；D-048 取消顾问称谓） | 卡标题沿用旧称谓"前端视觉顾问"，现行身份为经理；差异记录见 appointment_notes.md |
| doro | Doro | manager | 入职卡_Doro_接口一致性与独立验收_20261011.md | 机主任命经理（通讯录 doro.source.basis） | 无待决差异 |
| elsa | Elsa | manager | 入职卡_Elsa_知识连续性与秘书服务_20261011.md | 机主身份确认与经理任命（通讯录 elsa.source.basis；I-179 指定 manager 分类） | 无待决差异 |
| fil | Fil | manager | 入职卡_Fil_代码质量与架构优化分析_20261011.md | 机主任命经理（通讯录 fil.source.basis） | 无待决差异 |
| gwen | Gwen | manager | 入职卡_Gwen_通信拓扑与服务质量_20261011.md | 机主任命经理（通讯录 gwen.source.basis） | 无待决差异 |
| helm | Helm | manager | 入职卡_Helm_仿真与CFD引擎_20261012.md | 机主任命经理（通讯录 helm.source.basis） | 无待决差异 |
| iris | Iris | manager | 入职卡_Iris_感知压缩与轨迹规划_20261012.md | 机主任命经理（通讯录 iris.source.basis） | 无待决差异 |
| jane | Jane | manager | 入职卡_Jane_三维资产与构型指导_20261012.md | 机主任命经理（通讯录 jane.source.basis） | 无待决差异 |
| yulia | Yulia | Advisor | 入职卡_Yulia_组织与长期部署顾问_20261011.md | 机主任命 Advisor（通讯录 yulia.source.basis；I-179 指定 Advisor 分类） | 无待决差异 |
| zoya | Zoya | Advisor | （缺失，无独立入职卡） | 机主任命技术顾问 Advisor（通讯录 zoya.source.basis） | 全库未发现 Zoya 独立入职卡，按依据留空不虚构；如需建卡由机主明确后另行办理 |
| kula | Kula | 特殊成员 | [入职卡_Kula_疑难协助与档案知识_20261012.md](入职卡_Kula_疑难协助与档案知识_20261012.md) | 2026-10-12 机主在 Yulia 会话明确指定疑难协助、档案知识和辅助 Anni 攻坚职责 | 本轮仅形成入职卡；联系地址登记另行办理。 |
| luna | Luna | 特殊成员 | [入职卡_Luna_内部值守转发_20261012.md](入职卡_Luna_内部值守转发_20261012.md) | 2026-10-12 机主在 Yulia 会话明确指定使用 luna 小模型的内部值守转发职责 | 本轮仅形成入职卡；review_owner 与值守启用条件待落实，未配置自动任务。 |
| mira | Mira | 专业助手 | [入职卡_Mira_数据驱动建模与学习_20261014.md](入职卡_Mira_数据驱动建模与学习_20261014.md) | 2026-10-14 机主指定黑盒化、数据驱动、扩展动态模态分解、模仿与强化学习线主管，独立思考优化方向并提供理论指导 | 本轮形成入职卡；通讯录身份分类与会话地址未在本轮登记。 |
| nono | Nono | 专业助手 | [入职卡_Nono_展示规划与文献报告_20261014.md](入职卡_Nono_展示规划与文献报告_20261014.md) | 2026-10-14 机主指定展示面规划、文献研究、综述和报告标准化建设职责 | 本轮形成入职卡；通讯录身份分类与会话地址未在本轮登记。 |

批准边界：各岗位办理范围见 `organization/manager_operations.md` 接任与权限一节的岗位表；分类与目录收置不增加批准、派发、改状态或发送消息的权限。

当前动作入口：新接任某岗位时，先读本岗位现行卡（本目录），再读 `organization/manager_operations.md`，按其看账与核实一节读取云端账本确认当前委托；需要联系其他岗位时查 `organization/contacts.json`；需要提交待审事项时按操作指南的待审提交与入册一节办理。

历史依据：收置迁移发生在 I-180（2026-10-12），全部历史版本在版本库历史（git log --follow -- organization/onboarding/<卡名>），迁移差异记录见 `appointment_notes.md`。
