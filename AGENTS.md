
<!-- isaac-manager-comms -->
## Isaac 项目岗位联络

仅在担任 isaac_flapper_sim 岗位且联络该项目成员时适用：本项目岗位联络优先用 manager_comms.send_manager_message，不先搜索会话列表获取收件地址。固定 sender、to、purpose、summary、action，已有机主授权保存到 authorization。摘要80字、动作80字、补充100字，合计240字；审计状态串、会话号和执行细节留后台。purpose仅执行任务或机主信息移交；执行任务必须写现在可做的具体动作，等待、待命、收到不算动作。机主信息移交须引用机主明确要求转交的原话，action固定为无需操作，无需回复。普通进度不发送。接收任务直接执行，返回结果或具体阻碍；接收信息移交无需回复，不发送收到或复述。工具缺失报告接入问题，不能把脚本存在当成工具可见；queued不等于送达。
完整规范：G:/Playground/isaac_flapper_sim/organization/communication/README.md。岗位职责仍读各自现行卡；本规则不改变其他项目工作。
