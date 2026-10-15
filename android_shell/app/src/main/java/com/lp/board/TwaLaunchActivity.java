package com.lp.board;

import android.net.Uri;
import android.content.Intent;

import com.google.androidbrowserhelper.trusted.LauncherActivity;

/**
 * TWA 启动活动：设备具备 TWA/CustomTabs 浏览器时由 MainActivity 路由进入。
 * 职责：按 deeplink_contract.json 把自定义 scheme 链接映射为 https
 * 应用链接，其余交给父类 LauncherActivity 与浏览器进程。
 * 本类不含任何网络请求代码。
 */
public class TwaLaunchActivity extends LauncherActivity {

    /** 应用基路径，来源 deeplink_contract.json 的 host.app_base。 */
    private static final Uri APP_BASE =
            Uri.parse("https://personal.karmicsword.com/app/");

    @Override
    protected Uri getLaunchingUrl() {
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
        // https 链接与桌面图标启动走默认逻辑
        Uri url = super.getLaunchingUrl();
        Intent intent = getIntent();
        if (intent != null && intent.getData() != null
                && "https".equals(intent.getData().getScheme())) {
            return intent.getData();
        }
        return url;
    }
}
