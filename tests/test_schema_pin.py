"""DDL 双真相源 pin(schema v2a,2026-07-12):
scripts/postgres/init/01-schema.sql ⇔ 三个 pg_store._SCHEMA 常量。

背景(S2/P0-3):init SQL 是代码 DDL 的手抄镜像,"两处同改"靠人肉 —— 曾经
bootstrap 起出迁移前的旧世界。本测试把两处规范化后逐语句比对,drift 即红。
"""
import re

from ops.infra.config import get_project_root


def _statements(sql: str) -> list[str]:
    """去注释 → 按 ; 切语句 → 压平空白 → 排序(语句顺序不敏感,内容敏感)。"""
    no_comments = "\n".join(line.split("--")[0] for line in sql.splitlines())
    stmts = [re.sub(r"\s+", " ", s).strip()
             for s in no_comments.split(";")]
    return sorted(s for s in stmts if s)


def test_init_sql_mirrors_store_schemas():
    from ops.infra.info.pg_store import _SCHEMA as info_schema
    from ops.infra.snapshot.pg_store import _SCHEMA as snapshot_schema
    from ops.infra.store.pg_store import _SCHEMA as state_schema

    init_sql = (get_project_root() / "scripts" / "postgres" / "init"
                / "01-schema.sql").read_text()

    code_side = _statements(info_schema + state_schema + snapshot_schema)
    init_side = _statements(init_sql)
    assert init_side == code_side, (
        "init/01-schema.sql 与 pg_store._SCHEMA 漂移 —— 改表结构须两处同改。\n"
        f"仅在 init: {[s[:80] for s in set(init_side) - set(code_side)]}\n"
        f"仅在代码: {[s[:80] for s in set(code_side) - set(init_side)]}")
