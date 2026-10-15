# I-208 第一阶段迁移清单（旧版到系统托管）

日期：2026-10-13。施工：L3。安装与维护切换由 Beth 执行（生产 PID 117856/116432 及其子进程不由施工会话触碰）。

## 新增件

| 文件 | 作用 |
|---|---|
| lp_sync/svc_entry.py | 无窗口系统托管入口（pythonw + runpy 加载真实模块，stdout/stderr 重定向显式日志文件） |
| lp_sync/svc_task_install.bat | Windows 任务计划安装/卸载（LP_HostSvc_I208 与 LP_KernelSvc_I208，onstart + SYSTEM + HIGHEST） |
| host_service.py 单例锁（.host_singleton.lock） | 任何子进程创建之前生效；第二实例退出码 4；持有者进程退出或心跳超 60 秒可被接管；存活查询用 OpenProcess 只读无信号 API |
| host_service.py Popen CREATE_NO_WINDOW | 子进程（node app-server）不再弹黑窗 |
| host_service.py kill_child 生产禁用 | 仅 LP_HOST_ISOLATED=1 时可用；生产调用返回明确拒绝 |
| host_service.py LP_HOST_PORT / LP_HOST_STATUS 环境变量 | 隔离宿主用独立端口与状态文件 |
| host_service.py maintenance 子命令 | mode=halt 停新派发保留断点；mode=run 恢复；mode=status 活动摘要 |

## Beth 迁移步骤（一次维护切换）

1. 向生产宿主发 `{"op":"maintenance","mode":"halt"}` 停新派发，等待在途请求处理完（`maintenance status` 的 pending_requests 为零且各队无活动运行）。
2. 核对各队活动与持久断点（kernel_state/state.json 与 dispatch_queue/ 各目录快照留存）。
3. 停止旧临时启动的宿主进程（PID 117856，Beth 从外部核对后办理）。
4. 运行 `lp_sync/svc_task_install.bat install`，再 `schtasks /run /tn LP_HostSvc_I208`（内核同理 LP_KernelSvc_I208）。
5. 验证：`netstat` 确认 58771 由 SYSTEM 进程监听；`{"op":"status"}` 返回 ok；`{"op":"maintenance","mode":"run"}` 恢复派发；`tasklist` 确认无 pythonw/node 挂在施工会话进程树下。
6. 旧启动文件夹的 lp_host_service.bat 条目在新任务验证后手动删除。

## 旧版已知问题与本阶段处置

- 旧宿主经 start/min + cmd 启动有控制台依赖 → pythonw 无控制台 + 子进程 CREATE_NO_WINDOW。
- 单实例仅靠端口绑定（竞态可双启）→ 根目录排他锁文件先行（子进程创建之前）。
- kill_child 生产开放 → 生产禁用（隔离环境声明后可用）。
- watcher 只重建子进程不恢复取消任务 → 第二阶段实现持久断点自动恢复。

## 退回修正（Beth 14:25 发布复核后补，2026-10-13）

- ensure_child 全调用链带执行键（_readback_hit/dispatch_new/dispatch_resume 按 roster 反查 sid 固定键；未知 sid 走 test 隔离键不进共享池；watcher 看护每线池）——真实子进程双线验证独立 pid 连续运行。
- turn 取消/终止事件在协议事件消费处捕获（_capture_turn_event 持久 turn_recovery 记录）；内核 reconcile_turn_recovery 三态对账（active 退回 submitted 自动续原会话一次有效运行；无关联记 pending_decision 并出 reviewer 通知；已完成不重做）——隔离验证三态全过。
- 单例锁改 64 位句柄声明（OpenProcess restype c_void_p）；活宿主超 300 秒仍拒绝、确认死持有者才清理、权限未知不接管——反例实测。
- svc_entry 改文本模式日志流（utf-8 行缓冲，print 正常）；任务计划脚本改当前用户（Anni InteractiveToken，不使用 SYSTEM——expanduser 依赖用户目录），宿主任务名与 Beth 已注册的 LP Production Host I208 一致做就地更新不装第二个同端口。
- QueueWorker 接入 maintenance 控制（halt 拒绝新派发处理、run 恢复——隔离验证双向过）。
