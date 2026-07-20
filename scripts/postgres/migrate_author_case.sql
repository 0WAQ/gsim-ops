-- author 大小写归一(2026-07-20,plans.md 登记的 BUG)。
-- 成因:2026-05-20~27 一批 GA 因子的 XML <Description author="Fguo"> 大写,
-- 当时解析代码原文采用落库;后 factormeta.py 已加小写归一(无新增)。
-- 查询层 `ops list -u` 的 LowerAction 使大写行从 CLI 不可见。
-- 幂等,可重跑;执行前已 pg_dump(dumps/ops-20260720-1122.sql.gz)。
BEGIN;

UPDATE factor_info SET author = lower(author) WHERE author <> lower(author);

-- 防复发校验:应归 0
DO $$
DECLARE n INT;
BEGIN
    SELECT count(*) INTO n FROM factor_info WHERE author <> lower(author);
    IF n > 0 THEN
        RAISE EXCEPTION 'author 归一失败,残余 % 行', n;
    END IF;
END $$;

COMMIT;
