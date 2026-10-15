# I-201 动画构型第二版（修复轮）独立复验说明（复验人须未参与方案与施工）

## 一、可机检部分（约三分钟）

在 eagle_shell/l8_i201_animv2/ 目录运行：

    G:/Playground/PartField/venv/Scripts/python.exe verify_l8_i201_animv2.py

脚本重读导出模型并断言：来源哈希一致；蒙皮骨骼编号为局部编号且全部有效、
权重和为一；动画四元数归一；抽样帧接缝最大间距低于十微米；抽样帧过渡区外
边长变化低于百分之一；自由度字段完整。任何一项不过即断言失败。

## 二、人工目检部分（约十五分钟）

1. 用任一 glb 查看器（如系统内 three.js 页面或 Blender）打开
   output/eaglex_anim_config_v2.glb 播放动画，核对：整尾偏航与滚转时垂尾
   与两平尾共同运动；平尾开合时中央垂尾不动；翼尖上反下折绕弦向铰线；
   全程三处接缝无张开。
2. 播放 output/eaglex_animv2_preview.mp4 与 clips/ 十段，核对分段与底部
   标注一致（含联动开合三自由度候选与独立开合四自由度对照的标注差异）。
3. 打开 output/wing_root_axis_fix.png 与两张翼根近景：核对扭转轴候选位于
   前缘后约五分之一弦长、零位翼根连接边埋入机身轮廓。
4. 打开 output/edge_stretch_shrink.png 与 seam_gap_comparison.png：核对
   报告数值与图示一致；旧版毫米级间隙与本版纳米级间隙的对比。
5. 抽查 anim_config_v2_report.json 的 exported_model_edge_verification：
   过渡区内峰值边的位置、帧与分桶数值同图证。

## 三、结论记录

复验人把机检输出与目检结论逐条挂回 I-201，注明身份与时间。过渡区内剩余
百分之二至四的变化为本轮已知未全解项，复验结论应单列判定。机主的构型
认可不代替数据、导出与上线检查；本说明与施工自检均不构成独立验收。
