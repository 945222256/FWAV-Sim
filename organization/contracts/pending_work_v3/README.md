# pending-work-v3 契约（冻结）

版本：pending-work-v3。冻结日期：2026-10-15。承办：L3（I-233 返修 v3），审核 beth。
本版在 v2 基础上按真实消费边界复核修订；v1/v2 契约与样例保留作历史依据
（organization/contracts/pending_work_v1/、pending_work_v2/）。样例：
snapshot_sample.json、events_sample.json（生产真实只读构建）。

## 版本约定

生产者（pending_work / pending_work_events）与接收端（cloud_board/
api_console.py 四端点）统一当前版 pending-work-v3；v1/v2 为冻结历史版，
作为遗留兼容继续收存（快照按收到版本回读，事件存储记录收到版本）；
未知版本经真实路由明确拒绝（ApiError，不落库）。端点校验为函数内字面
版本表，不依赖模块级常量。

## 相对 v2 的语义修订（逐条）

1. 逐批回执身份：事件上行端点返回 {contract_version, after_cursor,
   first_cursor, last_cursor, count, batch_digest, added, deduped,
   stored_next_cursor}。发送方（push_once）仅当回执与当前批逐项匹配、
   且 added+deduped==count（全量保存或全量去重）时推进持久已确认游标；
   带 v3 身份字段的回执必须通过完整核验，核验失败（如摘要被篡改）不落
   入计数兜底。遗留计数回执（无 v3 字段、added 或 added+deduped 与批量
   全等）按契约允许的"数量确认"接受并标记 verified_receipt=
   legacy_count。空回执、缺字段、部分保存、错批、错版本、返回类型错误
   与超时未知均不推进，按原请求对账或同事件幂等补发（服务端按
   event_id 去重，重发同批得全量去重确认）。
2. 认领确认共同对账：claim_confirmed 须任务非 test、已派发且会话在案，
   云端事项认领与任务在完整会话（claim_session_id）与工位
   （claim_worker_id 在案时）都一致且状态在办；任一不一致拒绝假确认，
   生成 result_unknown 保留未知。
3. 实际开工证据：run_started 不再由本地 run.status=active（派发受理
   产物）或账本 run_started 事件生成；须读到实际执行消费者（会话库）在
   本次运行开始之后的模型/工具活动证据才生成（state_change 附
   execution_evidence）；受理、排队或仅本地 active 一律 result_unknown
   保留未知（不造心跳、不升级受理标签）。
4. 容量来源降级传递：items 源失联（云端认领不可读）时容量
   status=degraded、global 与各线 dispatchable=null（已知内核持槽保留
   呈现，可派数未知），degraded_reasons 说明；不再一边报可派一边缺
   认领源。
5. 不可复核准入短语只作诊断：KERNEL_REASON_CODES 中无法按当前事实
   复核的短语（会话忙、限流、归属未核、缺声明、恢复耗尽、派发耗尽）
   不再显示为已确认等待；分类为 needs_verification
   （ADMISSION_UNVERIFIED_<CODE>，diagnostic_only=true，原句留
   source_text 作历史），evaluation.validity=unverified；可复核短语
   （依赖/撤回/容量/冲突）经当前事实复核生效，复核不符判过期并按当前
   事实重分类（含 delivery_impact.review_required=False 的撤回旧句）。
   静态评估（未入队）与内核复核评估以 evaluator 字段区分"真正未评估"
   与"按当前事实已评估"。

## 其余字段

快照身份、来源版本与新鲜度锚、范围排除、四类分类、待核对清单、容量
三源分账、保留窗口 retention、事件类型与工位跨事项因果（同 v2，见
../pending_work_v2/README.md）。
