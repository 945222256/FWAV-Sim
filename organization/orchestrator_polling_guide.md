# Luna 编排器轮询操作指南

维护：Beth。更新：2026-10-14。所有相对路径均以 `G:\Playground\isaac_flapper_sim` 为根。

## 2026-10-15 本轮生效：运行完成补证与实时核账

标准岗位通道已核实通过正式 `codex queue` 入队，允许对忙碌审核人的已核实队列提交既有授权范围内的提醒，不等待其聊天变空闲。保持“不抢占”原则：不停止、取消或替换其当前会话；排队受理与实际收件分别核实。原卡“忙碌先等待”的旧操作方式不再用于阻止正式队列提交。已由审核人接手并留有处置记录的结果，同样参与通知去重，入口为 `organization/luna_polling_records/reviewer_work_20261015.json`；已复验实现、仍待生产加载的结果不能重写成整项验收完成。

机主要求修复审核后仍需逐件追问的接续断点。对原有“completed 运行完成结果按 reviewer 转发”的授权，按真实运行事实执行：原始 outcome 为 `evidence_appended`，但内核已结束并确认落实的结果，应递送为“阶段补证请复核”；保留原始类型，不能改写为母项待审核、整项通过或关闭。核对该结果的 run、applied_at、reviewer、证据与当前云端事项，阶段 run 不须等于母项较早 pending_review.run_id。仍不扩展到任意异常广播。下文历史记录中“追加证据不在 pending_review 筛选内”的描述解释了旧漏发原因，不再作为跳过已结束运行结果的依据。

核账优先走已验证的宿主只读文件桥，而非反复调用被会话网络权限拒绝的直接通道。完整生效提示词为 `organization/luna_polling_records/authorized_prompt_20261015.txt`；原任务、模型、收件范围和轮询间隔保留。人工代发、真实收件与审核已完成记录都参与去重；准备、受理、收件、审核分别保存。

## 2026-10-14 生效修正：待审核提醒

机主在 Beth 当前会话明确确认：**“允许，按各事项 reviewer 转发”**。现有 Luna 轮询任务新增 `pending_review` 提醒范围，不再等待事项关闭才通知审核人；`completed`、`closed` 保留原含义。此授权仅覆盖已明确指定 reviewer 的本项目结果通知，不授予验收、关闭、派工或任意广播权限。

每轮同时核对新事件及已准备但尚未尝试发送的有效提醒。`sending/` 目录中的记录若仍是 `prepared`、批次和包的 `send_attempt` 均为空且无其他真实发送尝试，并不属于“已经发送”，不能因此永久跳过。已有发送尝试或回执不明时继续对账，不另起请求重发。

发送待审核提醒前读实时账本：事项当前仍为 `pending_review`，通知运行号与当前交付一致，交付未撤回、证据可定位、reviewer 与通讯录一致。I-217 第一份交付已经退回补修，其旧提醒必须跳过；新交付用新的运行和版本分别核对。

岗位联络优先使用 `manager_comms.send_manager_message`，固定身份、对象、用途、摘要、动作及机主授权，遵守80/80/100字及合计240字的正文限制。标准入口失败先报告具体接入问题、核对持久发送记录；结果不明不得换入口重发。已确认未发送且现行通讯录唯一地址经过核实，在明确授权下可通过该平台现有正式工具恢复投递，完整保留替代入口和真实回执，不猜地址、不搜索其他会话替代目标。`queued` 只证明受理。

定时任务保持现行频率和模型，不另建重复自动化。原20分钟名称与原暂停记录是历史时间点；本日回读任务已 ACTIVE，实际间隔为30分钟。当前完整提示词及授权见 `organization/luna_polling_records/authorized_prompt_20261014.txt`。以下历史操作与一次性测试授权仍保留，不能据旧状态覆盖本段新授权。

## 现在怎么做

### 2026-10-15 通道修正：本机会话不能联网时仍核对云端

Beth 已实测：Luna 当前会话直接读取云端反复返回 `WinError 10013`，而常驻宿主通过现有文件桥完成同一云端事项的实时读取，首次成功请求用时 4.634 秒。记录见 `logs/hq_intake/beth_review_start_20261015/bridge_read_receipt.json`。这不是云端整体停机；不能因直接通道的同一个权限错误而每轮跳过所有提醒。

对需要通知的事项，优先使用现有文件桥的只读 `get_item_line`。Luna 只投放读取请求和读取返回文件；实际云端调用由已运行的宿主办理，不启动新服务、不修改安全设置、不连接本机端口。请求示例：

```json
{
  "schema_version": 1,
  "request_id": "luna-read-I-218-20261015T024500Z",
  "op": "ledger_read",
  "agent_id": "luna",
  "ledger": {"action": "get_item_line", "args": {"item_id": "I-218"}},
  "max_attempts": 2,
  "retry_delay_s": 5
}
```

示例中的时间须换成本轮时间，请求编号仅含英文字母、数字、点、短横或下划线，长度 4 至 64。把 JSON 保存到本轮记录目录后，用 `python lp_sync/bridge_client.py submit <请求文件>` 投放，再用 `python lp_sync/bridge_client.py wait <请求编号> --timeout 20` 读取结果。同一编号仍在途时只查结果，不能反复投放。

只有返回 `ok=true`、`result.bridge_ok=true`、`deferred=false` 才使用本次 `result.ledger_result.item`；记录请求、实际完成时间、返回文件和所核对的事项。该对象含正式状态、reviewer、当前 pending_review 运行、版本与撤回历史，可用于待审核提醒核对。它是本轮宿主实时读取的云端投影，不是本地账本镜像。若仍失败或超时，保留具体错误并报告 Beth，不能伪造核账成功。

机主本轮要求 Beth 直接通知 I-186、I-210、I-218、I-220、I-221 的审核人开始审；Beth 已通过标准岗位联络通道提交三条集合通知。逐事件覆盖与原始回执入口见 `organization/luna_polling_records/manual_notice_20261015_beth_review_start.json`。Luna 须把这些真实外部发送记录纳入去重：queued 继续对账，不能再发同一份提醒，也不能把 queued 补写成 delivered。

I-218 补证和 I-221 先行成果的原始类型是 `evidence_appended`。它们不满足 pending_review 同运行校验，不能改类型冒充整项待审核。本轮已由 Beth 按机主明确要求通知审核；此处不扩大 Luna 对其他追加证据或异常广播的既有发送范围。

Luna 每次醒来依次完成：**看心跳 → 看队列 → 看通知包 → 核对审核人与授权 → 递送或保留原因 → 保存游标**。不需要启动编排器，也不需要从 Codex 沙盒连接本机端口。

后台由 Windows 任务计划程序托管，入口均无窗口：

| 服务 | 工作 |
|---|---|
| LP Production Host I208 | 接收文件请求，管理各 L 独立 ZCode 执行器，派发与收取实际回执 |
| LP Production Kernel | 读取已批准任务，持久排队、自动接续、对账结果及恢复未完成运行 |
| LP Production Collector | 采集会话与上下文，办理守门交接 |
| LP Notification Preparation | 每 5 秒准备审核通知；默认只准备，不发送聊天消息 |

任务在登录时启动，系统每分钟补起已退出的服务；已运行实例不重复启动。服务不属于经理或施工会话的进程树。内核使用持久状态与规则，不是长期占用的模型聊天；需要施工时才调用模型。

## 一轮读取入口

| 文件/入口 | 核对内容 |
|---|---|
| `lp_sync/kernel_state/state.json` | last_tick_at 是否推进、任务请求与运行号、执行阶段和错误 |
| `lp_sync/kernel_state/lane_queues.json` | 各 L 当前项、等待数、顺序、不能开工的具体原因；open 总数不等于排队数 |
| `lp_sync/kernel_state/ledger.json` | 上次游标之后的唤醒、结果、恢复与迟到回执 |
| `lp_sync/kernel_state/notifications/pending/` | 新事件；排除 .reason.json、.meta.json |
| `lp_sync/kernel_state/notifications/batches/` | 已冻结批次与原始成员，保留 batch_id 和每个 event_id |
| `lp_sync/notification_outbox/<reviewer>/` | 待发送包、send_attempt、receipt；文件存在不等于送达 |
| `lp_sync/kernel_state/notifications/sending/`、`delivered/`、`acked/` | 已准备、在途、实际送达、逐事件确认 |
| `organization/contacts.json` | 稳定人员标识、平台、host_id、thread_id、核实来源和时间 |
| 云端 `/api/agent/snapshot` | 正式 I 状态、reviewer、待验收版本；云端为正式状态权威 |

通知必须保留 event_id、item_id、run_id、reviewer、category、evidence_ptr、action_needed。正式字段叫 **reviewer**，取通讯录稳定人员标识，不能从负责人或标题猜收件人。缺字段、路由冲突、空地址或失效地址保留事件及原因。

网络未取得 HTTP 回应时记“本轮未取得云端快照”，保留时间、入口和原始错误；不能把旧镜像称为当前状态，也不能据此把原 I 改成 blocked。可读取获授权经理本轮保存的实时核验记录，保留查询人、时间、入口与比对依据。

## 任务状态怎么读

| 状态 | 含义 |
|---|---|
| open | 未认领；只有已批准且入内核的任务才会自动排队 |
| in_progress | 已认领施工；还需核对实际运行记录 |
| pending_review | 已交付指定版本、运行号和证据，待审核；释放本次执行名额，不表示通过 |
| blocked | 该事项确有阻塞；本次执行结束后同线无依赖任务可继续 |
| closed | 正式结载；结论与逐条判据另读 |
| 内核 completed | 本次结果已落实，不自动等于 closed 或判据通过 |

默认依赖仍要求正式验收。提前使用交付，必须显式登记所需交付版本、证据及授权；撤回或版本不匹配不放行，并标记已有下游结果需要复核。未知派发结果继续占位对账，不能当成结束去重派。

## 集合递送与权限

同审核人的普通事件最多等待 10 秒集合，每批最多 25 个；窗口与条数有有限上限。blocked、execution_failed、uncertain 单独立即准备。不同 reviewer 不混包。

1. 先对账批次、逐事件记录和原始回执。已有 delivered 不重复发送；已有 send_attempt 但结果未知，保持 pending_receipt 并对账，禁止整批重发。
2. 批次 prepared 且 send_attempt 为空，表示内容已准备，尚未尝试发送。本日机主已授权按事项明确 reviewer 递送有效的待审核提醒；按新授权核对目标、事件和运行后发送。历史范围外的通知仍需相应明确授权。现行通讯录必须再次核对；地址变更需重新核实。
3. 发送前保存 owner_approved=true、授权来源 source、精确目标 recipient、精确 batch_id。调用 reviewer_batches.prepare_send 记录尝试，由具备真实通道工具的获授权会话发送已保存正文一次。
4. 原样保存工具应答，通过 record_receipt 对账。有效 receipt 的 threadId 必须对应目标。没有真实工具应答时不手填回执，prepared、submitted、pending_receipt 均不得记为 delivered。
5. 指定 reviewer 在实际送达后逐项 ACK；部分确认不重发整批，未确认项继续可追踪。Luna 不代确认或作技术结论。

接口在 `lp_sync/reviewer_batches.py`。`luna_consumer.py --once` 会领取并修改记录，不是只读预览；后台准备服务已运行，Luna 日常不需要再启动消费者。--send 对 ZCode 也要求实际授权。

历史指南交接授权来自机主要求 Beth“写指南并告诉 Luna”，该交接本身不授予任意发送权限。2026-10-13 的 I-213、I-215、I-216 一次性测试包授权与回执仍只属于该包，见 `logs/hq_intake/beth_orchestrator_completion_20261013/reviewer_batch_live/`。本日新增待审核范围来自机主新的明确确认，按本指南首节执行，两份授权分别保存。

## 周期、记录与异常

Luna 原聊天已有 luna-automation-update，历史曾按20分钟试运行并处于 PAUSED。本日已回读为 ACTIVE，实际间隔30分钟，且17:23实际轮询已执行。现有任务新增待审核提醒范围，保留原任务与现行频率。紧急异常广播未在本次授权中新增；旧的一次性集合测试授权不替代本日授权。

每轮将时间、游标、发现事件、核验来源、处理动作与未决原因原子保存到 `organization/luna_polling_records/`，唯一文件名保留历史。无新变化保持安静。心跳停滞、记录损坏或明确失败交 Beth 处理；不自行杀进程、重启宿主、改任务状态或重派未知请求。

I-178、I-200 两条旧包回执未知，继续保留 pending_receipt，不因新包成功而补写旧包回执。

相关材料：[公共操作指南](manager_operations.md)、[通讯录](contacts.json)、[Luna 入职卡](onboarding/入职卡_Luna_内部值守转发_20261012.md)、[内核说明](../lp_sync/kernel_README.md)、[上线证据](../logs/hq_intake/beth_orchestrator_completion_20261013/WORK.md)。
# 2026-10-14 实际递送核验

机主已批准新增待审核提醒，按各事项已登记 reviewer 转发。Luna 已实际递送 I-218 的待审核提醒给 Ciri；Ciri 已响应、完成独立审核并正式退回该版本。原通道 queued 回执和真实收件方完成记录分别保存，见 `logs/hq_intake/beth_lane_concurrency_20261014/luna_delivery_confirmed_20261014.json`。此记录确认该条提醒送达及收件方行动，不代表其他提醒已发送或事项验收通过。

I-217 的旧版 lane-workers-v1 已撤回；当前有效候选是 run-beth-lane-workers-core-rework-20261014-a1 / lane-workers-v2，正式状态于18:51:27回读为 pending_review，reviewer=beth。收件人忙碌时按现行规则保留待发，后续再检查，不能把待发丢弃。

