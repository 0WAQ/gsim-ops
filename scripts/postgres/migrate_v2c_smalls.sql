-- v2c 小件迁移(2026-07-12,schema v2 收官后的两处 \d 观察 + 遗留项):
--   1. 删 status 重复索引 idx_factor_state_status(2026-07-06 三表迁移遗留,
--      与代码 DDL 的 ix_fs_status 完全同构,白担写放大);
--   2. factor_state_new_* 内部名归一(当年建 _new 表 rename 而来,PG 不改
--      约束/序列名;纯美观 + 使生产 ⇔ 代码 DDL 完全同名);
--   3. factor_info.discovery_method 加 CHECK(automated/manual/backfill,
--      NULL 允许 —— 未入库/legacy;先验存量,违规值即 RAISE 回滚)。
-- 幂等:IF EXISTS / 条件 DO;可重复执行。
BEGIN;

DROP INDEX IF EXISTS idx_factor_state_status;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='factor_state_new_pkey') THEN
        ALTER TABLE factor_state RENAME CONSTRAINT factor_state_new_pkey TO factor_state_pkey;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='factor_state_new_name_key') THEN
        ALTER TABLE factor_state RENAME CONSTRAINT factor_state_new_name_key TO factor_state_name_key;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='factor_state_new_name_fkey') THEN
        ALTER TABLE factor_state RENAME CONSTRAINT factor_state_new_name_fkey TO factor_state_name_fkey;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname='factor_state_new_id_seq') THEN
        ALTER SEQUENCE factor_state_new_id_seq RENAME TO factor_state_id_seq;
    END IF;
END $$;

-- 前置守卫:存量 discovery_method 必须 ∈ 枚举或 NULL
DO $$
DECLARE bad bigint;
BEGIN
    SELECT count(*) INTO bad FROM factor_info
    WHERE discovery_method IS NOT NULL
      AND discovery_method NOT IN ('automated', 'manual', 'backfill');
    IF bad > 0 THEN
        RAISE EXCEPTION 'discovery_method 有 % 行枚举外值,停止待判读', bad;
    END IF;
END $$;
ALTER TABLE factor_info DROP CONSTRAINT IF EXISTS chk_discovery;
ALTER TABLE factor_info ADD CONSTRAINT chk_discovery
    CHECK (discovery_method IS NULL OR discovery_method IN ('automated', 'manual', 'backfill'));

COMMIT;
