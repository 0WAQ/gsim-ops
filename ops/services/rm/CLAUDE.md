# Rm

Soft-delete a factor.

## 默认行为

Flip state to DELETED (tombstone). 磁盘文件完全不动。因子可通过 `ops recheck -s deleted` 恢复。

## --force

额外删除:
- `alpha_dump/<name>/` (整个目录)
- `alpha_feature/<name>.v1.npy` + `<name>.v2.npy`

始终保留: `alpha_src`, `alpha_pnl`

## 传播

Tombstone 通过 `ops sync push` state merge 传播到其他机器。远端文件不删除（留给未来 `ops sync gc`）。

## 确认

默认交互确认 `[y/N]`，`-y` 跳过。
