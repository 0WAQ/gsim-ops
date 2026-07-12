-- v2a 迁移: factor_state 加状态↔时间戳一致性 CHECK(schema-v2 设计,2026-07-12)
--
-- 背景: "ACTIVE 必有入库时刻"是全部写路径(check archive / approve / backfill)
-- 已遵守的不变量,但只靠应用自觉。上约束后,"不该 NULL 的状态下 NULL"
-- (doctor snapshot-stale/illegal 那类漂移的近亲)在写入口被数据库拒掉。
--
-- 执行前备份:
--   pg_dump -h 10.9.100.160 -p 15432 -U ops -d ops -t factor_state \
--     > backup_state_before_v2a_check.sql
--
-- 幂等: 先 DROP IF EXISTS 再 ADD。

BEGIN;

-- 前置验证: 存量必须全部满足,否则 ADD CONSTRAINT 整个事务失败回滚(即本
-- 脚本天然自带守卫 —— 有违规行时什么都不会改,把下面这句的结果贴出来判读)。
SELECT count(*) AS violating_rows
FROM factor_state WHERE status = 'active' AND entered_at IS NULL;

ALTER TABLE factor_state DROP CONSTRAINT IF EXISTS chk_active_entered;
ALTER TABLE factor_state ADD CONSTRAINT chk_active_entered
    CHECK (status <> 'active' OR entered_at IS NOT NULL);

COMMIT;
