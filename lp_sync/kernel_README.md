# 常驻编排内核与生产执行设施

更新：2026-10-13，Beth 集成；I-178、I-205、I-206、I-208。正式状态与工程验收分别核对，运行在线本身不是验收通过。

## 结构与启动

```text
Windows 任务计划服务（当前用户，无窗口；登录启动，每分钟补起）
  ├─ host_service.py：持久请求、会话归属、各 L 独立 app-server
  │    ├─ L3 app-server / ZCode CLI → L3 模型会话及工具
  │    └─ L5 app-server / ZCode CLI → L5 模型会话及工具
  ├─ orchestrator_kernel.py：唤醒、任务准入、队列、结果、恢复
  ├─ sync_agent.py：采集、70% 守门、结构化交接
  └─ luna_consumer.py：审核集合包准备，默认不发送聊天消息
```

内核是规则驱动 Python 服务，执行器调用模型。宿主 NDJSON 端口 127.0.0.1:58771 不是网页；58779 是内核单实例互斥端口。另有持久会话归属和每线锁，同 session 的所有管理动作由唯一归属执行器办理。SQLite WAL 保障数据库事务，不防止两个模型同时执行同一会话。

生产安装入口为 `install_runtime_tasks.ps1`（兼容入口 `svc_task_install.bat install`）。先停新派发、保存状态、确认维护范围；安装器拒绝替换运行中任务。不要从施工会话命令启动常驻宿主或用旧 Startup 批处理另起第二套。故障演练用独立状态、队列、端口及自建进程；生产拒绝 kill_child。

## 经理提交任务

把 JSON 原子写入 `lp_sync/kernel_state/inbox/<request_id>.json`，先临时文件后改名：

```json
{
  "schema_version": 1,
  "kind": "task_submit",
  "request_id": "kt-20261013-unique001",
  "item_id": "I-xxx",
  "lane": "L3",
  "title": "I-xxx 已批准任务标题",
  "message": "明确施工范围、交付及证据要求",
  "approved_by": "Beth",
  "reviewer": "beth",
  "task_kind": "task"
}
```

批准字段不能代替云端已批准事项。内核从桥读取正式任务线，在执行槽准入时刷新状态、依赖、认领 session 与撤回影响。task 为生产通道；test 为已授权隔离通道，不接管生产认领。同号必须同内容，同事项在办重复入队拒绝留痕。桥请求号最多 64 字符，内部派生长号稳定摘要缩短。

`lane_queues.json` 保存每 L 顺序、当前项、等待数与原因。pending_review 或真实 blocked 结果落实后释放执行槽，下一可执行项自动派发；未知回执占位对账。open 总数不是内核队列长度，未派工事项不会自动认领。

## 执行器提交结果

派发简报含本次 run_id。施工完成提交 pending_review，多个证据文件提供一个总清单路径：

```json
{
  "schema_version": 1,
  "kind": "result_submit",
  "result_id": "result-unique001",
  "run_id": "消息中本次真实运行号",
  "item_id": "I-xxx",
  "outcome": "pending_review",
  "delivery_version": "v1",
  "evidence_ptr": "outputs/实际总交付清单.json",
  "reviewer": "beth",
  "unmet": ["明确尚待验收或补证的条目"],
  "evidence": "交付说明与可定位证据"
}
```

原子写入 inbox，读取 `outbox/<result_id>.ack.json`。非法字段不结载运行。云端写失败有明确记录与审核人通知，缓存排队不得充当已落实。另支持 blocked（真实原因、缺项和机主说明）、evidence_appended（阶段证据）、closed（既有逐条判据及关闭权限）。pending_review 不生成判据通过。

## 持久化与恢复

- state.json、ledger.json 保存请求、运行、尝试、结果和恢复；请求与运行号先于副作用保存。
- execution_store.py 保存会话归属、输入号及实际 turn 活动；恢复不重复装载替换活跃原会话。
- 云端副作用与 operation_receipt 同事务保存；回执丢失读取原请求结果，不能按事项当前状态猜旧请求成功。
- 取消和子进程退出记录 session/item/run/executor；核实未完成后原 session 自动续跑，已完成不重做、未知对账，额度限制保存下次可尝试时间。
- control.json halt 停新派发，仍收结果和对账，不杀进程。
- 存活查询使用 Windows GetExitCodeProcess，不发控制台信号；锁不因年龄偷走活跃持有人。

默认依赖要求 accepted。提前使用交付须正式登记所需版本、证据及授权；撤回后停受影响的新派发，保留已有下游来源和复核标记。

已获机主明确批准的有限先行施工使用云端事项的 `partial_start` 文字列表，保留 `depends_on`、原判据与审核人。内核在实际派发前重新核对该字段，把完整范围和未清依赖写入简报；没有登记范围仍等待依赖，范围格式无效或路线不符仍拒绝。本次仅完成先行部分时提交 `evidence_appended` 阶段证据，不据此申报整项通过。登记、队列迁移、正式认领和真实工具开工分别保存回读证据。

## 审核通知

正常结果集合最多等待 10 秒、25 个事件；紧急异常立即单独准备。准备、发送尝试、实际回执、逐事件 ACK 分开保存。默认只准备；实际发送需要机主明确授权及真实通道。回执未知不整批重发。详见 [Luna 轮询指南](../organization/orchestrator_polling_guide.md)。

## 验证与限制

独立消费者检查 45/45、实际进程故障演练 25/25 通过。真实三任务 FIFO、L3/L5 隔离与原会话续跑、系统自动补起、原子云端回执恢复、真实三事件送达均有证据。有限隔离作业不替代科研业务判据，仍需指定 reviewer 验收。索引见 [WORK.md](../logs/hq_intake/beth_orchestrator_completion_20261013/WORK.md)。

当前用户登录后运行；Windows 关机、休眠和退出登录会影响本地执行。直接向会话发消息但未入内核的任务，不保证生成队列和通知。可写收件箱是获授权本地操作边界，尚未实现不同系统用户之间细粒度授权隔离。
