# tools/checks/ — 判据脚本目录（I-116）

> 接口契约以 L3 冻结文本为准；冻结前本目录与执行器为本体，接口面
> 刻意收窄便于契约落地时适配。

## 约定（本体版）

- 每个事项的判据脚本放本目录，命名建议 `<事项编号小写>_<含义>.py`
  或 `<含义>.py`；注册后事项字段 check_script 指向
  `tools/checks/<名称>.py`。
- 脚本以独立进程运行，工作目录为仓库根；**退出码零=通过，非零、
  超时（默认三百秒）、异常=不通过**。
- 脚本只做只读检查与自身产物断言，禁止写账本、禁止联网改状态。

## 执行器（executor.py）

    python tools/checks/executor.py run <脚本> [--timeout N]   # 直接运行
    python tools/checks/executor.py register <事项> <脚本>      # 注册到事项
    python tools/checks/executor.py verify <事项>               # 按注册脚本运行并落证据
    python tools/checks/executor.py close <事项> --evidence ... # verify 通过后走判据闸关闭
    python tools/checks/executor.py list                        # 列出全部已注册事项

## 判据闸（服务端）

挂了脚本的事项关闭时，云端关闭接口要求请求携带三十分钟内的新鲜
check_result（脚本匹配且通过），否则拒绝；未挂脚本的事项关闭流程
与历史逐字一致。

## 当前脚本

- i116_selfcheck.py — 执行器与四规则自检（本事项 I-116 注册用）
- demo_pass.py / demo_fail.py / demo_timeout.py — 三规则演示件
