"""run_eagle_shell.py — eagle_shell 第三步：零侵入运行时包装器（不改任何既有代码）。

用法：isaac_sim python.bat eagle_shell/run_eagle_shell.py（建议经 eagle_shell/启动_鹰壳.bat）。
机制：
  1. 注册 meta-path 钩子，在 main_sim 首次导入 modules.flapper_loader 执行完毕的瞬间打包装补丁；
  2. 包装 load_flapper：原加载完成后，把 eagle_shell.usd 四个分件以引用方式挂到对应连杆下，
     并把被替换连杆的原视觉件在会话层设为不可见（碰撞体不动、既有文件零写入）；
  3. 包装 setup_aero：第二次调用（world.reset 后重新注册回调的时机）创建追拍相机与渲染回调，
     保存三张验证截图到 eagle_shell/out/；EAGLE_SHELL_AUTOSTOP=1 时拍完即退出（验证模式专用）。
开关：EAGLE_SHELL=0 只跑原版不挂壳；EAGLE_SHELL_AUTOSTOP=1 自动停（默认关，正常仿真不受影响）。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
MANIFEST = os.path.join(HERE, "out", "bind_manifest.json")
SHOT_DIR = os.path.join(HERE, "out")

_state = {"aero_calls": 0, "shots": 0, "cam": None}

# 需隐藏旧视觉件的连杆（壳层已覆盖或属翼链：外翼/翼根盒体不藏会以折叠动作叠在壳翼下方，
# 观感为主翼拉伸+绕 z 轴旋转；I-114 起尾链已有壳件：尾撑/垂尾/左右舵面由鹰壳尾翼件
# 替代，原红黑视觉件一并隐藏）
HIDE_LINKS = ("base_link", "flapconn_L", "flapconn_R",
              "innerwing_L", "innerwing_R", "outerwing_L", "outerwing_R",
              "tail_base", "tailpanel_L", "tailpanel_R")


def _patch_loader(mod):
    orig_load = mod.load_flapper

    def load_flapper(world):
        r = orig_load(world)
        try:
            if os.environ.get("EAGLE_SHELL", "1") != "0":
                _attach_shell()
        except Exception as e:
            print(f"[eagle_shell] attach FAILED: {e}", flush=True)
            raise
        return r

    mod.load_flapper = load_flapper

    orig_setup = mod.setup_aero

    def setup_aero(world, flapper_view=None):
        _state["aero_calls"] += 1
        r = orig_setup(world, flapper_view=flapper_view)
        if _state["aero_calls"] >= 2 and os.environ.get("EAGLE_SHELL", "1") != "0":
            try:
                _setup_shots(world)
            except Exception as e:
                print(f"[eagle_shell] camera setup failed: {e}", flush=True)
        return r

    mod.setup_aero = setup_aero

    orig_sensors = mod.setup_sensors

    def setup_sensors(world, record_data=False):
        r = orig_sensors(world, record_data=record_data)
        if os.environ.get("EAGLE_SHELL", "1") != "0":
            try:
                _setup_shots(world)
            except Exception as e:
                print(f"[eagle_shell] camera setup failed: {e}", flush=True)
        return r

    mod.setup_sensors = setup_sensors
    print("[eagle_shell] loader patched (load_flapper + setup_aero + setup_sensors wrapped)", flush=True)


def _attach_shell():
    import omni.usd
    from pxr import Usd, UsdGeom

    with open(MANIFEST, "r", encoding="utf-8") as f:
        mf = json.load(f)
    stage = omni.usd.get_context().get_stage()

    links = {}
    for prim in stage.Traverse():
        p = str(prim.GetPath())
        if not p.startswith("/World/Flapper/MAV"):
            continue
        n = prim.GetName()
        if n in ("base_link", "innerwing_L", "innerwing_R",
                 "tail_base", "tailpanel_L", "tailpanel_R") and n not in links:
            links[n] = p
    missing = {"base_link", "innerwing_L", "innerwing_R",
               "tail_base", "tailpanel_L", "tailpanel_R"} - set(links)
    if missing:
        raise RuntimeError(f"link prims not found: {missing}")

    hide_paths = dict(links)
    for prim in stage.Traverse():
        p = str(prim.GetPath())
        if not p.startswith("/World/Flapper/MAV"):
            continue
        n = prim.GetName()
        if n in HIDE_LINKS and n not in hide_paths:
            hide_paths[n] = p

    for part in mf["parts"]:
        host = stage.DefinePrim(links[part["link"]] + "/EAGLE_" + part["name"], "Xform")
        ok = host.GetReferences().AddReference(mf["usd"], part["prim_path"])
        print(f"[eagle_shell] attach {part['name']} -> {part['link']} ref={ok}", flush=True)

    # 隐藏被替换连杆的原视觉件（仅会话层，不写任何文件；碰撞体不受影响）
    hidden = 0
    for lpath in hide_paths.values():
        for prim in stage.Traverse():
            p = str(prim.GetPath())
            if not p.startswith(lpath + "/") or "/EAGLE_" in p:
                continue
            seg = p[len(lpath) + 1:].split("/")[0].lower()
            if seg in ("visual", "visuals"):
                UsdGeom.Imageable(prim).GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
                hidden += 1
    print(f"[eagle_shell] original visuals hidden: {hidden} (links: {', '.join(hide_paths)})", flush=True)

    # 数值对位核验（零位关节，机构初始位置 0,0,1.5）
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_], False)
    lines = []
    bboxes = {}
    for part in mf["parts"]:
        prim = stage.GetPrimAtPath(links[part["link"]] + "/EAGLE_" + part["name"])
        bb = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        lo, hi = bb.GetMin(), bb.GetMax()
        bboxes[part["name"]] = (lo, hi)
        lines.append(f"{part['name']}: x[{lo[0]:+.2f},{hi[0]:+.2f}] "
                     f"y[{lo[1]:+.2f},{hi[1]:+.2f}] z[{lo[2]:+.2f},{hi[2]:+.2f}]")
        if "wing" in part["name"]:
            # 读回诊断：宿主合成矩阵 + 网格点世界系 z 统计（定位组合层不对称）
            try:
                xq = UsdGeom.Xformable(prim)
                m = xq.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                mesh_prim = stage.GetPrimAtPath(str(prim.GetPath()) + "/geom")
                pts = UsdGeom.Mesh(mesh_prim).GetPointsAttr().Get()
                import numpy as _np
                P = _np.array([[p[0], p[1], p[2], 1.0] for p in pts])
                W = (_np.array(m).reshape(4, 4) @ P.T).T
                lines.append(f"{part['name']} diag: L2W[2,:]={_np.round(_np.array(m).reshape(4,4)[2,:],4)} "
                             f"world_z[min,mean,max]={W[:,2].min():.3f},{W[:,2].mean():.3f},{W[:,2].max():.3f} "
                             f"world_y[min,max]={W[:,1].min():.3f},{W[:,1].max():.3f}")
            except Exception as e:
                lines.append(f"{part['name']} diag failed: {e}")
    # 左右翼镜像对称核验：右翼 y 取反后与左翼各轴范围应一致
    if "eagle_wing_L" in bboxes and "eagle_wing_R" in bboxes:
        ll, lh = bboxes["eagle_wing_L"]
        rl, rh = bboxes["eagle_wing_R"]
        def _flip_y(lo, hi):  # 镜像比较须先取负：左翼 [a,b] 应等于右翼 [−c,−d] 的镜像
            return (-hi[1], -lo[1])
        dx = (abs(ll[0] - rl[0]) + abs(lh[0] - rh[0])) / 2
        dy = (abs(_flip_y(ll, lh)[0] - (rl[1], rh[1])[0]) + abs(_flip_y(ll, lh)[1] - (rl[1], rh[1])[1])) / 2
        dz = (abs(ll[2] - rl[2]) + abs(lh[2] - rh[2])) / 2
        lines.append(f"wing-mirror-check: mean|dx|={dx:.3f} mean|dy|={dy:.3f} mean|dz|={dz:.3f} (0=perfect)")
    report = "\n".join("[eagle_shell][align-check] " + l for l in lines)
    print(report, flush=True)
    with open(os.path.join(SHOT_DIR, "align_check.txt"), "w", encoding="utf-8") as f:
        f.write(report + "\n")


def _aim_camera(cam_path, pos, tgt):
    """直接写相机 prim 变换为看准目标的姿态：USD 相机看局部 -Z、上 +Y。
    本版 Camera 构造无 target 参数，默认朝向不指向飞机，09-29 起三张截图
    实拍为空景，本函数为其修复。"""
    import numpy as np
    import omni.usd
    from pxr import Gf, UsdGeom

    pos = np.asarray(pos, dtype=np.float64)
    tgt = np.asarray(tgt, dtype=np.float64)
    f = tgt - pos
    f = f / np.linalg.norm(f)
    up = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(f, up))) > 0.999:
        up = np.array([0.0, 1.0, 0.0])  # 视线与上向量近平行时换右向基准，避免叉积退化
    r = np.cross(f, up)
    r = r / np.linalg.norm(r)
    u = np.cross(r, f)
    stage = omni.usd.get_context().get_stage()
    prim = stage.GetPrimAtPath(cam_path)
    xf = UsdGeom.Xformable(prim)
    xf.ClearXformOpOrder()
    xf.AddTransformOp().Set(Gf.Matrix4d(
        float(r[0]), float(r[1]), float(r[2]), 0.0,
        float(u[0]), float(u[1]), float(u[2]), 0.0,
        float(-f[0]), float(-f[1]), float(-f[2]), 0.0,
        float(pos[0]), float(pos[1]), float(pos[2]), 1.0))
    print(f"[eagle_shell] camera aimed at {tgt.tolist()}", flush=True)


def _setup_shots(world):
    if _state["cam"] is not None:
        return
    import numpy as np
    from isaacsim.sensors.camera import Camera

    pos = np.array([0.0, 0.0, 4.2])
    tgt = np.array([0.0, 0.0, 1.5])
    try:
        cam = Camera("/World/EAGLE_ShotCam", position=pos, target=tgt,
                     resolution=(1280, 720))
    except TypeError:
        cam = Camera("/World/EAGLE_ShotCam", resolution=(1280, 720))
        cam.set_world_pose(position=pos)  # 兜底：默认朝向为俯视，仍可核验
    world.scene.add(cam)
    try:
        cam.initialize()
    except Exception:
        pass
    _aim_camera("/World/EAGLE_ShotCam", pos, tgt)
    _state["cam"] = cam
    marks = (0.5, 3.0, 6.0)

    def _shot(event_data):
        if _state["shots"] >= len(marks):
            return
        t = float(world.current_time)
        if t < marks[_state["shots"]]:
            return
        try:
            rgba = cam.get_rgba()
            from PIL import Image
            fn = os.path.join(SHOT_DIR, f"shot_{_state['shots']}_t{t:.1f}s.png")
            Image.fromarray(rgba.astype("uint8")).save(fn)
            print(f"[eagle_shell] shot saved: {fn}", flush=True)
        except Exception as e:
            print(f"[eagle_shell] shot failed: {e}", flush=True)
        _state["shots"] += 1
        if _state["shots"] >= len(marks):
            print("[eagle_shell] verification shots complete", flush=True)
            if os.environ.get("EAGLE_SHELL_AUTOSTOP", "0") == "1":
                sys.stdout.flush()
                os._exit(0)

    world.add_render_callback("eagle_shell_shots", _shot)
    print("[eagle_shell] shot camera ready", flush=True)

    def _watchdog(evt):
        # 无头模式渲染回调可能不触发：到时未拍满则按兜底路径收尾
        if _state["shots"] >= len(marks):
            world.remove_physics_callback("eagle_shell_watchdog")
            return
        if float(world.current_time) > 15.0:
            print(f"[eagle_shell] shots={_state['shots']}/3 by t=15s "
                  f"(headless render callback unavailable?)", flush=True)
            world.remove_physics_callback("eagle_shell_watchdog")
            if os.environ.get("EAGLE_SHELL_AUTOSTOP", "0") == "1":
                sys.stdout.flush()
                os._exit(0)

    world.add_physics_callback("eagle_shell_watchdog", _watchdog)


class _LoaderHook:
    """拦截 modules.flapper_loader 的导入：执行完成后立即打包装补丁（其余导入直通）。"""

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "modules.flapper_loader":
            return None
        from importlib.machinery import PathFinder
        spec = PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return None
        inner = spec.loader

        class _WrapLoader:
            def create_module(self, spec_):
                return inner.create_module(spec_)

            def exec_module(self, module):
                inner.exec_module(module)
                _patch_loader(module)

        spec.loader = _WrapLoader()
        return spec


def main():
    sys.path.insert(0, PROJ)
    os.chdir(PROJ)
    sys.meta_path.insert(0, _LoaderHook())
    print("[eagle_shell] wrapper active, launching main_sim ...", flush=True)
    runpy.run_path(os.path.join(PROJ, "main_sim.py"), run_name="__main__")


if __name__ == "__main__":
    import runpy
    main()
