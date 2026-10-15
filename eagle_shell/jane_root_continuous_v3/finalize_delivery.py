"""Bind the review package to the checked export; do not publish or close I-201."""
import datetime,hashlib,html,json
from pathlib import Path
import imageio.v2 as imageio
from PIL import Image
from surface_source import OUT,ROOT

read=lambda name:json.loads((OUT/name).read_text(encoding='utf-8'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
file=OUT/'eaglex_continuous_root_v3.glb';identity=sha(file)
motion=read('motion_checks.json');contact=read('intersection_checks.json');orientation=read('orientation_checks.json')
texture=read('texture_checks.json');loader=read('standard_loader_check.json');validation=read('khronos_validation.json');source=read('source_checks.json');build=read('build_report.json')
for report in [motion,contact,orientation,texture,loader,validation,build]:assert report['glb_sha256']==identity
assert not contact['new_nonadjacent_intersections_found']
assert not sum(r['negative_tangent_alignment_faces'] for r in orientation['rows'])
assert validation['issues']['numErrors']==validation['issues']['numWarnings']==0
assert loader['passed'] and texture['passed']
assert max(motion['maximum_seam_gap_m'].values())<2e-6
assert all(v['root_components']==1 and v['all_degrees_two'] for v in source['root_curves'].values())
reader=imageio.get_reader(OUT/'完整动作与双侧翼根.mp4');video_meta=reader.get_meta_data();frame=reader.get_data(35);reader.close()
Image.fromarray(frame).save(OUT/'动作视频抽帧.png')
peak=motion['actual_exported_edge_changes'];stretch=max(v['stretch'] for v in peak.values())*100;shrink=-min(v['shrink'] for v in peak.values())*100
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
report={'generated_at_utc':now,'glb_sha256':identity,'status':'visual_candidate_checks_passed_not_material_or_mechanism_qualified',
 'implemented_by':'Jane personally','published_to_parameter_lab_default':False,'independent_acceptance':False,
 'maximum_edge_stretch_percent':stretch,'maximum_edge_shrink_percent':shrink,'root_boundary_single_closed_curve_each_side':True,
 'deformation_end_abs_span_m':.24,'source_negative_half_reflected_including_body_and_tail':True,
 'entire_main_wing_rigid_incidence':False,'physical_material_qualified':False,'physics_modified':False,
 'geometry_and_motion_checks':['motion_checks.json','intersection_checks.json','orientation_checks.json','source_checks.json'],
 'format_and_texture_checks':['khronos_validation.json','standard_loader_check.json','texture_checks.json'],
 'intersection_test_scope':contact['scope'],'video':{'size':video_meta['size'],'duration_s':video_meta['duration']},
 'files':{p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in [file,OUT/'完整动作与双侧翼根.mp4',OUT/'迎角接缝修前修后.png',OUT/'八种姿态与真实接缝.png']}}
(OUT/'delivery_manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
body=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Eagle X 接缝修复候选的测量和使用范围</title>
<style>body{{font:17px/1.8 system-ui;max-width:1000px;padding:30px;margin:auto;color:#243543;background:#f5f7f9}}h1{{font-size:28px}}h2{{font-size:21px}}img,video{{width:100%}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #c1cbd3;padding:8px;text-align:left}}a{{color:#075f87}}code{{overflow-wrap:anywhere;font-size:14px}}.scope{{padding:16px;background:#fff1d5}}</style>
<h1>Eagle X 接缝修复候选的测量和使用范围</h1>
<p>Jane 亲自处理的完整装配候选。机身与主翼、主翼与翼尖在所查动作和帧间姿态中保持连接，左右主翼和翼尖对称。模型通过标准格式校验，并在浏览器里检查实际加载与动作。状态为可检查的数字外皮动作候选，参数实验室默认资产尚未替换。</p>
<p><a href="../review.html">打开可转动的完整模型</a> · <a href="完整动作与双侧翼根.mp4">完整动作与双侧翼根视频</a> · <a href="eaglex_continuous_root_v3.glb">下载模型</a></p>
<div class="scope"><b>最需要知道的取舍</b><p>这版采用连续外皮：弯扭从靠近机身的翼根逐渐分布到距机体中线约 24 厘米处，覆盖主翼和翼尖内段，外端达到原命令姿态。主翼因此具有分布弯扭，没有证明整片主翼可以作为刚体转动而保持同样接缝。保持了原命令幅度和外端位置，未通过缩小动作或整体移动外翼降低变形。</p><p>当前最大局部网格边长伸长 {stretch:.2f}%，缩短 {shrink:.2f}%。这是网格几何测量，不是材料许用值。真实蒙皮能否承受、真实根部机构如何过渡，仍需要结构依据。此前自设 5% 筛查线并未通过，不能据此认定实物机构或材料合格。</p></div>
<h2>实际改了什么</h2><p>从原始表面重新生成互相配对的机身和翼面边界。一侧翼根为一条连续闭合的三维边界曲线。分界距中线约 21.2–26.2 毫米，恢复到靠近机身的位置；每侧整片活动翼投影面积保留修前数值，没有再把分界推到 60 毫米处冻结大块主翼。</p><p>左右采用原模型右半作为共同基准，同时处理相邻机身和尾部外表面，再镜像生成左半，避免只换一片翼造成接口不配对。外形经过对称化，不能称两侧原始生成细节全部保留。沿用既有骨架与命令；翼根迎角仍绕展向轴改变，翼尖折叠沿用原弯曲链，没有新增独立作动通道。</p><p>所有表面来自原有三角面及其切分、镜像和变形，没有添加封口面。原始基色贴图像素保持一致，并修正导出时纹理坐标的上下方向。基色图透明度全为 255，材质为不透明；本次接缝修复不依靠透明遮罩或填色。</p>
<h2>实际检查结果</h2><table><tr><th>项目</th><th>结果与范围</th></tr>
<tr><td>黄色主翼投影面积</td><td>左右各 163.5139 平方厘米</td></tr><tr><td>绿色翼尖投影面积</td><td>左右各 151.6221 平方厘米</td></tr>
<tr><td>接缝</td><td>301 帧和 150 个翼面帧间姿态，机身与主翼及主翼与翼尖四处对应边界测得最大间距均为 0。该数值为文件读回计算结果，数值判据为 0.002 毫米。</td></tr>
<tr><td>左右动态对称</td><td>前 10 秒翼面动作及所查帧间姿态，对应点镜像误差为 0。</td></tr>
<tr><td>穿插</td><td>静止及 21 个关键/帧间姿态，机身与右翼三角面之间及右翼内部未发现非相邻面穿插。左翼采用精确镜像；这是选定姿态检查，未声称连续时间接触证明或全尾部接触验证。</td></tr>
<tr><td>表面方向</td><td>22 个姿态对照原表面切平面及翼尖骨架近似法向传递，没有负向对齐面。配合穿插检查使用。</td></tr>
<tr><td>伸缩与局部面积</td><td>最大边长伸长 {stretch:.2f}%，缩短 {shrink:.2f}%；最小三角面面积比主翼 0.710，翼尖 0.468，未把局部面积压缩省略不报。</td></tr>
<tr><td>单独改变迎角</td><td>整个主翼和翼尖的展向坐标误差小于 0.000001 毫米，没有额外展向外推。</td></tr>
<tr><td>静态闭合性</td><td>整件位置焊接检查：开放边 0，非流形边 0，退化面 0。</td></tr>
<tr><td>标准加载</td><td>glTF 校验错误 0、警告 0；标准 Three.js 加载器与独立读回程序的 16896 个位置样本最大差约 0.000037 毫米。格式校验仍有未使用对象的信息提示。</td></tr></table>
<h2>修前与修后</h2><p>左列为此前发布使用的动画资产，右列为本候选。上下分别为正、负迎角。图像逐个绘制实际导出三角面，未使用轮廓填色或补洞。有限分辨率下的个别像素以交互模型为准。</p><img src="迎角接缝修前修后.png" alt="修前和修后正负迎角真实接缝对照">
<h2>完整动作和双侧近景</h2><video controls preload="metadata" src="完整动作与双侧翼根.mp4"></video>
<h2>复验与回退</h2><p>所有实现和产物位于本目录的上一级。依次运行 surface_source、check_source、build_motion、verify_motion、check_intersections、check_surface_orientation、verify_texture、make_reference，再运行 standard_loader_check.mjs，最后生成画面及本交付清单。浏览器的 review.html 直接加载同一份模型。旧动画资产和冻结物理参数保留；回退使用原 l8_i201_animv2 资产。本候选没有独立验收或 I-201 关闭结论。</p>
<p>模型 SHA256：<code>{identity}</code>。检查材料：<a href="delivery_manifest.json">交付清单</a>、<a href="motion_checks.json">接缝与伸缩</a>、<a href="intersection_checks.json">穿插</a>、<a href="texture_checks.json">贴图</a>。生成时间 UTC：{html.escape(now)}。</p></html>'''
(OUT/'接缝检查说明.html').write_text(body,encoding='utf-8')
print(json.dumps({'sha256':identity,'stretch_percent':stretch,'shrink_percent':shrink,'checks_passed':True,'status':report['status'],'video':report['video']},ensure_ascii=False),flush=True)
