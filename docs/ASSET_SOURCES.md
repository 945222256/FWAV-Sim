# 扑翼仿真资产来源清单 (Asset Sources)

> 调查时间：2026-07-20
> 目标：扑翼 MAV 仿真 + 柱状障碍 + 森林场景，**长期可用**资产

---

## 核心结论

| 类别 | NVIDIA 原生 USD？ | 推荐方案 |
|---|---|---|
| 扑翼 MAV | ❌ 无（NVIDIA 只有四旋翼） | RCmags/ornithopter-hover (FreeCAD) + OmniDrones 物理骨架 |
| 柱状障碍 | ✅ Props/Shapes/ 有圆柱 | **直接用项目自带 obstacle_manager 程序化生成**（已实现） |
| 森林场景 | ⚠️ 只有 Rivermark 户外场 | joevento 程序化森林生成器 + OpenGameArt CC0 树木包 |

---

## 一、扑翼 MAV 资产

### 1. RCmags/ornithopter-hover ⭐ 最佳机械匹配
- URL: https://github.com/RCmags/ornithopter-hover
- 格式: **FreeCAD `.FCStd`**（~100 个零件，完整四翼机构+齿轮箱+连杆）
- 许可: **GPL-3.0**
- 基于 AeroVironment Nano Hummingbird 设计
- 转换路径: FreeCAD (免费) 导出 STEP → Isaac Sim URDF Importer / Blender USD exporter
- 配套仓库: `RCmags/ornithopter-airfoil-sim`（翼型仿真）、`RCmags/ServoFlappingControl`

### 2. OmniDrones（物理骨架参考）
- URL: https://github.com/btx0424/OmniDrones
- 格式: USD + YAML (Apache-2.0)
- 含 `hummingbird.usd`、`firefly.usd` 等（**注意：是四旋翼不是扑翼**）
- 价值: 作为 Isaac Sim 多旋翼 RL 平台的**物理/控制器骨架**参考
- 文档: https://omnidrones.readthedocs.io/en/latest/tutorials/drone.html

### 3. Isaac Sim 原生（最近似）
- Crazyflie 2.x（27g 微型四旋翼，尺度最接近 MAV）
- 路径: `omniverse://localhost/NVIDIA/Assets/Isaac/5.0/Isaac/Robots/Bitcraze/Crazyflie/cf2x.usd`
- 许可: NVIDIA Isaac Sim EULA
- 用途: 推力控制器参考（非关节驱动）

### 4. 视觉用鸟类网格（仅外观，无物理）
| 模型 | URL | 格式 | 许可 |
|---|---|---|---|
| Low Poly Bird (Animated) | https://sketchfab.com/3d-models/low-poly-bird-animated-82ada91f0ac64ab595fbc3dc994a3590 | glTF/FBX/USDZ | CC-BY-4.0 |
| LowPoly Hummingbird | https://sketchfab.com/3d-models/lowpoly-humming-bird-animated-abc35c5c30fc44c282188c3065a6daf0 | glTF/USDZ | CC-BY |
| Golden Eagle (4K PBR) | https://sketchfab.com/3d-models/golden-eagle-bird-14fba409ecf84939ba4605f5c08f043e | GLB/glTF | CC-BY |

> Sketchfab CC0 鸟类稀少。CC0 过滤器: https://sketchfab.com/search?q=bird&type=models&licenses=322a749bcfa841b29dff1e8a1bb74b0b

### 5. GrabCAD 机构设计（需转换）
- https://grabcad.com/library/tag/ornithopter （数十个 STEP/IGES/STL 模型，需逐个看许可）

### 转换工具链
1. **FreeCAD**（开源）打开 `.FCStd` → 导出 STEP
2. **Blender ≥ 4.2**（带 USD exporter）或 NVIDIA `omni_blender`: https://catalog.ngc.nvidia.com/orgs/nvidia/omniverse/resources/omni_blender/
3. **Isaac Sim 内置 URDF Importer**: `Tools → URDF Importer`（或 `isaaclab.sim.converters.UrdfConverter`）
4. NVIDIA 教程: https://www.youtube.com/watch?v=4Y4Bm1y860I

---

## 二、柱状障碍资产

### ✅ 已实现：项目自带 `modules/obstacle_manager.py`
- 纯 USD 程序化生成 `UsdGeom.Cylinder`（带碰撞、运动学刚体、低摩擦材质）
- **无需任何外部资产**
- 配置在 `config/sim_config.py`:
  - `STATIC_OBSTACLE_COUNT` / `STATIC_OBSTACLE_RADIUS`
  - `BUOY_DEFAULT_RADIUS`（半径）/ `BUOY_DEFAULT_HEIGHT`（高度）
  - `BUOY_COLORS`（已改成树皮棕色系）
- 用作"柱子/树干/避障柱"完全够用

### 备选：Isaac Sim 原生
- `Isaac/5.0/Isaac/Props/Shapes/` 和 `Isaac/Props/Blocks/` 有圆柱 USD 原语
- S3 根: https://omniverse-content-production.s3.us-west-2.amazonaws.com/Assets/Isaac/5.0/Isaac/

---

## 三、森林场景资产

### 1. joevento 程序化森林生成器 ⭐ 最佳匹配
- URL: https://github.com/joevento/Nvidia-Isaac-Sim-Procedual-Forest-Generator （注意 URL 拼写 "Procedual"）
- 许可: MIT（待确认）
- 在 Isaac Sim 内**程序化生成地形 + 森林**，输出 USD
- 配套: https://github.com/joevento/Nvidia-Isaac-Sim-Tree-Generator （从 .txt 布局文件生成确定性的森林）

### 2. NVIDIA Isaac Sim 原生户外场景
- **Rivermark**（最接近"森林"的 NVIDIA 原生场景，含地形+建筑+树木）
  - 路径: `omniverse://localhost/NVIDIA/Assets/Isaac/5.0/Isaac/Environments/Outdoor/Rivermark/rivermark.usd`
- 地形包: `Isaac/5.0/Isaac/Environments/Terrains/` (`flat_plane.usd`, `rough_plane.usd`, `slope.usd`, `stairs.usd`)
- 浏览: https://omniverse-content-production.s3.us-west-2.amazonaws.com/?prefix=Assets/Isaac/5.0/Isaac/Environments/

### 3. NVIDIA Omniverse SimReady 资产包（USD + MDL）
| 包 | URL |
|---|---|
| Environments_NVD ⭐ | https://d4i3qtqj3r0z5.cloudfront.net/Environments_NVD%4010012.zip |
| Environments_Templates_NVD | https://d4i3qtqj3r0z5.cloudfront.net/Environments_Templates_NVD%4010010.zip |
| Showcases_Content_NVD | https://d4i3qtqj3r0z5.cloudfront.net/Showcases_Content_NVD%4010011.zip |
| vMaterials 2.4 (树皮/树叶纹理) | https://d4i3qtqj3r0z5.cloudfront.net/vMaterials_2_4_0_NVD%40020240.zip |
- 索引: https://docs.omniverse.nvidia.com/usd/latest/usd_content_samples/downloadable_packs.html

### 4. 开放许可树木包
| 源 | URL | 格式 | 许可 | 亮点 |
|---|---|---|---|---|
| OpenGameArt Low Poly Nature | https://opengameart.org/content/low-poly-nature-pack-1 | FBX/OBJ/BLEND | **CC0** ⭐ | 150 个模型，真 CC0，长期最安全 |
| Sketchfab Low Poly Forest Pack | https://sketchfab.com/3d-models/low-poly-forest-pack-8908bc910d384f7ea867fa7c97ce6380 | glTF/USDZ | CC-BY | 82 树 + 69 损伤树 + 98 杂项 |
| Sketchfab Free Low Poly Forest | https://sketchfab.com/3d-models/free-low-poly-forest-6dc8c85121234cb59dbd53a673fa2b8f | glTF/USDZ | CC-BY | 23 个资产 |
| Sketchfab CC0 过滤 | https://sketchfab.com/search?q=low+poly+forest&type=models&licenses=322a749bcfa841b29dff1e8a1bb74b0b | 混合 | CC0 ⭐ | 直链 CC0 |

> ⚠️ **避免** Quixel Megascans / SpeedTree（除非只用 UE 内）：许可绑定 UE，脱离 UE 长期商用有风险。

### 转换说明
- glTF / USDZ / FBX → USD：Isaac Sim 视口拖放，或 `MeshConverter` / `UrdfConverter` Python API
- USDZ 本身是 USD 变体，导入摩擦最小

---

## 四、推荐长期资产栈（CC0/开放许可优先）

```
扑翼机体:   RCmags/ornithopter-hover (GPL, FreeCAD)
            + OmniDrones hummingbird.usd (Apache-2.0) 作物理骨架
森林环境:   joevento 程序化森林生成器 (MIT)
            + OpenGameArt Low Poly Nature Pack (CC0)
            + Sketchfab CC0 树木
柱状障碍:   项目自带 obstacle_manager.py（已实现，无需外部资产）
转换工具:   FreeCAD + Blender ≥ 4.2 (USD exporter) + Isaac Sim URDF Importer
```

---

## 五、NVIDIA 论坛参考（how-to，非资产）
- 苹果园环境搭建: https://forums.developer.nvidia.com/t/isaac-sim-apple-orchard-environment/360224
- 程序化植被技巧: https://forums.developer.nvidia.com/t/procedural-vegetation-and-nature/272348
- USD 植物资产: https://forums.developer.nvidia.com/t/usd-assets-of-small-plants/329162
