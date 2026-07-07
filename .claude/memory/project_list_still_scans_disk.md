---
name: project_list_still_scans_disk
description: "ops list 仍靠 LibraryScanner.scan() 扫盘界定因子集,抵消 PG 迁移,必须改纯 PG 查询"
metadata: 
  node_type: memory
  type: project
  originSessionId: 02290df3-9b26-41b7-8e6f-757fdf6c2227
---

**严重待办(2026-07-07 记)**:`ops list` 的因子集合仍靠 `LibraryScanner.scan()` 扫盘界定(`ops/services/list/list.py:run_list`),这抵消了把因子目录迁 Postgres 的意义。

背景:修 PG 遗留 #1(删 `factor_snapshot.has_pnl/dump_days`)时,list 原来靠 `has_pnl IS NOT NULL` 界定"在库因子集"的路径失效。当时图省事,复用 `run_list` 里本就有的 `scan()` 调用,拿扫盘结果的因子名做白名单(`scanned_names = {f.name for f in scanned}`,内存过滤 `x.info.name in scanned_names`)。这是 **stopgap**,问题严重:

- `scan()` 会 `stat(alpha_src)` 碰盘,alpha_src 变过就触发 ~25s 全盘扫描
- 命中缓存时读的是**僵尸 `factor_derived` 表**(正是要退役那层)
- PG 里已有因子存在性真相源(`factor_state.status`),绕经 scanner+derived 等于把已迁 PG 的东西又从盘上算一遍

**正确方向**:list 应变成**纯 PG catalog 查询,零扫盘**。"在库因子集"判据 = `factor_state.status != 'submitted'`(submitted 在 staging 不在 alpha_src;active+rejected 都归档在 alpha_src)。即 `query_factors` 加个 in-library 过滤下推到 state,`run_list` 删掉 `scan()` 调用。

**副作用**:`ops list --format json` 的 `has_pnl`/`dump_days` 两个实时物理字段无扫盘来源,需删除(实时物理状态走 `ops info` / `ops health`,它们本就该扫盘)。table 输出不含这两列(只有 delay,在 snapshot 里),不受影响。

**Why**:PG 迁移的核心目标就是 list/health 查询不扫盘(三机共享、~0.1s)。留着 scan() 白名单是自我否定,且钉死了 derived 层无法退役(scanner 是 `factor_derived` 唯一活跃读者之一)。

**How to apply**:改 `query_factors`(加 in-library 语义,下推 `s.status != 'submitted'`)+ `run_list`(删 scan)+ 删 list json 的 has_pnl/dump_days。与"清理僵尸 derived 层"(`factor_derived` 表 + `ops/infra/derived/` + scanner 的 PG index 缓存)一并做,见 [[project_factor_library_storage_architecture]]。CLAUDE.md Phase G 剩余项 "list.py scanner.scan() 冗余调用 (待清)" 就是这条。
