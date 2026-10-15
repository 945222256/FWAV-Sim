# android_shell — LP 看板安卓薄壳（I-064）

可信任网页活动壳（Trusted Web Activity，TWA）：全屏加载云端看板
https://personal.karmicsword.com/app/ ，自身不含任何界面与网络代码。
深度链接路由的唯一事实源是本目录 [deeplink_contract.json](deeplink_contract.json)，
前端解析实现在 board_ui/src/deeplink.ts，两者逐条对应。

## 一、路由与调起（契约摘要）

| 路由 | 自定义 scheme | https 形态 |
|---|---|---|
| 首页 | flapperboard://home | /app/#/home |
| 事项详情 | flapperboard://item/I-055 | /app/#/item/I-055 |
| 泳道任务板 | flapperboard://lanes | /app/#/lanes |
| 依赖关系图 | flapperboard://graph | /app/#/graph |
| 逻辑线下钻 | flapperboard://line/{id} | /app/#/line/{id} |

豆包手机助手调起走自定义 scheme 入口（无需域名资产校验）；https 入口
为 App Links，需服务器部署 assetlinks.json（模板在 deploy/）。

## 二、目录结构

    app/build.gradle                 应用模块构建脚本
    app/src/main/AndroidManifest.xml 入口声明（scheme 与 App Links）
    app/src/main/java/…/MainActivity.java  唯一活动，scheme 映射
    app/src/main/res/                名称、颜色、主题、占位图标
    deploy/assetlinks.json.template  域名资产校验文件模板
    deeplink_contract.json           深度链接契约（唯一事实源）

## 三、构建步骤（需装有 Android Studio 或命令行 SDK 的机器）

本仓库开发机未装 Android SDK，构建在任一装有 SDK 的机器执行：

1. Android Studio 打开 android_shell 目录（自动识别 Gradle 工程）；
2. Build 菜单 Generate Signed App Bundle / APK，首次创建签名密钥库
   （Generate signed APK，Create new，按向导保存 .jks 与两个密码）；
3. 产出 app/release/app-release.apk，传到手机安装。

## 四、上线 App Links（可选， scheme 入口已可用则可缓做）

1. 对步骤二产出的签名密钥库执行：
   keytool -list -v -keystore <密钥库.jks> -alias <别名>
   取 SHA-256 指纹；
2. 把指纹填入 deploy/assetlinks.json.template，去模板占位说明，
   改名 assetlinks.json 上传服务器站点的 /.well-known/ 路径；
3. 手机重装应用，adb shell am verify-app-links --re-verify
   com.lp.board 触发校验。

## 五、判据对照（事项 I-064）

判据三的静态检索与判据二的接口一致性由 tools/verify_i064.py 执行。
判据一（豆包语音实测）在手机安装本壳后由机主执行，指令示例：
"用豆包打开 flapperboard://item/I-055" 或口令"打开 LP 看板的事项
I-055 详情"。
