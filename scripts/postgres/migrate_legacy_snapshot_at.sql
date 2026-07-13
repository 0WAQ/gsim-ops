-- legacy 清理批 ①(2026-07-13):历史 snapshot_at 漂移修正(判读锚点 472,全库)。
--
-- 背景:schema v3 起 snapshot_at = 测得时刻(该次 check 事件的 at,factor_history
-- 是测得时刻的 SSOT)。v3 上线复验时 doctor snapshot-stale 新判据暴露存量漂移
-- 472 条(2026-07-13 生产,全库范围):fguo 批 snapshot_at 统一 16:38:27(三表
-- 迁移批量写入时刻,非测得时刻)、lhw 批差 8h(老时区错位)。真实测得时刻都在
-- factor_history 的 check 事件里 —— 本脚本把 snapshot_at 拉回正轨。
--
-- 判据与 ops/services/doctor/checks.py::_scan_snapshot_stale 逐字对齐:
--   期望值 = 最近一次 check 事件 at(DISTINCT ON ... ORDER BY at DESC, id DESC,
--   与 store.latest_check_ats 同序);无任何 check 事件的 legacy 锚 entered_at。
--   unanchored(无事件且 entered_at 空)不在本脚本范围(盘点归档,批内项④)。
--
-- 幂等:二跑两段 UPDATE 均 0 行、守卫与断言照常通过。
-- 执行前全量预览(dry-run 等价,只读):
--   SELECT count(*) FROM factor_snapshot n JOIN factor_state st USING (name)
--   LEFT JOIN (SELECT DISTINCT ON (name) name, at FROM factor_history
--              WHERE op='check' ORDER BY name, at DESC, id DESC) lc USING (name)
--   WHERE coalesce(lc.at, st.entered_at) IS NOT NULL
--     AND n.snapshot_at IS DISTINCT FROM coalesce(lc.at, st.entered_at);
--
-- 用法(160):
--   docker exec -i ops-pg psql -U ops -d ops -v ON_ERROR_STOP=1 \
--     < scripts/postgres/migrate_legacy_snapshot_at.sql
BEGIN;

-- 前置计数 + 规模守卫(判读锚点 2026-07-13:mismatch=472,全库;超 600 视为
-- 环境/判据异常,整体回滚待判读。二跑 0 行属预期,不触发守卫)
DO $$
DECLARE n_check bigint; n_legacy bigint;
BEGIN
    SELECT count(*) INTO n_check
    FROM factor_snapshot n
    JOIN (SELECT DISTINCT ON (name) name, at FROM factor_history
          WHERE op = 'check' ORDER BY name, at DESC, id DESC) lc USING (name)
    WHERE n.snapshot_at IS DISTINCT FROM lc.at;

    SELECT count(*) INTO n_legacy
    FROM factor_snapshot n
    JOIN factor_state st USING (name)
    WHERE st.entered_at IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM factor_history h
                      WHERE h.name = n.name AND h.op = 'check')
      AND n.snapshot_at IS DISTINCT FROM st.entered_at;

    RAISE NOTICE '待修正: 有 check 事件 % 行 + 无事件锚 entered_at % 行 (判读锚点 472, 全库)',
        n_check, n_legacy;
    IF n_check + n_legacy > 600 THEN
        RAISE EXCEPTION '总数 % 超守卫 600 —— 环境/判据异常, 回滚待判读',
            n_check + n_legacy;
    END IF;
END $$;

-- 主段:有 check 事件 → snapshot_at := 最近 check 事件 at
UPDATE factor_snapshot n
SET snapshot_at = lc.at
FROM (SELECT DISTINCT ON (name) name, at FROM factor_history
      WHERE op = 'check' ORDER BY name, at DESC, id DESC) lc
WHERE lc.name = n.name AND n.snapshot_at IS DISTINCT FROM lc.at;

-- 补段:无任何 check 事件的 legacy → 锚 entered_at(doctor 同判据的另一翼;
-- 预期 0 行 —— entered_at 侧存量已由 migrate_snapshot_at.py 于 doctor v1 批修平)
UPDATE factor_snapshot n
SET snapshot_at = st.entered_at
FROM factor_state st
WHERE st.name = n.name AND st.entered_at IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM factor_history h
                  WHERE h.name = n.name AND h.op = 'check')
  AND n.snapshot_at IS DISTINCT FROM st.entered_at;

-- 后置断言:doctor mismatch 判据残余必须为 0(否则整体回滚)
DO $$
DECLARE residual bigint;
BEGIN
    SELECT count(*) INTO residual
    FROM factor_snapshot n
    JOIN factor_state st USING (name)
    LEFT JOIN (SELECT DISTINCT ON (name) name, at FROM factor_history
               WHERE op = 'check' ORDER BY name, at DESC, id DESC) lc USING (name)
    WHERE coalesce(lc.at, st.entered_at) IS NOT NULL
      AND n.snapshot_at IS DISTINCT FROM coalesce(lc.at, st.entered_at);
    IF residual > 0 THEN
        RAISE EXCEPTION '后置断言失败: mismatch 残余 % 行, 整体回滚', residual;
    END IF;
    RAISE NOTICE '后置断言通过: mismatch 残余 0';
END $$;

COMMIT;
