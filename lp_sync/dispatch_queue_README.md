# 派发请求文件队列与外部智能体文件桥使用说明（I-175、I-177）

日期：2026-10-12。施工：L3。用途：其一，指挥部不经任何命令确认，写一个文件即可派活；其二，沙箱外援不经网络，写一个文件即可请宿主代执行账本写操作。两条业务共用同一文件桥通道（同一目录、同一轮询线程、同一台账）。

## 一、通道形态

指挥部或外援把请求写成 JSON 文件，放入：

```
G:\Playground\isaac_flapper_sim\lp_sync\dispatch_queue\incoming\
```

文件名必须是 `<request_id>.json`（例如 `req-20261012-0001.json`）。
宿主服务常驻轮询线程每 2 秒扫一轮（队列心跳），文件落盘后两轮心跳内
自动认领并派发，派发自带服务侧回读核验，结果写回 `done/` 目录并在
`dispatch_queue\ledger.json` 台账留痕。全程不经 Kimi 侧命令确认，也
不需要运行 tools/dispatch_session.mjs。

写入方式必须原子：先写成别的临时名（例如 `xxx.writing`），写完把
文件改名成 `<request_id>.json`。直接写目标名时，服务按文件静默期
（最后修改时间距今满 1 秒）兜底，仍建议用改名法。外援直接用
`lp_sync\bridge_client.py`（零网络纯文件客户端，不导入任何网络
模块）的 submit 与 wait 子命令。

## 二、派发请求格式（I-175 冻结）

```json
{
  "schema_version": 1,
  "request_id": "req-20261012-0001",
  "op": "dispatch_new",
  "title": "派活标题",
  "message": "简报正文（必填非空）",
  "lane": "L3",
  "item_id": "I-175",
  "kind": "task",
  "sid": "sess_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "verify": true,
  "max_attempts": 3,
  "retry_delay_s": 15
}
```

字段规则：

| 字段 | 必填 | 说明 |
|---|---|---|
| schema_version | 是 | 固定为 1 |
| request_id | 是 | 全局唯一幂等键，4 至 64 位字母数字点横下划线；同一编号重复递交只派发一次 |
| op | 是 | `dispatch_new` 开新会话或复用常驻会话；`dispatch_resume` 向指定会话发送 |
| message | 是 | 简报正文，非空 |
| title | 否 | 仅 dispatch_new 用，缺省为 指挥部派活 |
| lane | 否 | 队名（如 L3），dispatch_new 走一队一驻判定 |
| item_id | 否 | 事项号，派发简报附带会话归属自报指令 |
| kind | 否 | task 或 test，缺省 task；test 走测试通道不登记不入驻 |
| sid | 条件 | dispatch_resume 必填，sess_ 开头 |
| verify | 否 | 缺省 true，回读核验开关 |
| max_attempts | 否 | 失败重试上限，缺省 3 |
| retry_delay_s | 否 | 重试间隔秒数，缺省 15 |

## 三、账本写请求格式（I-177 冻结，外援用）

```json
{
  "schema_version": 1,
  "request_id": "br-20261012-0001",
  "op": "ledger_write",
  "agent_id": "codex-sandbox-01",
  "ledger": {
    "action": "claim",
    "args": {"item_id": "I-xxx", "worker": "L3队", "session_id": "sess_..."}
  },
  "max_attempts": 3,
  "retry_delay_s": 15
}
```

| 动作 action | 必填 args | 说明 |
|---|---|---|
| claim | item_id、worker | 认领事项，可选 session_id、lease_hours |
| release | item_id、worker | 释放认领 |
| append_evidence | item_id、evidence | 追加单条证据字符串 |
| close | item_id、evidence、conclusion、criterion_check、owner_note | 收工关单 |
| block | item_id、reason、blocker_type、needs、evidence、owner_note | 申报卡住 |
| set_criterion | item_id、index、verified | 判据子项核验标记 |
| owner_reply | item_id、text | 机主答复 |

行为约定：宿主代执行时复用 items_registry 登记库的全部校验（机主说
明字数、结论措辞、判据对照表形态等，不合格即定性拒绝）；结果回写
`done\<request_id>.result.json` 的 result 字段含 bridge_ok、deferred、
ledger_result、error。断网时操作转入本地断网队列由同步代理恢复后按
序重放，结果标 deferred 为真；云端明确拒绝或校验不过为定性失败，不
重试，直接进 failed/ 留痕；其余异常可重试。外援零网络权限：联网动
作全部由宿主完成，bridge_client.py 不导入任何网络模块。

## 四、结果与留痕

- 成功：请求文件与 `.result.json` 结果进入 `done\`。result 含会话号、
  通道、回读核验结果、认领与完成时间、派发耗时。
- 失败：先进入 `retry\`（附 `.meta.json` 记录次数、下次重试时间、
  最近错误），重试耗尽进入 `failed\`（附最终 `.result.json`）。
- 台账：`dispatch_queue\ledger.json` 只追加不改写，事件序列为
  received → claimed → dispatch_start → dispatch_result → completed，
  失败路径为 … → retry_scheduled → retry_due → …，耗尽为 failed_final；
  重复递交记 duplicate_skipped；重启恢复记 recovered_after_restart；
  坏文件进 `invalid\` 记 invalid。
- 查询：向宿主服务端口 127.0.0.1:58771 发送
  `{"op":"queue_status"}`（逐行 JSON 套接字协议）返回目录计数与最近
  二十条台账事件；程序化投递可发 `{"op":"queue_submit", ...请求字段}`。

## 五、纪律

- request_id 是防重复派发与防重复写账的唯一凭据，指挥部与外援必须保证一号一用。
- kind 为 test 的派发用于验收与演练，不入驻注册表、不登记任务索引。
- 文件桥对能写进本机队列目录的进程开放账本写权限，第一版以 agent_id
  自报身份入台账留痕，不做更强鉴别；请求身份与授权表属 I-178 范围。
- 派发结果未知的严格对账（消息已发但回执未收）属 I-178 常驻内核
  范围；本队列对重启中断的请求如实标记 recovered_after_restart 并
  转入重试通道。
