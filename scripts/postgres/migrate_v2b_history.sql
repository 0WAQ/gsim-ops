-- v2b 迁移: factor_history 全操作审计表 + factor_state 删四列 + fields/tables TEXT[]
-- (schema-v2 设计 docs/schema-v2.md,2026-07-12)
--
-- 内容(单事务,任一步失败整体回滚):
--   1. 建 factor_history(与代码 DDL 同文;无 FK —— 历史活过 ops rm);
--   2. check_history JSONB 逐元素展开成 op='check' 事件(真实数据),
--      行数核对不一致即 RAISE 回滚;
--   3. 生命周期事件合成(actor='migration'):factor_info.created_at → submit、
--      factor_state.entered_at → entered;rejected_at 不合成(最近 check-fail
--      事件已含该事实);
--   4. factor_state 删 rejected_at / last_fail_stage / last_fail_reason /
--      check_history(全部变 factor_history 派生);
--   5. factor_snapshot fields/tables JSONB → TEXT[](加列-改写-删旧-改名,
--      ALTER TYPE 的 USING 不允许子查询),GIN 索引重建(array_ops)。
--
-- 执行前备份(三表全量):
--   docker exec ops-pg pg_dump -U ops -d ops \
--     -t factor_info -t factor_state -t factor_snapshot > backup_before_v2b.sql
--
-- 前置守卫(违规即事务内 RAISE 回滚,什么都不会改):
--   check_history 里 passed=false 而 failed_stage 为空的元素(违反
--   chk_fail_has_stage)—— 出现说明有历史脏数据,停下判读,不要强推。
--
-- 幂等性:**非幂等,只跑一次**。二次执行会在第 2 步引用已删除的
-- check_history 列上报错回滚(净效果 no-op),但不要依赖这一点。
--
-- ⚠ 部署顺序(短窗口,全程禁 ops 写命令):本脚本 → 各机滚存新代码 → 复验。
--   旧代码读删掉的列会报错,窗口内 list/status 也不要跑。

BEGIN;

-- check_history 的时间戳是 naive 本地时间(ISO string 原样存 JSONB),
-- 与应用写入路径(ts_in 补本地 tz)同规:按上海时区解释
SET LOCAL TIME ZONE 'Asia/Shanghai';

-- 1. 建表(与 ops/infra/store/pg_store.py::_SCHEMA / init/01-schema.sql 同文)
CREATE TABLE IF NOT EXISTS factor_history (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,
    op TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL,
    actor TEXT,
    started_at TIMESTAMPTZ,
    passed BOOLEAN,
    failed_stage TEXT,
    fail_reason TEXT,
    CONSTRAINT chk_op CHECK (op IN ('submit', 'overwrite', 'check', 'approve', 'restage', 'cancel', 'rm', 'backfill', 'entered')),
    CONSTRAINT chk_fail_has_stage CHECK (passed IS DISTINCT FROM FALSE OR failed_stage IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_fh_name_at ON factor_history(name, at DESC);

-- 前置守卫:存量脏元素(会违反 chk_fail_has_stage)即停
DO $$
DECLARE bad bigint;
BEGIN
    SELECT count(*) INTO bad
    FROM factor_state s, jsonb_array_elements(s.check_history) c
    WHERE (c->>'passed')::boolean IS FALSE AND c->>'failed_stage' IS NULL;
    IF bad > 0 THEN
        RAISE EXCEPTION 'check_history 有 % 个 passed=false 且无 failed_stage 的脏元素,停止迁移待判读', bad;
    END IF;
END $$;

-- 2. check_history 展开(真实数据;WITH ORDINALITY 保原始顺序 → id 单调)
INSERT INTO factor_history (name, op, at, actor, started_at, passed, failed_stage, fail_reason)
SELECT s.name, 'check',
       COALESCE((c.val->>'finished_at')::timestamptz,
                (c.val->>'started_at')::timestamptz, s.updated_at),
       NULL,
       (c.val->>'started_at')::timestamptz,
       (c.val->>'passed')::boolean,
       c.val->>'failed_stage',
       c.val->>'fail_reason'
FROM factor_state s,
     jsonb_array_elements(s.check_history) WITH ORDINALITY AS c(val, ord)
ORDER BY s.name, c.ord;

-- 行数核对:事件数必须 == JSONB 元素总数,不符回滚
DO $$
DECLARE jsonb_cnt bigint; event_cnt bigint;
BEGIN
    SELECT coalesce(sum(jsonb_array_length(check_history)), 0) INTO jsonb_cnt
    FROM factor_state;
    SELECT count(*) INTO event_cnt FROM factor_history WHERE op = 'check';
    IF jsonb_cnt <> event_cnt THEN
        RAISE EXCEPTION 'check 事件 % != JSONB 元素 %,回滚', event_cnt, jsonb_cnt;
    END IF;
    RAISE NOTICE 'check 事件展开: % 条', event_cnt;
END $$;

-- 3. 生命周期合成(actor='migration' 标记非实录)
INSERT INTO factor_history (name, op, at, actor)
SELECT name, 'submit', created_at, 'migration' FROM factor_info;

INSERT INTO factor_history (name, op, at, actor)
SELECT name, 'entered', entered_at, 'migration'
FROM factor_state WHERE entered_at IS NOT NULL;

-- 4. factor_state 删派生列(v2b 后这些事实活在 factor_history)
ALTER TABLE factor_state
    DROP COLUMN IF EXISTS rejected_at,
    DROP COLUMN IF EXISTS last_fail_stage,
    DROP COLUMN IF EXISTS last_fail_reason,
    DROP COLUMN IF EXISTS check_history;

-- 5. fields/tables JSONB → TEXT[](USING 不许子查询,走加列-改写-删旧-改名)
ALTER TABLE factor_snapshot ADD COLUMN fields_arr TEXT[], ADD COLUMN tables_arr TEXT[];
UPDATE factor_snapshot SET
    fields_arr = CASE WHEN fields IS NULL THEN NULL
                 ELSE ARRAY(SELECT jsonb_array_elements_text(fields)) END,
    tables_arr = CASE WHEN tables IS NULL THEN NULL
                 ELSE ARRAY(SELECT jsonb_array_elements_text(tables)) END;
ALTER TABLE factor_snapshot DROP COLUMN fields, DROP COLUMN tables;  -- 旧 GIN 随列删
ALTER TABLE factor_snapshot RENAME COLUMN fields_arr TO fields;
ALTER TABLE factor_snapshot RENAME COLUMN tables_arr TO tables;
CREATE INDEX idx_factor_snapshot_fields ON factor_snapshot USING GIN(fields);
CREATE INDEX idx_factor_snapshot_tables ON factor_snapshot USING GIN(tables);

COMMIT;
