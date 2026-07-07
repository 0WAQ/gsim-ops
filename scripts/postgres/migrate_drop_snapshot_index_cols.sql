-- 迁移: factor_snapshot 删除 has_pnl / dump_days 两列
--
-- 背景: 三表重构 (migrate_to_snapshot.sql) 把 index 组 (has_pnl/dump_days/delay)
-- 塞进了"入库时不可变快照" factor_snapshot。但 has_pnl/dump_days 是可变物理事实
-- (dump 每天涨、pnl 可后补)，与快照不可变语义冲突，且从未有正确的补全路径
-- (check 写 NULL，LibraryScanner 只更新旧 factor_derived 表)。
--
-- 决定: has_pnl/dump_days 直接删列 (需实时物理状态走 LibraryScanner 扫盘)；
--       delay 保留 (入库时从 XML 解析定死，与 metrics 同性质不可变)。
--
-- 执行前备份:
--   pg_dump -h 10.9.100.160 -p 15432 -U ops -d ops -t factor_snapshot \
--     > backup_snapshot_before_drop_index_cols.sql
--
-- 幂等: 用 IF EXISTS，重复执行安全。

BEGIN;

ALTER TABLE factor_snapshot DROP COLUMN IF EXISTS has_pnl;
ALTER TABLE factor_snapshot DROP COLUMN IF EXISTS dump_days;

COMMIT;
