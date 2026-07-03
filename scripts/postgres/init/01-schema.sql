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
