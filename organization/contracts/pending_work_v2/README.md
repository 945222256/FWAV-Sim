# pending-work-v2 契约（冻结）

版本：pending-work-v2。冻结日期：2026-10-15。承办：L3（I-233 返修 v2），审核 beth。
本版在 pending-work-v1 基础上按独立复核九项反例修订字段语义；v1 契约与样例保留于
organization/contracts/pending_work_v1/ 作历史依据。实现：lp_sync/pending_work.py 与
lp_sync/pending_work_events.py；控制台端点同 v1 四个（cloud_board/api_console.py），
GET events 新增 retention 保留窗口字段。样例：snapshot_sample.json、
events_sample.json（生产真实只读构建）。

## 相对 v1 的语义修订（逐条）

1. 依赖评估复用 items_registry/dependency_policy.py 的 unmet_dependencies 实际
   显式交付判定：delivery_dependencies 中未交付前置构成显式等待条件；已批准且
   delivery_version 与 evidence_ptr 与上游 pending_review 匹配的交付先行满足
   （未关闭不等于未满足）；撤回影响（delivery_impact.review_required）与授权
   门槛保持。
2. 内核 last_error 短语降为候选原因：依赖类经 dependency_policy 当前复核、容量
   类经当前持槽复核、冲突类经 lane_workers.check_parallel_admission 无副作用
   复算；复核不符即判过期（superseded_stale_reason 注明）并按当前事实重分类。
   不可复核类短语保留原句，有效性随来源新鲜度降级。
3. 来源新鲜度：kernel_state.last_tick_at 超过 48 小时或不可读时评估与容量降级
   （dispatchable=null 不报全部可派），completeness=partial 并注明；
   sample_validity 以最旧来源时刻为锚（source_anchor），重新生成快照不续期。
4. 容量三源分账：by_lane.occupancy_sources = {kernel_slots,
   cloud_claims_unreconciled}；云端已认领但本地无对应活跃运行的线保守不报可派
   （dispatchable=null，degraded_reasons 说明）；global 在任一线降级时同样保守。
   reserved 恒 null 且 reservation_policy=not_set（同 v1）。
5. 事件事实性：claim_confirmed 仅在任务非 test 通道、已派发、且云端事项
   claim_session_id 与任务会话一致（状态 in_progress）时生成；不一致或来源不可读
   生成 result_unknown 保留未知。run_started 须有会话与工位在案的真实运行记录
   （或内核账本 run_started 事实）；仅受理或本地相位不构成开工。test 通道任务
   不进生产因果流。
6. 因果按工位跨事项：同工位（lane+worker_id）slot_released 与其后跨事项
   dispatch_selected 互为因果（causes/triggers，relation=
   same_worker_previous_item_release / same_worker_next_item_selection）；
   同一释放后同事项只首次选择建立因果（重试不重复建）；同事项时间邻接、换工位、
   两工位交错不建立因果。
7. 上行分批完整：push_once 维护持久已确认游标（pending_work_data/
   push_state.json），按 after_cursor 增量分批（批 500）循环至发完；失败或未知
   回执不推进游标并计入 failures；来源 next_cursor 低于已确认游标即报漏段；
   返回逐批报告（after/count/first/last/response）。GET events 附 retention
   {window_events:2000, earliest_cursor_kept}：服务端保留窗口淘汰的早段事件
   可被消费端检测，不把高游标冒充之前全部送达。

## 其余字段

快照身份、来源版本、范围排除、四类分类、needs_verification（KIND_UNKNOWN、
CLAIM_RUN_SESSION_MISMATCH、CLAIM_WITHOUT_RUN、RUN_WITHOUT_CLAIM、
MULTIPLE_ACTIVE_RUNS、STATUS_UNCLASSIFIED 等）、事件类型与游标去重语义同 v1
（见 ../pending_work_v1/README.md）；分类与容量语义以上述修订为准。
