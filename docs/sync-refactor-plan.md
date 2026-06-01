# Sync Refactor Plan

## 背景

旧 sync 是"假同步":因为 alpha_dump 每因子有 1.8M+ 小文件,无法直接 list/比对,所以引入 `sync_manifest.json` 缓存每因子指纹(mtime + dump 计数),push 时只看指纹变化决定是否上传,从不真正对比两端内容。

现状变化:
- alpha_dump 降级为本地中间产物,不参与 sync(Phase 2 完成)。
- sync 实际只覆盖 `alpha_src / alpha_pnl / alpha_feature` 三个目录,规模:每因子 ~5 个文件,千级因子也就几千对象,完全可以直接列举比对。
- 状态机重写后,因子有明确状态(SUBMITTED/ACTIVE/REJECTED/DELETED),sync 需要按状态决定数据策略。

**结论:把 sync 改成真同步,以 S3 list + 本地 walk 为真相源,直接做 diff;manifest 指纹机制拆掉。**

## 目标

1. push / pull 都基于"列举两端实际对象 → diff → 传输"完成,不再依赖本地指纹缓存。
2. 状态机感知:DELETED tombstone、REJECTED 部分产物、SUBMITTED 不入库,都有明确处理。
3. `sync_manifest.json` 的 sync 部分整体废弃;dump 追踪字段挪给 pack。
4. `verify` 从 placeholder 升级为真校验(size + count + 选 etag)。

## 设计

### 真同步核心流程

```
1. list_remote(pfx)         → {dir: {relpath: (size, etag, mtime)}}
2. walk_local(config)       → {dir: {relpath: (size, mtime)}}
3. diff(local, remote)      → push_set / pull_set / conflict_set
4. transport(diff, dir)     → boto3 上传/下载
5. merge_states(...)        → 三态文件仍走 updated_at merge
```

- **列举开销**:3 个目录 × `list_objects_v2` 分页,千级因子量级一两秒。
- **判等策略**:size 相等且 etag/md5 匹配 → 跳过;否则按 mtime 新旧仲裁(后续可改 updated_at)。
- **并发**:ThreadPoolExecutor(8) 沿用。

### 状态感知规则

| 状态 | push 行为 | pull 行为 |
|---|---|---|
| ACTIVE | 正常 diff + 传输三类数据 | 正常 diff + 拉缺失/过期 |
| SUBMITTED | **跳过**(在 staging,不进 alpha_src,源头天然不会出现) | **跳过** |
| REJECTED | 按本地实际存在的产物传(失败阶段决定有无 pnl/feature) | 按远端实际存在拉,缺的不报错 |
| DELETED | 数据不动;tombstone 通过 state merge 传播 | 跳过该因子数据 |

DELETED 因子的远端数据回收 → `sync gc`(独立命令,不在本次范围)。

### State 文件 merge

保留现有 `updated_at` 三态 merge(`merge.py`),仅调整一点:
- `factor_state.json` merge 时,SUBMITTED 条目按 library 区分。若 staging 与 prod 是同一 `library_id`,需在 merge 阶段过滤 SUBMITTED 不进 prod(待 P4 确认)。

### push pre-push check

旧逻辑 `behind = remote_names - local_names` 改成:对每个 key 比较 `updated_at`,只有当远端某 key 的 `updated_at` 严格新于本地才报 behind。raw key set 差异不再作为拒绝条件。

### manifest 去向

- `sync_manifest.json` 直接废弃,sync 不再读写。
- `dump_latest / dump_count` 字段挪到新的 `pack_manifest.json`,owner 改为 `services/pack/`。
- 首次运行 pack 时,若发现旧 `sync_manifest.json`,一次性迁移 dump 字段到 pack manifest 后删除原文件。

### verify

`ops sync verify` 实现真校验:
- list 两端 → 输出 size 不一致 / 仅本地 / 仅远端 / etag 不一致 四类清单。
- 默认只对比 size + count;`--deep` 启用 etag 校验。
- 只读,不修改任何东西。

## 推进顺序

1. **Step 1 — 真 diff 引擎**:实现 `list_remote / walk_local / diff` 三个原子函数,先用在 `verify` 上(只读,验证正确性最低风险)。
2. **Step 2 — push 重写**:基于 diff 引擎重写 push,删 `scan_changes` 路径。pre-push behind 改 updated_at。
3. **Step 3 — pull 重写**:基于 diff 引擎重写 pull,加状态过滤(skip DELETED,REJECTED 容错)。
4. **Step 4 — manifest 拆分**:dump 字段迁到 pack,删 `sync_manifest.json` 读写,清掉 `manifest.py` 里 sync 不再需要的代码。
5. **Step 5 — library_id 隔离确认**:确认 staging/prod 是否共用 library_id,必要时调 config 或 merge 过滤。

每步独立 PR,Step 1 落地后续步骤都基于它。

## 不在范围

- `sync gc`:回收远端 DELETED 因子存储,独立设计独立 PR。
- merge 算法升级(vector clock / CRDT):`updated_at` + 平局保本地够用。
- S3 后端抽象(切 rclone / ssh):暂无需求。

## 待定决策

- DELETED tombstone 传播后,本机 pull 是否需要主动清掉本地对应数据?(当前 `ops rm` 默认软删,`--force` 才清本地;pull 侧默认应保持非破坏,不主动清。)
- push 发现远端某文件比本地新(意味着另一台机器更早推送了同一因子的新版本),策略:报警跳过 vs 自动以远端为准更新本地?倾向**报警跳过**,避免误覆盖未 pull 的工作。
