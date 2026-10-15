package com.lp.board;

import android.content.Intent;
import android.os.Bundle;

import androidx.appcompat.app.AppCompatActivity;

/**
 * I-064 薄壳主入口（路由活动）：探测浏览器 TWA 能力后二选一分发。
 *
 * I-075 根因修复（2026-10-05 logcat 定性）：设备无 TWA 提供方时，
 * androidbrowserhelper 的 TwaLauncher.launch 在 LauncherActivity.onCreate
 * 内抛 NameNotFoundException，屏幕停留主题深底即机主所见黑屏。
 * 故本活动只做路由，不再让无浏览器设备进入 LauncherActivity：
 *   有 TWA/CustomTabs 浏览器 → TwaLaunchActivity（全屏可信网页）；
 *   没有                     → BoardWebViewActivity（壳内 WebView 自渲染）。
 * 能力探针用 CustomTabsClient.getPackageName（权威解析
 * CustomTabsService 持有方）；TwaProviderPicker.pickProvider 实测在
 * 浏览器被禁用时仍返回非空，不可作探针。
 * 本类不含任何网络请求代码。
 */
public class MainActivity extends AppCompatActivity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Intent next;
        if (twaCapable()) {
            next = new Intent(this, TwaLaunchActivity.class);
        } else {
            next = new Intent(this, BoardWebViewActivity.class);
        }
        // 转发原始动作与数据（深度链接五路由与冷启动都经这里）。
        if (getIntent() != null && getIntent().getData() != null) {
            next.setAction(getIntent().getAction());
            next.setData(getIntent().getData());
        }
        // 诊断钩子透传：验收脚本可用 test_url 分层判别渲染问题。
        String testUrl = getIntent() == null ? null : getIntent().getStringExtra("test_url");
        if (testUrl != null && !testUrl.isEmpty()) {
            next.putExtra("test_url", testUrl);
        }
        startActivity(next);
        finish();
    }

    /** 探测是否存在可承载 TWA/CustomTabs 的浏览器。 */
    private boolean twaCapable() {
        try {
            return androidx.browser.customtabs.CustomTabsClient
                    .getPackageName(this, null) != null;
        } catch (Exception e) {
            // 探测本身异常按无能力处理，走兜底。
            return false;
        }
    }
}
