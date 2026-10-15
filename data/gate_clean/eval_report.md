# 评测报告 — gate_clean

- 帧数(门可见): 37 | 工况: clean / normal

| method | cpe_rmse_px | cpe_mean_px | radius_err_mean_px | valid_rate | latency_ms_mean | latency_ms_p95 |
|---|---|---|---|---|---|---|
| adaptive | 1.665 | 1.553 | 11.8 | 1.000 | 1.693 | 5.441 |
| plain_hough | 7.753 | 3.351 | 23.4 | 1.000 | 2.853 | 3.258 |

免预训练: {'adaptive': '否', 'plain_hough': '否'}

CPE RMSE 归一化(÷对角线): {'adaptive': 0.00208, 'plain_hough': 0.00969}
