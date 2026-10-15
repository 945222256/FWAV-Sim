# 评测报告 — gate_lowlight

- 帧数(门可见): 39 | 工况: lowlight / lowlight

| method | cpe_rmse_px | cpe_mean_px | radius_err_mean_px | valid_rate | latency_ms_mean | latency_ms_p95 |
|---|---|---|---|---|---|---|
| adaptive | 5.920 | 5.688 | 11.2 | 0.205 | 2.353 | 3.371 |
| plain_hough | 4.328 | 4.328 | 13.1 | 0.026 | 2.387 | 2.693 |

免预训练: {'adaptive': '否', 'plain_hough': '否'}

CPE RMSE 归一化(÷对角线): {'adaptive': 0.0074, 'plain_hough': 0.00541}
