# 参数实验室阶段一备份（I-136 判据一）

版本标签：阶段一（I-130 陈列表示版）。
备份日期：2026-10-10。
来源提交：bbd9122（四文件与该提交逐字节一致，git diff 为空）。
备份方式：目录拷贝。

## 内容

- LabPage.tsx：阶段一页面组件原文件。
- lab.css：阶段一样式原文件（含十七个本地令牌副本）。
- types.ts：阶段一载荷类型声明。
- lab_payload_v1.py：阶段一载荷构建器（原路径 lp_board/lab_payload.py）。

## 回退步骤（在仓库根目录执行）

```
cp board_ui/lab_backup_v1_20261010/LabPage.tsx board_ui/src/lab/LabPage.tsx
cp board_ui/lab_backup_v1_20261010/lab.css board_ui/src/lab/lab.css
cp board_ui/lab_backup_v1_20261010/types.ts board_ui/src/lab/types.ts
cp board_ui/lab_backup_v1_20261010/lab_payload_v1.py lp_board/lab_payload.py
```

回退后本地与云端各重启服务即恢复阶段一页面。本备份目录本身不随
回退删除，作为阶段一的永久留档。
