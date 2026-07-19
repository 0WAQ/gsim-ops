-- 分组产线 roster 两表(docs/design/factor-produce-groups.md)。
-- 幂等,可重跑;对存量库(生产 ops / 测试 ops_test)执行,新库由 init/01-schema.sql 自带。
BEGIN;

CREATE TABLE IF NOT EXISTS produce_group (
    gid TEXT PRIMARY KEY,
    author TEXT NOT NULL,
    delay INT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_produce_group_status CHECK (status IN ('active', 'superseded'))
);
CREATE TABLE IF NOT EXISTS produce_group_member (
    gid TEXT NOT NULL REFERENCES produce_group(gid),
    factor TEXT NOT NULL,
    ordinal INT NOT NULL,
    muted BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (gid, factor),
    CONSTRAINT uq_pgm_gid_ordinal UNIQUE (gid, ordinal)
);
CREATE INDEX IF NOT EXISTS ix_pgm_factor ON produce_group_member(factor);
CREATE TABLE IF NOT EXISTS produce_single (
    factor TEXT PRIMARY KEY,
    author TEXT NOT NULL,
    admitted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
