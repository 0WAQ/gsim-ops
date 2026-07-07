-- PostgreSQL 迁移脚本: factor_derived 宽表 -> factor_info + factor_snapshot 分离
--
-- 背景: ret/shrp 等字段语义从"最新表现"改为"入库时快照"
-- 变更:
--   1. 去掉 library_id (永远单库)
--   2. author 从 factor_state/factor_derived 移到 factor_info
--   3. 所有派生数据 (metrics/datasources/bcorr/index) 移到 factor_snapshot (不可变)
--   4. 主键从 (library_id, name) 改为自增 id + name UNIQUE
--
-- 执行前备份:
--   pg_dump -h 10.9.100.160 -p 15432 -U ops -d ops > backup_before_snapshot.sql
--
-- 修正点 (相比原版):
--   1. CHECK 约束改为小写 (submitted/checking/active/rejected)
--   2. 只迁移有 ret 的因子到 snapshot (避免迁移不完整的数据)
--   3. derived_meta 迁移逻辑修正 (假设单库，取 max(library_id) 的记录)

BEGIN;

-- ========== 1. 创建新表 ==========

-- 1.1 因子身份信息
CREATE TABLE factor_info (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    author TEXT,
    discovery_method TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 1.2 生命周期状态（只保留状态机字段）
CREATE TABLE factor_state_new (
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
    -- 修正: 小写状态值
    CONSTRAINT chk_status CHECK (status IN ('submitted', 'checking', 'active', 'rejected'))
);

-- 1.3 入库时快照（所有派生数据，不可变）
CREATE TABLE factor_snapshot (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,

    -- metrics 组
    ret DOUBLE PRECISION,
    shrp DOUBLE PRECISION,
    mdd DOUBLE PRECISION,
    tvr DOUBLE PRECISION,
    fitness DOUBLE PRECISION,

    -- datasources 组
    fields JSONB,
    tables JSONB,

    -- index 组
    has_pnl BOOLEAN,
    dump_days INT,
    delay INT,

    -- bcorr 组
    max_bcorr DOUBLE PRECISION,
    max_bcorr_factor TEXT,

    snapshot_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (name) REFERENCES factor_info(name) ON DELETE CASCADE
);

-- 1.4 derived_meta_new (去掉 library_id)
CREATE TABLE derived_meta_new (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- ========== 2. 数据迁移 ==========

-- 2.1 迁移 factor_info
-- 数据源 = factor_state ∪ factor_derived(有 ret) 的并集
--   - 正常因子: 两表都有
--   - 孤儿因子: 只在 factor_derived 有 ret 但无 state record (state 被误删,
--     但 src/pnl/metrics 都在) -> 也要建 info, 否则 snapshot 外键失败
-- author: 优先 factor_state.author, 回退 factor_derived.author
-- created_at: 优先 state.submitted_at, 回退 state.updated_at, 再回退 derived.updated_at
-- discovery_method 暂时 NULL (后续 Python 脚本补)
INSERT INTO factor_info (name, author, created_at)
SELECT
    u.name,
    COALESCE(fs.author, fd.author) as author,
    COALESCE(fs.submitted_at, fs.updated_at, fd.updated_at, now()) as created_at
FROM (
    -- 并集: 所有 state 因子 + 所有有 ret 的 derived 因子
    SELECT name FROM factor_state
    UNION
    SELECT name FROM factor_derived WHERE ret IS NOT NULL
) u
LEFT JOIN factor_state fs ON fs.name = u.name
LEFT JOIN factor_derived fd ON fd.name = u.name;

-- 2.2 迁移 factor_state (去掉 library_id 和 author)
-- 注意: status 必须转成小写
INSERT INTO factor_state_new (
    name, status, version,
    submitted_at, entered_at, rejected_at,
    last_fail_stage, last_fail_reason,
    check_history, updated_at
)
SELECT
    name,
    LOWER(status) as status,  -- 转小写
    version,
    submitted_at, entered_at, rejected_at,
    last_fail_stage, last_fail_reason,
    check_history, updated_at
FROM factor_state;

-- 2.2b 为孤儿因子补 state record (只在 derived 有 ret, 无 state)
-- 它们有 src/pnl/metrics, 视为正常 ACTIVE 因子, state 被误删需补回
-- version=1, entered_at 用 derived.updated_at (近似入库时间)
INSERT INTO factor_state_new (
    name, status, version,
    submitted_at, entered_at, rejected_at,
    last_fail_stage, last_fail_reason,
    check_history, updated_at
)
SELECT
    fd.name,
    'active' as status,
    1 as version,
    fd.updated_at as submitted_at,
    fd.updated_at as entered_at,
    NULL as rejected_at,
    NULL as last_fail_stage,
    NULL as last_fail_reason,
    '[]'::jsonb as check_history,
    fd.updated_at as updated_at
FROM factor_derived fd
WHERE fd.ret IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM factor_state fs WHERE fs.name = fd.name);

-- 2.3 迁移 factor_snapshot (所有派生数据)
-- 只迁移有 ret 的记录 (代表至少跑完了 metrics)
INSERT INTO factor_snapshot (
    name,
    ret, shrp, mdd, tvr, fitness,
    fields, tables,
    has_pnl, dump_days, delay,
    max_bcorr, max_bcorr_factor,
    snapshot_at
)
SELECT
    fd.name,
    fd.ret, fd.shrp, fd.mdd, fd.tvr, fd.fitness,
    fd.fields, fd.tables,
    fd.has_pnl, fd.dump_days, fd.delay,
    fd.max_bcorr, fd.max_bcorr_factor,
    -- snapshot_at 策略:
    -- 1. ACTIVE 因子: 用 entered_at (入库时间)
    -- 2. REJECTED 但有 metrics: 用 metrics_updated_at (最后一次刷 metrics 的时间)
    -- 3. 孤儿/都没有: 用 derived.updated_at
    COALESCE(
        (SELECT fs.entered_at FROM factor_state fs
         WHERE fs.name = fd.name AND fs.entered_at IS NOT NULL),
        fd.metrics_updated_at,
        fd.updated_at,
        now()
    ) as snapshot_at
FROM factor_derived fd
WHERE fd.ret IS NOT NULL;  -- 只迁移有 metrics 的

-- 2.4 迁移 derived_meta (假设单库，取最大 library_id 的记录)
INSERT INTO derived_meta_new (key, value)
SELECT key, value
FROM (
    SELECT key, value, library_id,
           ROW_NUMBER() OVER (PARTITION BY key ORDER BY library_id DESC) as rn
    FROM derived_meta
) t
WHERE rn = 1;

-- ========== 3. 删除旧表 ==========

DROP TABLE factor_state CASCADE;
DROP TABLE factor_derived CASCADE;
DROP TABLE derived_meta CASCADE;

-- 重命名新表
ALTER TABLE factor_state_new RENAME TO factor_state;
ALTER TABLE derived_meta_new RENAME TO derived_meta;

-- ========== 4. 创建索引 ==========

CREATE INDEX idx_factor_info_author ON factor_info(author);
CREATE INDEX idx_factor_info_discovery ON factor_info(discovery_method);
CREATE INDEX idx_factor_state_status ON factor_state(status);
CREATE INDEX idx_factor_snapshot_fields ON factor_snapshot USING GIN(fields);
CREATE INDEX idx_factor_snapshot_tables ON factor_snapshot USING GIN(tables);
CREATE INDEX idx_factor_snapshot_ret ON factor_snapshot(ret);
CREATE INDEX idx_factor_snapshot_shrp ON factor_snapshot(shrp);

COMMIT;

-- ========== 后续步骤 ==========
-- 执行完 SQL 后，运行 Python 脚本补充 discovery_method:
--   uv run python scripts/postgres/backfill_discovery_method.py
--
-- 验证数据一致性:
--   SELECT COUNT(*) FROM factor_info;
--   SELECT COUNT(*) FROM factor_state;
--   SELECT COUNT(*) FROM factor_snapshot;
--
--   -- 检查 info 和 state 是否 1:1
--   SELECT
--     (SELECT COUNT(*) FROM factor_info) as info_count,
--     (SELECT COUNT(*) FROM factor_state) as state_count,
--     (SELECT COUNT(*) FROM factor_snapshot) as snapshot_count;
--
--   -- 检查是否有 orphan
--   SELECT name FROM factor_state WHERE name NOT IN (SELECT name FROM factor_info);
--   SELECT name FROM factor_snapshot WHERE name NOT IN (SELECT name FROM factor_info);
