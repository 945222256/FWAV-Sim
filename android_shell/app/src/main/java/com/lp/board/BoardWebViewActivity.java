package com.lp.board;

import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebSettings;

import androidx.appcompat.app.AppCompatActivity;

/**
 * I-075 黑屏修复执行体：壳内 WebView 兜底渲染。
 *
 * 触发条件：设备上没有任何 TWA/CustomTabs 能力浏览器
 * （logcat 定性证据：TWAProviderPicker "Found no TWA providers, using
 * first browser: null" + TwaLauncher.launch 抛错，屏幕停留主题底色）。
 * 本活动把契约五路由的 https 页面渲染在壳自己的 WebView 里，
 * 使看板在任何手机上都能出现，不再依赖浏览器能力。
 * 本类只做页面加载，不发起任何账本写请求（写通道在页面登录后）。
 */
public class BoardWebViewActivity extends AppCompatActivity {

    /** 应用基路径，来源 deeplink_contract.json 的 host.app_base。 */
    private static final Uri APP_BASE =
            Uri.parse("https://personal.karmicsword.com/app/");

    private WebView webView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        webView = new WebView(this);
        webView.setBackgroundColor(0xFF0D1117);
        setContentView(webView);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                // 同域（含 /login 与 /app/）一律就地渲染；返回 true 会把
                // React 的 401 跳转也掐死（2026-10-05 实测教训）。
                // 外域交给系统浏览器。
                Uri u = Uri.parse(url == null ? "" : url);
                if (APP_BASE.getHost() != null
                        && APP_BASE.getHost().equals(u.getHost())) {
                    return false;
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (Exception ignored) {
                }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                android.util.Log.i("BoardWebView", "onPageFinished " + url);
            }

            @Override
            public void onReceivedError(WebView view, android.webkit.WebResourceRequest request,
                                        android.webkit.WebResourceError error) {
                android.util.Log.e("BoardWebView", "onReceivedError "
                        + request.getUrl() + " " + error.getErrorCode() + " " + error.getDescription());
            }
        });
        Uri target = resolveTarget();
        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            webView.loadUrl(target.toString());
        }
        // 状态栏沉浸：内容顶到状态栏下方即可，无特殊处理。
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
    }

    /** 与 MainActivity 同一张映射表：flapperboard://route → 基路径#/route。 */
    private Uri resolveTarget() {
        // 诊断钩子：验收脚本可用 test_url 覆盖目标，分层判别渲染问题。
        String override = getIntent().getStringExtra("test_url");
        if (override != null && !override.isEmpty()) {
            return Uri.parse(override);
        }
        Uri input = getIntent().getData();
        if (input != null && "flapperboard".equals(input.getScheme())) {
            // flapperboard://item/I-055 → https://…/app/#/item/I-055
            // schemeSpecificPart 自带前导斜杠，剥净再拼哈希。
            String route = input.getSchemeSpecificPart();
            if (route != null) {
                while (route.startsWith("/")) {
                    route = route.substring(1);
                }
                if (!route.isEmpty()) {
                    return Uri.parse(APP_BASE.toString() + "#/" + route);
                }
            }
            return APP_BASE;
        }
        if (input != null && "https".equals(input.getScheme())) {
            return input;
        }
        return APP_BASE;
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        webView.saveState(outState);
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
