# -*- coding: utf-8 -*-
"""
probe_jforce_i009.py — I-009 判别探针：get_measured_joint_forces 崩溃条件定位
==========================================================================
背景（I-009）：2026-09-28 完整记录落地时，主仿真内探测
get_measured_joint_forces 触发原生堆损坏（0xc0000374，零行即崩，
logs/_run_fullrec_verify.log 第615行实录：step=4 render=True 步上崩），
接口弃用、jforce 列恒空。而 2026-09-19 的独立探针
（tools/probe_instant.py，本档案的对照原型）同一接口曾正常工作并测得
肩关节惯性力矩。两现场存在多处差异，本探针把差异拆成单变量轴。

判据先于数据（跑前写死，勿改）：
  每模式独立进程运行，进程末尾打印哨兵行 "PROBE-COMPLETE mode=X rows=N"。
  哨兵在（进程活到写完日志）＝该模式存活；哨兵缺席＝进程原生死亡
  （0xc0000374 类崩溃，Python 异常不可捕，与 0928 实录同族）。
  各模式判读：
    A 存活 → 对照组复现 0919 可用性；
    A 存活且 B 死 → 关节力上报开关（PhysxJointAPI.jointForceEnabled）
      是决定变量，修复路径＝主仿真启用该开关（交接 L2）；
    A 存活且 B 存活且 C/D 死 → 渲染管线交互是决定变量，
      获取途径＝仅在非渲染步读取；
    E 死 → 物理回调在场是决定变量，获取途径须换回调线程内查询；
    A 死 → 本版引擎（Isaac Sim 5.1.0）该接口在本资产上不可用，
      I-009 按引擎限制书面关闭；
    全存活 → 原崩溃由现场更大差异（多刚体/多视图/记录器并存）致，
      需第二轮加料探针，不得直接下"接口可用"结论。

模式表（单变量轴；E/F 于 0930 第一轮后改判据注记：jf_on 轴已被
tools/_min_jf_repro.py 确定结论为 CreateJointForceEnabledAttr 必死，与
渲染/回调无关，故 E/F 改为 jf_off 版本专测剩余两轴）：
  A  jf_on  + render恒False            对照组：0919 可用版复刻（死于开关创建）
  B  jf_off + render恒False            0919 实际配置（开关当年就没开成）→ 已存活
  C  jf_on  + render每4步True          死于开关创建（不另跑，min_repro 已给确定结论）
  D  jf_off + render每4步True          嫌疑二：无头渲染管线交互
  E  jf_off + render混合 + 物理回调施力  嫌疑三：回调在场（主仿真现场最小复刻）
  F  jf_off + render混合 + 回调 + 自由刚体×5 + 第二视图读姿  现场加料复刻

用法（每模式单独起进程，勿同进程连跑）：
  set PROBE_MODE=A && g:\\Playground\\isaac_sim_5.1.0\\python.bat tools/probe_jforce_i009.py
  （模式经环境变量传入：命令行参数会被 SimulationApp 转发给 kit 当作
  待打开文件名，kit 启动完成后即干净退出，实测坑，勿用 argv）
日志：logs/probe_jforce_i009_<mode>.txt
"""
import os
import sys
import math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MODE = os.environ.get("PROBE_MODE", "A").upper()
VALID = {"A", "B", "C", "D", "E", "F"}
if MODE not in VALID:
    raise SystemExit("用法: probe_jforce_i009.py <A|B|C|D|E|F>")
JF_ON = MODE in ("A", "C")                 # 关节力上报开关（确定结论：创建即死）
RENDER_MIX = MODE in ("D", "E", "F")       # 每4步 render=True
WITH_CB = MODE in ("E", "F")              # 物理回调在场
WITH_BODIES = MODE == "F"                 # 自由刚体+第二视图

LOG = os.path.join(ROOT, "logs", "probe_jforce_i009_%s.txt" % MODE)


def log(m):
    line = str(m)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


open(LOG, "w").close()
log("[probe_jforce_i009] mode=%s jf_on=%s render_mix=%s cb=%s bodies=%s"
    % (MODE, JF_ON, RENDER_MIX, WITH_CB, WITH_BODIES))

# 项目内模块必须在 SimulationApp 之前导入（cv2 的 pip config.py 劫持坑）
import config.sim_config as cfg
from isaacsim import SimulationApp
app = SimulationApp({"headless": True})

try:
    import numpy as np
    import omni.usd
    from isaacsim.core.api import World
    from isaacsim.core.utils.stage import add_reference_to_stage
    from isaacsim.core.prims import Articulation
    from isaacsim.core.prims import RigidPrim as RigidPrimView
    from pxr import UsdPhysics

    F_HZ = 2.5
    AMP_DEG = 35.0
    STEPS = 360  # 3 s @120 Hz

    USD = os.path.join(ROOT, "assets/flapper/fwav_15dof.usd").replace("\\", "/")
    omni.usd.get_context().new_stage()
    add_reference_to_stage(usd_path=USD, prim_path="/World/Test")
    stage = omni.usd.get_context().get_stage()
    n_rev = 0
    for prim in stage.Traverse():
        if prim.GetTypeName() == "PhysicsRevoluteJoint":
            n_rev += 1
            if not prim.HasAPI(UsdPhysics.DriveAPI, "angular"):
                UsdPhysics.DriveAPI.Apply(prim, "angular")
            d = UsdPhysics.DriveAPI.Get(prim, "angular")
            d.GetStiffnessAttr().Set(60.0)
            d.GetDampingAttr().Set(0.25)
    log("场景：revolute 关节 %d 个" % n_rev)

    # 质量等比（与 probe_instant/主仿真同式）
    items = []
    for prim in stage.Traverse():
        if str(prim.GetPath()).startswith("/World/Test") and prim.HasAPI(UsdPhysics.MassAPI):
            m = UsdPhysics.MassAPI(prim).GetMassAttr().Get()
            if m and m > 0:
                items.append((prim, float(m)))
    _tot = sum(m for _, m in items)
    for prim, m in items:
        UsdPhysics.MassAPI(prim).GetMassAttr().Set(m * cfg.MASS / _tot)
    log("质量等比 %.3f → %.3f kg（cfg.MASS）" % (_tot, cfg.MASS))

    # ── 判别轴一：关节力上报开关 ──
    if JF_ON:
        log("开关环节：准备 import PhysxSchema")
        from pxr import PhysxSchema as _PhysxSchema
        log("开关环节：import 完成")
        n_on = 0
        for prim in stage.Traverse():
            if prim.GetTypeName() == "PhysicsRevoluteJoint":
                api = _PhysxSchema.PhysxJointAPI.Apply(prim)
                api.CreateJointForceEnabledAttr(True)
                n_on += 1
                log("开关环节：已开 %s" % prim.GetPath())
        log("jointForceEnabled 已开 %d 个" % n_on)
    else:
        log("jointForceEnabled 保持默认（未开）")

    # ── 现场加料：自由刚体×5（浮标替身）──
    from pxr import UsdGeom, Gf
    _obs_paths = []
    if WITH_BODIES:
        UsdGeom.Xform.Define(stage, "/World/Obstacles")
        for k in range(5):
            p = "/World/Obstacles/B%d" % k
            UsdGeom.Xform.Define(stage, p)
            cube = UsdGeom.Cube.Define(stage, p + "/box")
            cube.AddTranslateOp().Set(Gf.Vec3f(10.0 + k, 5.0 * k, 2.0))
            cube.AddScaleOp().Set(Gf.Vec3f(0.3, 0.3, 0.3))
            UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
            UsdPhysics.RigidBodyAPI.Apply(cube.GetPrim())
            UsdPhysics.MassAPI.Apply(cube.GetPrim()).GetMassAttr().Set(0.5)
            _obs_paths.append(p + "/box")
        log("自由刚体 5 个已入场景：%s" % ",".join(_obs_paths))

    world = World(stage_units_in_meters=1.0,
                  physics_dt=1 / 120.0, rendering_dt=1 / 120.0)
    art = Articulation("/World/Test")
    world.scene.add(art)
    # 施力目标视图必须在 reset 前构造并入 scene（reset 后构造会静默杀进程，
    # 与 F 障碍物视图同序）；根刚体=Test 直系子级中带 RigidBodyAPI 的首件
    _force_view = None
    if WITH_CB:
        _body_paths = [str(p.GetPath()) for p in stage.Traverse()
                       if str(p.GetPath()).startswith("/World/Test/")
                       and p.HasAPI(UsdPhysics.RigidBodyAPI)
                       and str(p.GetParent().GetPath()) == "/World/Test"]
        if _body_paths:
            _force_view = RigidPrimView(prim_paths_expr=_body_paths[0])
            world.scene.add(_force_view)
        log("施力目标根刚体：%s" % (_body_paths[0] if _body_paths else "未找到"))
    if WITH_BODIES:
        # 视图用显式路径列表（通配符版在 reset 处挂起，改判据注记）
        # F2 变体：REPRO 环境变量 OBS_VIEW=0 时刚体在场但不建第二视图
        if os.environ.get("OBS_VIEW", "1") == "1":
            obs_view = RigidPrimView(prim_paths_expr=_obs_paths)
            world.scene.add(obs_view)
            log("障碍视图已注册入 scene")
        else:
            obs_view = None
            log("障碍视图跳过（OBS_VIEW=0，刚体在场无视图）")
    world.reset()
    log("world.reset() 完成")
    names = list(art.joint_names)
    iFL, iFR = names.index("flap_L"), names.index("flap_R")
    iTL, iTR = names.index("twist_L"), names.index("twist_R")
    log("关节 %d 个：%s" % (len(names), ",".join(names)))

    # ── 判别轴三：物理回调在场（主仿真 flapper_aero 最小替身）──
    if WITH_CB:
        _cb_state = {"fires": 0}

        def probe_cb(dt):
            _cb_state["fires"] += 1
            try:
                _force_view.apply_forces_and_torques_at_pos(
                    forces=np.asarray([[0.0, 0.0, 0.5]], dtype=np.float32),
                    is_global=True)
            except Exception as e:
                _cb_state["apply_err"] = repr(e)
        world.add_physics_callback("probe_cb", probe_cb)
        log("物理回调 probe_cb 已注册（施力经根刚体"
            " RigidPrim.apply_forces_and_torques_at_pos，与主仿真同款）")

    def set_targets(t):
        phi = math.radians(AMP_DEG) * math.sin(2 * math.pi * F_HZ * t)
        vec = [0.0] * len(names)
        vec[iFL] = phi
        vec[iFR] = -phi
        vec[iTL] = vec[iTR] = 0.3  # 常值扭转偏置，令约束力非零
        art.set_joint_position_targets(
            positions=np.asarray([vec], dtype=np.float32), joint_names=names)

    def pin():
        pos, quat = art.get_world_poses()
        art.set_world_poses(positions=np.asarray(pos, dtype=np.float32),
                            orientations=np.asarray(quat, dtype=np.float32))
        try:
            art.set_velocities(np.zeros((1, 6), dtype=np.float32))
        except Exception:
            pass

    # ── 主循环：每步读 get_measured_joint_forces（判别对象本体）──
    rows = 0
    norms = []
    last_shape = None
    for i in range(STEPS):
        _tr = i < 3  # 前三步逐语句追踪（F2 死点定位）
        t = i / 120.0
        if _tr:
            log("iter %d：set_targets 前" % i)
        set_targets(t)
        if _tr:
            log("iter %d：pin 前" % i)
        pin()
        render = RENDER_MIX and (i % 4 == 0)
        if _tr:
            log("iter %d：step 前 render=%s" % (i, render))
        world.step(render=render)
        if _tr:
            log("iter %d：step 后，读 jforce 前" % i)
        jf = np.asarray(art.get_measured_joint_forces())
        last_shape = tuple(jf.shape)
        if jf.ndim == 3 and jf.shape[-1] == 6:
            tq = jf[0, names.index("flap_L") + 1, 3:6]
            norms.append(float(np.linalg.norm(tq)))
            rows += 1
        if WITH_BODIES and obs_view is not None:
            _ = obs_view.get_world_poses()  # 第二视图读姿（在场即够）
        if i % 120 == 0:
            log("step %d shape=%s 走到" % (i, last_shape))

    arr = np.asarray(norms)
    log("读取成功 %d/%d 步，shape=%s，flap_L 力矩模 min/mean/max="
        "%.3f/%.3f/%.3f N·m，有限值占比 %.2f"
        % (rows, STEPS, last_shape, arr.min(), arr.mean(), arr.max(),
           float(np.isfinite(arr).mean())))
    if WITH_CB:
        log("回调触发 %d 次（预期≈步数），施力异常=%s"
            % (_cb_state["fires"], _cb_state.get("apply_err", "无")))
    # 哨兵行：进程存活到这里的证明（判据见文件头）
    log("PROBE-COMPLETE mode=%s rows=%d" % (MODE, rows))
finally:
    try:
        app.close()
    except Exception:
        pass
