# 评测报告 — gate_clutter

- 帧数(门可见): 39 | 工况: clutter / normal

| method | cpe_rmse_px | cpe_mean_px | radius_err_mean_px | valid_rate | latency_ms_mean | latency_ms_p95 |
|---|---|---|---|---|---|---|
| adaptive | 2.073 | 1.754 | 10.7 | 0.974 | 2.267 | 8.423 |
| plain_hough | 6.776 | 3.221 | 22.6 | 0.974 | 3.043 | 3.378 |

免预训练: {'adaptive': '否', 'plain_hough': '否'}

CPE RMSE 归一化(÷对角线): {'adaptive': 0.00259, 'plain_hough': 0.00847}
