"""produce 分组 roster 的 PG 真身测试(ops_test 不可达自动 skip)。

钉的是 roster 的承重语义:ordinal 落库即序(checkpoint 腿序号)、muted 翻转、
active_membership 只含 active 组、supersede 后 roster 留痕但不再占位、
FactorRepository 门面与 ungrouped_delay1 的 delay 过滤。
"""
import pytest

from ops.core.factor import FactorSnapshot
from ops.core.state import FactorStatus
from ops.infra.groups.pg_store import PostgresGroupStore, ProduceGroup
from ops.infra.repository import FactorRepository

pytestmark = pytest.mark.pg


def test_create_list_members(test_config):
    _, config = test_config
    store = PostgresGroupStore(config.state_postgres_conninfo)

    store.create_group(ProduceGroup(gid="g101", author="wbai", delay=1),
                       ["AlphaA", "AlphaB", "AlphaC"])

    groups = store.list_groups()
    assert [(g.gid, g.author, g.delay, g.status) for g in groups] == [
        ("g101", "wbai", 1, "active")]
    members = store.members("g101")
    assert [(m.factor, m.ordinal, m.muted) for m in members] == [
        ("AlphaA", 0, False), ("AlphaB", 1, False), ("AlphaC", 2, False)]
    assert store.active_membership() == {
        "AlphaA": "g101", "AlphaB": "g101", "AlphaC": "g101"}


def test_set_muted_roundtrip(test_config):
    _, config = test_config
    store = PostgresGroupStore(config.state_postgres_conninfo)
    store.create_group(ProduceGroup(gid="g102", author="wbai", delay=1),
                       ["AlphaA", "AlphaB"])

    store.set_muted("g102", {"AlphaB"}, True)
    assert [(m.factor, m.muted) for m in store.members("g102")] == [
        ("AlphaA", False), ("AlphaB", True)]
    store.set_muted("g102", {"AlphaB"}, False)
    assert [(m.factor, m.muted) for m in store.members("g102")] == [
        ("AlphaA", False), ("AlphaB", False)]


def test_supersede_keeps_roster_but_vacates_membership(test_config):
    _, config = test_config
    store = PostgresGroupStore(config.state_postgres_conninfo)
    store.create_group(ProduceGroup(gid="g103", author="wbai", delay=1), ["AlphaSS"])

    store.supersede("g103")

    # 同 session 其它组仍 active(共享 schema),断言只对本组作用域
    assert "g103" not in [g.gid for g in store.list_groups()]
    assert "AlphaSS" not in store.active_membership()
    # 旧组留痕不复号(重组语义:active_only=False 仍可见)
    assert "g103" in [g.gid for g in store.list_groups(active_only=False)]


def test_repository_group_facade_and_ungrouped(test_config, seed_factor):
    _, config = test_config
    repo = FactorRepository(config)
    for name, delay in (("AlphaD1a", 1), ("AlphaD1b", 1), ("AlphaD0", 0)):
        seed_factor(name, FactorStatus.ACTIVE, author="wbai")
        repo.attach_snapshot(
            FactorSnapshot(name=name, delay=delay),
            measured_at="2026-07-18T00:00:00")

    repo.create_group("g104", "wbai", 1, ["AlphaD1a"])

    # 共享 session schema 下其它测试也在建组,membership 断言只取本组键
    assert repo.group_membership().get("AlphaD1a") == "g104"
    assert [(m.factor, m.ordinal) for m in repo.group_members("g104")] == [
        ("AlphaD1a", 0)]
    # ungrouped_delay1:delay0 与已入组的都不在列(同 session 其它 seeded 因子
    # 可能也在结果里,断言只钉排除语义)
    ungrouped = repo.ungrouped_delay1()
    assert ("AlphaD1b", "wbai", 1) in ungrouped
    assert not any(n == "AlphaD1a" for n, _, _ in ungrouped)
    assert not any(n == "AlphaD0" for n, _, _ in ungrouped)

    repo.set_group_muted("g104", {"AlphaD1a"}, True)
    assert repo.group_members("g104")[0].muted is True
