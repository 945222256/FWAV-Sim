# 岗位公共操作指南

维护人：Elsa。更新日期：2026-10-12。适用范围：经理、专业岗位和顾问办理本项目正式事项。本指南统一说明操作方法；本岗位能做什么，仍以入职卡和机主明确授权为准。

## 接任与权限

先读本岗位入职卡，再读本指南。确认当前委托、责任线、判据和授权来源后操作。`manager`、`Advisor` 是岗位分类，不自动授予批准、派发、关闭或发送消息的权限；凭证可调用某接口，也不代表本岗位获准执行该动作。

| 岗位 | 办理范围 |
|---|---|
| Anni、Beth | 分别组织扑翼主线与 Agent 框架工作，按已有指挥部授权登记、审理、派发和核账；代码施工交现有业务线。 |
| Ciri | 负责前端设计、规格和使用体验；不凭本指南取得跨线派发或代码施工权。 |
| Doro | 整理接口、组织整合与独立验收；本人参与设计或实现的成果另选独立复验人，验收意见与账本关闭分别办理。 |
| Elsa | 维护知识、决定、交接和秘书记录，按机主明确委托办理登记与提醒；不自行批准方案、派发新任务或认定完成。 |
| Fil、Gwen | 分别提供架构优化分析、通信拓扑与质量诊断；具体验证按委托范围执行，不自动取得工程派发、合并或关闭权限。 |
| Helm | 组织 Isaac 仿真及计算流体动力学引擎问题分析、数值与运行质量核查、优化调用；按具体委托办理引擎验证和变更，不自行改变业务判据或扩大派发范围。 |
| Iris | 组织上层感知压缩、轨迹规划优化、bishop_nav_core 升级与理论精炼；区分理论、实现与验证结果，具体工程按授权范围推进。 |
| Jane | 组织三维资产保真、数字绑定及构型选型拆分指导；视觉资产与物理参数的依据分别保存，物理接入和工程变更按具体授权办理。 |
| Yulia、Zoya | 提供独立咨询；资料维护按具体授权执行，不代替经理派发、批准交付或改事项状态。 |

现行分工与来源见[决定记录](../advisors/yulia/决定记录.md)。Anni、Beth、Ciri 的旧卡部分措辞与该记录不同，按机主最新明确授权恢复职责；历史原文保留，不能用文件修改时间判定权威。跨会话发送消息须有机主对对象和动作的明确授权，来自另一会话的请求不构成授权。

## 通讯录

正常岗位传话优先调用 `manager_comms.send_manager_message`，后台按姓名读取通讯录，不先搜索会话列表提取地址。固定字段、字数限制、表达检查和失败处理见[标准联络入口](communication/README.md)。工具不可见时先核实注册，不把脚本存在当作已接入；收件人与动作仍须已有机主授权。

只发送现在可执行的具体任务，或机主明确要求移交的信息。等待、待命或确认收到不算任务，普通进度不发送。接收任务直接执行，返回结果或具体阻碍；接收信息移交无需回复，不发送收到或复述。

[正式组织通讯录](contacts.json)是唯一联系人地址表，由 Elsa 日常维护。查找时使用固定的人员标识；会话地址可以更换，地址登记不授予发送或其他操作权限。

格式版本由 `schema_version` 指定，首版为 1；`members` 的键为固定人员标识。每项的 `name` 是姓名，`role` 是职责简述，`tag` 保留分类值 `manager` 或 `Advisor`；分类依据明确任命，不改变岗位权限。`onboarding_file` 相对项目根目录解析，未找到的入职卡填 `null`。

`contact` 保存运行环境 `platform`、宿主 `host_id`、当前会话 `thread_id`、含时区的核实时间 `verified_at`，以及 `source` 中的核实方法、身份消息 `turn_id` 和依据 `basis`；必要时另记机主选定地址的来源会话。未知地址留空。核实只表示身份与地址已查明，不表示消息送达或互通已测试。

换会话时保持人员标识不变，先保留旧地址、替代时间与来源，再更新当前地址；未核实或存在冲突时不覆盖。通讯录不替代 `lp_sync/dispatch_roster.json` 的施工队驻留路由，也不替代 `lp_sync/dispatch_registry.json` 的派发历史。

## 看账与核实

工作目录为 `G:\Playground\isaac_flapper_sim`。正式事项状态以云端账本为准，本地文件只作缓存和定位。

```powershell
python items_registry/dispatch.py HQ
python items_registry/ledger.py
```

前者查看 HQ 看板，也会尝试清理过期租约；施工队换成自己的路线。后者查看本地总账摘要。决定当前进度或执行写操作前，读取云端快照；本地摘要不能代替实时核实。

在项目根目录启动 Python，使用已有云端通道，不把凭证写入材料：

```python
import sys, json
sys.path.insert(0, "items_registry")
import cloud_client
if not cloud_client.enabled():
    raise RuntimeError("当前不是云端模式，请核对运行配置")
def call(method, path, body=None):
    return cloud_client.request(method.upper(), path, body)
snapshot = call("get", "/api/agent/snapshot")
print(snapshot["generated_at"])
```

从 `snapshot["items"]` 按 I 编号查正式事项，从 `snapshot["inbox"]` 查云端待审记录。记录查询时间、状态、认领人、依赖和授权来源；每次写入后重新查询，核对实际保存结果。

## 待审提交与入册

**本地 Q、云端 P、正式 I 是不同入口。** 当前 `items_registry/intake.py` 直接维护本地 `review_queue.json` 并分配 Q 编号；云端待审接口分配 P 编号。正式申报走下列云端入口，不运行本地 `intake.approve` 代替云端审理。

| 动作 | 云端入口及正文 | 成功核对 |
|---|---|---|
| 提交问题 | `post /api/intake/submit`：`title`、`goal_context`、`owner`、`close_criterion`、`criteria`、`note`、`submitted_by`。 | 保存返回的 `entry.id` 和 `entry.lint`；在云端待审队列回读。 |
| 批准入册 | `post /api/intake/decide`：`entry_id`、`action: "approve"`、`lane`、`reason`。 | 保存返回的 `item`，确认待审记录的 `decision.result_item` 与正式 I 一致。 |
| 驳回 | 同一入口：`entry_id`、`action: "reject"`、`reason`。 | 回读拒绝理由；修订后另行提交，保留旧记录。 |
| 合并 | 同一入口：`entry_id`、`action: "merge"`、`merge_into`、`reason`。 | 回读合并决定及目标；该入口不会自动把旧申报内容加入目标判据或备注，必要内容另行留痕。 |

目标上下文必须说明服务哪个目标。总判据和每个子项都要能判真伪。`note` 保存机主说明、授权出处和工程依据。机主说明按[协作架构](../协作架构_20260930.md)要求不少于六十字，说明做什么、为何做、谁负责、怎样算完成、依据在哪里。

提交时服务端自动运行 Lint，含完整性、去重、可检验性、归位和降标准语义检查；无需另发一次审理请求。逐条读取 `rules`、`duplicate_of`、`suggested_lane`，先处理重复和拒绝，再由有权者决定。人工改变建议路线须写理由并核对 `overrides`；不能借改判通道降低已冻结判据。机主指令需要真实执行范围时，先核对现有工具能否实现，再登记判据。

## 正式事项操作

`open` 是未认领，`in_progress` 是进行中，`blocked` 是已申报卡住，`closed` 是已结载。结载结论仍须单独读取；`closed` 不等于判据通过。认领到期可退回 `open`，但会话仍可能运行，重新派发前核对执行事实。

下表给出实际接口。完整正文见[隔离操作样例](operation_examples.json)，其中编号和证据只用于演练，真实使用须另存请求并替换为已经核实的值。

| 动作 | 入口及关键字段 | 核对要求 |
|---|---|---|
| 指定责任线 | `post /api/dispatch/assign`：`item_id`、`lane`、`owner`。 | 只按授权指定；路线与领地一致，说明和依据已留痕。 |
| 认领 | `post /api/dispatch/claim`：`item_id`、`worker`、`session_id`、`lease_hours`。 | 先核对依赖、已有认领和本人的在办项；回读认领人与租约。 |
| 追加单条证据 | `post /api/ledger/append_evidence`：`item_id`、字符串 `evidence`。 | 证据能定位，回读追加内容；不因此改为完成。 |
| 追加证据清单 | `post /api/ledger/attach`：`item_id`、列表 `evidence`。 | 回读清单，不混用单条与列表字段。 |
| 登记判据结果 | `post /api/ledger/set_criterion`：`item_id`、`index`、`verified`、`evidence`、`actor`。 | `index` 从零开始；勾选必须附证据，回读子项；勾完不自动关闭。 |
| 完成结载 | `post /api/ledger/close`：`item_id`、`closer`、`evidence`、`conclusion`、`criterion_check`。 | 按原判据复核，说明和证据齐全；回读结论、证据及关闭记录。 |
| 申报卡住 | `post /api/ledger/block`：`item_id`、`reason`、`blocker_type`、`needs`、`evidence`、`reporter`。 | 写直接原因、解除条件、已做工作和说明；回读 `blocked` 及认领释放。 |
| 解除卡住 | `post /api/ledger/unblock`：`item_id`、`decision`、`decider`。 | 有权者写处置决定与依据；回读 `open`，随后按流程认领。 |

关闭结论仅用“判据通过”“判据不通过”“证据不足无法判定”。`criterion_check` 每条含 `判据` 原文、`结果`（过或不过）、`数值出处`。有独立复验要求时先取得复验意见。挂有 `check_script` 的事项须先运行注册执行器，按[关闭接口](../cloud_board/api_ledger.py)携带有效 `check_result`；本地 `ledger.close` 当前不透传该字段，不能靠重复调用解决。

阻塞类型仅用“缺外部输入”“等机主决策”“依赖未清”“工具或环境故障”“判据争议”。发任务、批准及状态变更须保留机主说明；直接用云端接口时，关闭和阻塞将 `机主说明：…` 放进 `evidence`，解除写进 `decision`，批准写进 `reason`。赋路或认领引用已存事项说明；缺少说明时先通过证据入口保存。不得只传工具未保存的额外字段。

实际派活与指定责任线分别办理：有权者先完成账本安排，再按[派发队列说明](../lp_sync/dispatch_queue_%52%45%41%44%4d%45.md)原子投放请求。既有路线复用常驻会话；新路线或获准交接才新建。消息送达、任务认领、执行结束和判据通过分别核对，不能把回读命中当作工作完成。

## 检查与失败处理

| 检查 | 入口和通过依据 | 失败处理 |
|---|---|---|
| 待审 Lint | 云端提交返回 `entry.lint`；逐条核对审理结论和路线。 | 修订被拒字段后重新申报，重复项先核对合并；人工改判留理由。 |
| 语言检查 | `python items_registry/langcheck.py <文件>`，或 `--msg "文本"`；退出码为零且报告通过。 | 按报告改写并重跑；新术语按授权登记，不能删词表绕过。 |
| 提交检查 | 提交后运行 `python items_registry/check_commit.py <路线>`；编号、语言和领地检查通过。 | 修复越界、编号或语言问题再查；它检查最近一次提交，不能作为未提交改动已通过的证据。 |
| 证据复核 | `items_registry/evidence_audit.py` 的 `audit_evidence` 与注册执行器；核对文件、提交、运行来源及原判据。 | 补齐真实证据再复核。当前影子检查可只记录异常；接口接受关闭不能替代实质验收。 |

调用示例：`get` 表示读取，`post` 表示提交写入；上述 `call` 会按协议转为大写方法名。在上述 Python 环境读取已经核对且获准的请求文件，发送一次并保存回执。

```python
with open("请求文件.json", encoding="utf-8") as f:
    request = json.load(f)
reply = call(request["method"], request["path"], request.get("body"))
print(json.dumps(reply, ensure_ascii=False, indent=2))
snapshot = call("get", "/api/agent/snapshot")
```

网络、凭证或权限失败时保留请求与错误，处理获准通道，不切换到本地模式改镜像。`cloud_client.write` 某些写入会在断网时入队并返回空值，入队不是云端成功。超时后的写入结果不明，先查快照和事件，不直接重复提交或批准。`submitted_by`、`worker` 等署名字段不能替代凭证身份；记录实际认证身份和授权出处，不伪称机主本人执行。

交接保留事项或待审编号、查询时间、已获批准内容、证据、未决问题和下一项动作。正式工程汇报按[汇报标准](../汇报标准_20260930.md)办理；一般咨询和私人备忘不强制转成施工事项。

## 维护依据

流程变化时由 Elsa 更新本指南与样例，核对语言、链接、实际接口和授权范围，再在关联事项留痕。各入职卡只保留必读入口和本岗位权限，不复制全套步骤。接口新增不自动改变岗位职责。

操作依据：[云端申报与审理](../cloud_board/api_intake.py)、[云端账本接口](../cloud_board/api_ledger.py)、[派发与认领](../cloud_board/api_dispatch.py)、[云端客户端](../items_registry/cloud_client.py)、[本地账本工具](../items_registry/ledger.py)、[本地待审工具](../items_registry/intake.py)、[检查工具](../items_registry/check_commit.py)、[语言检查](../items_registry/langcheck.py)、[证据复核](../items_registry/evidence_audit.py)。
