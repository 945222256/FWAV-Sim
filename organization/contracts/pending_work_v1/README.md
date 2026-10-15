# pending-work-v1 契约（冻结）

版本：pending-work-v1。冻结日期：2026-10-15。承办：L3（I-233），审核 beth，
页面消费与最终前端审核：Ciri（L5）。本文件冻结字段语义与样例；字段变更须升
版本，不得原地改义。样例：snapshot_sample.json（真实生产只读快照）、
events_sample.json（真实事件重建节选）。实现：lp_sync/pending_work.py 与
lp_sync/pending_work_events.py；控制台只读入口：
GET /api/console/pending_work、GET /api/console/pending_work/events?after_cursor=N，
上行数据面：POST /api/console/pending_work/ingest、POST
/api/console/pending_work/events/ingest（cloud_board/api_console.py）。

## 一、快照（snapshot）

- snapshot_id：形如 pw-<时刻>-<内容摘要12位>，同一来源版本集合重算得到
  相同摘要；摘要基数为事项号排序集合、汇总与来源版本。
- sampled_at：采样时刻（毫秒 ISO）。sample_validity.max_age_s：采样有效
  期（缺省 300 秒），expires_at 为过期时刻；过期采样由消费端按待核对处理。
- source_versions：各来源的版本与读取错误——items（origin=cloud 或降级
  file 缓存、generated_at/读取错误）、kernel_state（generation、last_tick_at、
  tasks/runs 数、读取错误）、lane_workers_config 与 roster（文件版本）。
- completeness：complete 或 partial；partial 附原因列表（source、severity、
  reason）。失联不假零：来源不可读时相关输出降级并说明。
- summary：total_pending（明细去重后条数）、by_category（四类计数）、
  exclusions（requirement/claimed/pending_review/closed/test_channel 各附
  计数与说明）、needs_verification_count、by_lane（各线计数与事项号）、
  unassigned（未分配路线计数、事项号与说明，独立入口不伪装成业务线）。
- items：按正式事项号去重的待领明细。每项字段：
  item_id、title、kind、status、lane、goal、reviewer、owner；
  category（ready/waiting_conditions/awaiting_orchestration/needs_verification
  四类之一）；primary_reason（code、category、sentence、可选 related/recovery、
  provenance）；related（dependencies 列表含各依赖状态、delivery_impact）；
  recovery（恢复条件短句）；evaluation（at、validity、evaluator、provenance
  ——已入队为 kernel_admission_outcome，未入队为 static_same_source 并明示
  未经内核实际准入）；enqueue（in_kernel_queue、request_ids 请求层明细、
  first_enqueued_at、total_dispatch_attempts——多次重试只计一项）；
  execution_ref（worker_id、session_id、run_id、dispatch_request_id、
  kernel_phase）。
- needs_verification：待核对清单（KIND_UNKNOWN 历史类别缺失、
  CLAIM_RUN_SESSION_MISMATCH、CLAIM_WITHOUT_RUN、MULTIPLE_ACTIVE_RUNS、
  STATUS_UNCLASSIFIED、ADMISSION_REASON_UNKNOWN 等），与确定待领分开核账，
  不批改历史记录。
- read_only：恒为 true。

### 分类语义
- ready：准入规则层面无待满足条件（依赖、授权、声明、资源冲突均无碍）。
  就绪不代表立即开工：容量与冲突另有实际限制，见容量节。
- waiting_conditions：等待事项自身条件——DEP_NOT_CLEARED、
  DEP_WITHDRAWN_UNREVIEWED、RESOURCE_WRITABLE_OVERLAP、
  RESOURCE_EXCLUSIVE_CONFLICT、DECLARATION_MISSING、AUTH/
  SESSION_TURN_BUSY、RATE_LIMITED_WAIT、ITEM_STATUS_NOT_OPEN、
  OWNERSHIP_UNVERIFIED。
- awaiting_orchestration：条件已满足、等待调度——LINE_CAPACITY_FULL、
  NOT_ENQUEUED（未入内核队列）、IN_QUEUE_<PHASE>。
- needs_verification：无法确定——类别缺失、认领与运行矛盾、原因未识别、
  采样降级波及。

### 就绪评估的规则来源
已入队任务直接采用内核准入结果（task 相位与 last_error 为
orchestrator_kernel.py 与 lane_workers.py 准入路径的结构化输出，搬运不
解析）；未入队任务读取任务线同源数据（items.depends_on、状态、
delivery_impact.review_required、认领字段）做静态评估并明示 evaluator。
不以尝试认领判断就绪，不解析 last_error 之外的自由文字猜依赖。

## 二、容量（capacity）

- status ok/degraded；degraded 附原因（配置不可读等），不假零。
- global 与 by_lane：effective_limit（I-217 lane_workers.json 每线并发配置，
  全局为十线之和）、occupied（内核任务持槽数）、dispatchable
  （=max(0, limit-occupied)）、reserved、policy_source。
- reserved 恒为 null 且 reservation_policy.status=not_set：现行体系无预留
  策略，null 表示未设置而非零；引入预留策略须另立工程范围并升契约版本。
- separate_measures_note：页面并行额度（console_parallel_limit）与近五分钟
  活跃会话数为独立口径，只引用不与本容量混算。

## 三、事件（events）

- 类型：slot_released、dispatch_selected、claim_confirmed、run_started、
  condition_wait、recovery、result_unknown。
- 事实来源：内核账本 ledger.json（权威因果）与内核状态 tasks/runs。
  run_started 只在存在真实运行记录时生成；派发受理、按钮点击与普通轮询
  不生成事件、不重放接续；重复回执（duplicate_result_skipped）不映射。
- event_id：类型+来源种类+事实引用（账本事件名@时刻）+身份字段的 SHA256
  前 32 位；同一事实重复记录、重复派生、重启重建得到同一标识并去重。
- cursor：持久递增游标；GET ?after_cursor=N 增量读取；gaps 按来源高水位
  （账本长度）核对，来源截断时报告缺口。
- 每事件字段：event_id、type、source_kind、source_ref、occurred_at、
  identity（item/lane/worker/session/run/request 完整身份）、state_change、
  fact_source、causes/triggers（同事项时序因果关联，仅按已核实事件）。

## 四、只读边界

快照与事件查询不认领、不派发、不释放工位、不启动模型、不恢复旧会话。
上行端点只存储快照与事件（同 lane_queue_snapshot 镜像模式），事件按
event_id 服务端去重；无任何调度副作用。
