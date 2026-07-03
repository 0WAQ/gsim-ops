-- ops 派生层 schema — 首次 initdb 时自动执行 (仅 volume 为空时跑一次)
-- 幂等重建见 ops 代码里的 _init_schema(); 这里是 bootstrap
--
-- 单张宽表: 四个 per-machine JSON 缓存 (index/metrics/datasources/bcorr)
-- 合并, 以 (library_id, name) 为主键, 让 ops list 过滤/排序单表无 join

CREATE TABLE IF NOT EXISTS factor_derived (
    library_id              TEXT NOT NULL,
    name                    TEXT NOT NULL,

    -- index 组 (来自 library._scan_directory, 从 JFS 重建)
    author                  TEXT,
    has_pnl                 BOOLEAN,
    dump_days               INT,
    delay                   INT,

    -- metrics 组 (gsim simsummary)
    ret                     DOUBLE PRECISION,
    shrp                    DOUBLE PRECISION,
    mdd                     DOUBLE PRECISION,
    tvr                     DOUBLE PRECISION,
    fitness                 DOUBLE PRECISION,
    metrics_updated_at      TIMESTAMPTZ,

    -- datasources 组 (AST 解析 dr.getData)
    fields                  JSONB,
    tables                  JSONB,
    datasources_updated_at  TIMESTAMPTZ,

    -- bcorr 组 (gsim bcorr, 同 discovery_method 池内最大相关)
    max_bcorr               DOUBLE PRECISION,
    max_bcorr_factor        TEXT,
    bcorr_updated_at        TIMESTAMPTZ,

    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (library_id, name)
);

-- 反查支撑 (Phase G 下一小步用): "哪些因子用了字段 X / 表 Y"
CREATE INDEX IF NOT EXISTS ix_fd_fields ON factor_derived USING GIN (fields);
CREATE INDEX IF NOT EXISTS ix_fd_tables ON factor_derived USING GIN (tables);
-- 常用排序/过滤列
CREATE INDEX IF NOT EXISTS ix_fd_author ON factor_derived (library_id, author);

-- library 级元数据 (如 index_built_at: 上次全量扫盘构建 index 的时间水位,
-- 用来跨机判断 PG 里的 index 是否比 JFS alpha_src mtime 新)
CREATE TABLE IF NOT EXISTS derived_meta (
    library_id TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT,
    PRIMARY KEY (library_id, key)
);

-- 因子生命周期状态 (真相源, 2026-07-04 从 Redis 迁入)。
-- (library_id, name) 主键, 与 factor_derived 同主键, 查询时 join。
-- "factor_state 有 record = 因子存在" 是因子实体的单一定义。
-- check_history 用 JSONB 列 (读因子时一把读全, 无跨因子按 check 查的需求)。
CREATE TABLE IF NOT EXISTS factor_state (
    library_id       TEXT NOT NULL,
    name             TEXT NOT NULL,
    author           TEXT,
    status           TEXT NOT NULL,
    version          INT NOT NULL DEFAULT 1,
    submitted_at     TIMESTAMPTZ,
    submitted_by     TEXT,
    entered_at       TIMESTAMPTZ,
    rejected_at      TIMESTAMPTZ,
    deleted_at       TIMESTAMPTZ,
    last_fail_stage  TEXT,
    last_fail_reason TEXT,
    check_history    JSONB NOT NULL DEFAULT '[]',
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (library_id, name)
);
CREATE INDEX IF NOT EXISTS ix_fs_author ON factor_state (library_id, author);
CREATE INDEX IF NOT EXISTS ix_fs_status ON factor_state (library_id, status);
