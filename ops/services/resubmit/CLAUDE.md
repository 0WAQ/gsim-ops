# Resubmit

已有因子提交新代码(从 dropbox 覆盖,version += 1)。

## 前置条件

因子名必须已存在于 state 中。不存在的因子会被拒绝,提示用 `ops submit`。

## 操作流程

1. 扫描 dropbox(复用 `submit.py` 的 `_iter_dropbox_dirs`)
2. 过滤:只保留 state 中已存在的因子
3. 复制到 staging,normalize XML,生成 meta.json
4. `store.transition → SUBMITTED`,`version += 1`
5. 旧 alpha_src 中的代码保留(作为对比基准)
6. dump / feature / pnl 保留

## 语义区分

- `ops submit`: 新因子入系统。因子名不能已存在。version = 1。
- `ops resubmit`: 已有因子提交新代码。因子名必须已存在。version += 1。
- `ops recheck`: 原代码不变,重跑 check。version 不变。

## 并发安全

每个因子操作包裹在 `factor_lock` 中。被占用则跳过。
