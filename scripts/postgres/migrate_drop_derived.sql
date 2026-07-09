-- 2026-07-07 Wave 2 (docs/remediation/JOURNAL.md V2): 删除 derived 僵尸表。
--
-- 背景: metrics/datasources/bcorr 三组 2026-07-06 迁 factor_snapshot;index
-- 缓存组随 LibraryScanner 缓存路径在 Wave 2 删除 (它自迁移起已坏:
-- derived_meta 被重建为无 library_id 形状,get_meta 每次 UndefinedColumn 被吞,
-- 缓存永久失效,full-review P0-4)。ops 代码 (Wave 2 起) 不再引用这两张表。
--
-- 在 ops 生产库上【手动】执行,执行前确认:
--   1. 三机 ops 均已更新到 Wave 2 之后的版本 (git log 含 refactor(wave2));
--   2. psql 里 spot-check factor_snapshot 行数 ≈ 已入库因子数 (迁移时 7485)。
--
-- 执行: docker exec -i <pg容器> psql -U ops -d ops < migrate_drop_derived.sql

BEGIN;

DROP TABLE IF EXISTS factor_derived CASCADE;
DROP TABLE IF EXISTS derived_meta CASCADE;

COMMIT;

-- 完成后: ~/.cache/ops/lib/<lib>/derived.json (各机的 json 后端残留) 可一并
-- 手动删除;index_built_at 水位不复存在。
