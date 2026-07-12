-- ops 三表 schema — 首次 initdb 时自动执行 (仅 volume 为空时跑一次)。
-- 幂等重建见 ops/infra/schema.py::ensure_schemas(); 这里是 bootstrap。
--
-- ⚠ 本文件是各 pg_store._SCHEMA 的镜像 (多真相源, full-review S2)。改表结构
-- 时两处同改;一致性由 tests/test_schema_pin.py 钉住(drift 即红,2026-07-12)。
-- 2026-07-07 Wave 2 重写: 原文件仍是迁移前的 factor_derived + 带
-- library_id/author 的旧 factor_state —— 空库 bootstrap 会起出旧世界
-- (full-review P0-3)。旧生产库的僵尸表清理见 ../migrate_drop_derived.sql。
--
-- 三表结构 (2026-07-06 拆分):
--   factor_info     身份 (三表的根; state/snapshot 外键级联于它)
--   factor_state    生命周期状态机
--   factor_snapshot 入库时不可变快照 (snapshot_at = entered_at)
-- 建表顺序即 FK 依赖顺序: info 必须最先。

-- 1. factor_info — 身份信息 (镜像 ops/infra/info/pg_store.py:_SCHEMA)
CREATE TABLE IF NOT EXISTS factor_info (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    author TEXT,
    discovery_method TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_factor_info_author ON factor_info(author);
CREATE INDEX IF NOT EXISTS idx_factor_info_discovery ON factor_info(discovery_method);

-- 2. factor_state — 生命周期 (镜像 ops/infra/store/pg_store.py:_SCHEMA)
CREATE TABLE IF NOT EXISTS factor_state (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    version INT NOT NULL DEFAULT 1,
    submitted_at TIMESTAMPTZ,
    entered_at TIMESTAMPTZ,
    rejected_at TIMESTAMPTZ,
    last_fail_stage TEXT,
    last_fail_reason TEXT,
    check_history JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (name) REFERENCES factor_info(name) ON DELETE CASCADE,
    CONSTRAINT chk_status CHECK (status IN ('submitted', 'checking', 'active', 'rejected')),
    CONSTRAINT chk_active_entered CHECK (status <> 'active' OR entered_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_fs_status ON factor_state(status);

-- 3. factor_snapshot — 入库时快照 (镜像 ops/infra/snapshot/pg_store.py:_SCHEMA)
CREATE TABLE IF NOT EXISTS factor_snapshot (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,

    ret DOUBLE PRECISION,
    shrp DOUBLE PRECISION,
    mdd DOUBLE PRECISION,
    tvr DOUBLE PRECISION,
    fitness DOUBLE PRECISION,

    fields JSONB,
    tables JSONB,

    delay INT,

    max_bcorr DOUBLE PRECISION,
    max_bcorr_factor TEXT,

    snapshot_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (name) REFERENCES factor_info(name) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_factor_snapshot_fields ON factor_snapshot USING GIN(fields);
CREATE INDEX IF NOT EXISTS idx_factor_snapshot_tables ON factor_snapshot USING GIN(tables);
CREATE INDEX IF NOT EXISTS idx_factor_snapshot_ret ON factor_snapshot(ret);
CREATE INDEX IF NOT EXISTS idx_factor_snapshot_shrp ON factor_snapshot(shrp);
