# 盘面布局

因子的重型产物落 JuiceFS 挂载点上的 alphalib。布局唯一正主是
[`ops/core/paths.py`](../../ops/core/paths.py) 的 `FactorPaths`——**任何地方不得再手写
`config.alpha_xxx / name`**。

## alphalib 五路径 + 池

```
alphalib/
├── alpha_src/<name>/              源码目录(*.py + Config.xml + Readme)  —— JFS 共享
├── alpha_pnl/<name>               pnl 单文件 ⚠                           —— JFS 共享
├── alpha_dump/<name>/             日频持仓目录(YYYY/MM/<date>*.npy)      —— 软链 → 本机 sidecar
├── alpha_feature/<name>.v{1,2}.npy 聚合矩阵(pack 产出)单文件            —— JFS 共享
├── staging/<name>/                在检因子(共享队列)                    —— JFS 共享
├── pnl_automated/<name>           bcorr 分流池(机器因子)单文件          —— JFS 共享
└── pnl_manual/<name>              bcorr 分流池(人工因子)单文件          —— JFS 共享
```

**布局事实由类型承载**(不再靠文档人肉维持):
- **目录**:alpha_src / staging / alpha_dump —— 删除用 `shutil.rmtree`。
- **单文件**:alpha_pnl / 池副本 / alpha_feature —— 删除用 `Path.unlink()`(⚠ 用 rmtree 会
  `Errno 20: Not a directory`)。
- feature 命名 `<name>.<v1|v2>.npy`(`FEATURE_VERSIONS`);`meta.json` 随因子目录走
  (staging → alpha_src)。

`FactorPaths.of(name, config)` 拼出全部落点;`repo.paths(name)` 是 service 层入口。

## 共享 vs 本机

**五条数据路径唯一本机的是 alpha_dump**(日频小文件量大,有意留本机 sidecar 不进 JFS):

- `/mnt/storage/alphalib` 是**软链**,指向本机实际 alphalib(各机挂载点不同:160/150
  `/tank/vault/`、144 `/storage/vault/`、170 `/nvme125/`)——老脚本/固定路径经它仍可用。
- `alphalib/alpha_dump` 是**软链**,实体是 `<挂载点>.local/alpha_dump`(本地盘 sidecar,每机一份)。
- `alphalib/staging` 自 2026-07-11 起是 **JFS 实目录(共享)**——共享 staging + 队列消费
  (见 [topology.md](topology.md) 与 [`../design/shared-staging-queue.md`](../design/shared-staging-queue.md))。
- bcorr 分流池 `pnl_automated`/`pnl_manual` 是 **JFS 共享实目录**(对比池须全局一致)。

⇒ src / pnl / feature / staging + 分流池全部共享;只有 alpha_dump 本机。

sidecar alpha_dump 只承载 **check 验证产物**(入库时搬入,≤20251231 窗口);
因子**生产** dump 是另一个事实族,落 170 独立 dataset(/nvme125/alpha_dump,
20110101 起全史 + 日增),两处不对账不互搬 —— 见
`docs/design/factor-produce-v3.md`。feature 仍只覆盖到 20251231(PACK_L 扩行后议)。

## bcorr 分流池

archive 入库时按 `discovery_method` 把 pnl 额外分流一份:automated → `pnl_automated/`,
manual → `pnl_manual/`。correlation stage 只在同类池内比相关性(人工/机器因子互不撞车),
见 [check-pipeline.md](check-pipeline.md) 的 correlation 节。REJECTED 不分流;离库回收
(restage/rm/--overwrite)走 `repo.purge_artifacts(name, ArtifactScope.CHECK)` 清池副本
(防"自鬼影")。

## 权限模型

共享路径 owner 一律 root,group `alpha-core`(alpha_src)/ `alpha-data`(其它)只读且仅作
跨机 label。**所有写都走 root**——ops 的写命令经 [`ops/infra/sudo.py`](../../ops/infra/sudo.py)
自动 sudo 提权(见 [topology.md](topology.md))。本机部署形态的补建/体检归 `ops setup`
(见 [`../../ops/services/setup/CLAUDE.md`](../../ops/services/setup/CLAUDE.md))。

深度见 [`../../CLAUDE.md`](../../CLAUDE.md) "Factor Library Structure"。

→ 回 [架构总览](../architecture.md#7-盘面布局)
