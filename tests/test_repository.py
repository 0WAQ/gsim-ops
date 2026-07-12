"""FactorRepository 行为测试(factor-aggregate-plan 阶段 2)。

两组:
- **json 后端组**(CI 常跑,无 PG):产物面(ArtifactScope 两面语义)、
  记录面的降级语义(register 只写 state、get 合成 identity、discard no-op、
  find 拒绝)、lock 门面。
- **PG 组**(test_config,ops_test 不可达自动 skip;160 复跑):register 原子
  双表写、find 单条 JOIN 的因子集判据/过滤/快照拼装、attach_snapshot 的
  entered_at 强制 + stale 自愈、exists/delete 级联语义。
"""
from ops.core.factor import Factor, FactorIdentity, FactorSnapshot
from ops.core.state import FactorStatus
from ops.infra.repository import ArtifactScope, FactorRepository

# ---------------------------------------------------------------------------
# json 后端组(无 PG)
# ---------------------------------------------------------------------------

def _touch_artifacts(config, name: str) -> None:
    """铺满一个因子的全部产物落点(pnl/池副本/dump/feature)。"""
    (config.alpha_pnl / name).write_text("pnl")
    (config.pnl_automated / name).write_text("pool-a")
    (config.pnl_manual / name).write_text("pool-m")
    d = config.alpha_dump / name
    d.mkdir(parents=True)
    (d / "20260101v2.npy").write_text("x")
    (config.alpha_feature / f"{name}.v1.npy").write_text("f1")
    (config.alpha_feature / f"{name}.v2.npy").write_text("f2")


def test_purge_check_scope_only(json_config):
    """CHECK 面 = pnl + 两个池副本;SERVING 产物(dump/feature)不动。"""
    _, config = json_config
    repo = FactorRepository(config)
    _touch_artifacts(config, "AlphaWbaiScope")

    removed = repo.purge_artifacts("AlphaWbaiScope", ArtifactScope.CHECK)

    assert sorted(removed) == sorted([
        "alpha_pnl/AlphaWbaiScope",
        "pnl_automated/AlphaWbaiScope",
        "pnl_manual/AlphaWbaiScope",
    ])
    assert not (config.alpha_pnl / "AlphaWbaiScope").exists()
    assert (config.alpha_dump / "AlphaWbaiScope").exists()          # SERVING 保留
    assert (config.alpha_feature / "AlphaWbaiScope.v1.npy").exists()


def test_purge_serving_scope_only(json_config):
    _, config = json_config
    repo = FactorRepository(config)
    _touch_artifacts(config, "AlphaWbaiServ")

    removed = repo.purge_artifacts("AlphaWbaiServ", ArtifactScope.SERVING)

    assert sorted(removed) == sorted([
        "alpha_dump/AlphaWbaiServ",
        "alpha_feature/AlphaWbaiServ.v1.npy",
        "alpha_feature/AlphaWbaiServ.v2.npy",
    ])
    assert (config.alpha_pnl / "AlphaWbaiServ").exists()            # CHECK 保留
    assert not (config.alpha_dump / "AlphaWbaiServ").exists()


def test_purge_all_scope(json_config):
    _, config = json_config
    repo = FactorRepository(config)
    _touch_artifacts(config, "AlphaWbaiAll")

    removed = repo.purge_artifacts("AlphaWbaiAll", ArtifactScope.ALL)
    assert len(removed) == 6  # dump + 2 feature + pnl + 2 池副本


def test_json_register_get_delete(json_config):
    """json dev/test 后端的降级语义:register 只落 state;get 合成仅含 name
    的 identity;exists/delete 以 state 为准;discard_snapshot no-op。"""
    _, config = json_config
    repo = FactorRepository(config)

    assert repo.exists("AlphaWbaiJson") is False
    repo.register(
        FactorIdentity(name="AlphaWbaiJson", author="wbai",
                       discovery_method="manual", created_at="2026-07-09T00:00:00"),
        submitted_at="2026-07-09T00:00:00",
    )
    assert repo.exists("AlphaWbaiJson") is True

    rec = repo.record("AlphaWbaiJson")
    assert rec is not None
    assert rec.status == FactorStatus.SUBMITTED
    assert rec.version == 1
    assert rec.submitted_at == "2026-07-09T00:00:00"

    factor = repo.get("AlphaWbaiJson")
    assert isinstance(factor, Factor)
    assert factor.identity.name == "AlphaWbaiJson"
    assert factor.identity.author is None      # json 后端无 info 表,身份合成
    assert factor.snapshot is None
    assert factor.status == FactorStatus.SUBMITTED

    assert repo.discard_snapshot("AlphaWbaiJson") is False  # no-op
    assert repo.delete("AlphaWbaiJson") is True
    assert repo.exists("AlphaWbaiJson") is False


def test_json_find_unsupported(json_config):
    import pytest

    _, config = json_config
    with pytest.raises(NotImplementedError):
        FactorRepository(config).find()


def test_lock_facade(json_config):
    """repo.lock 是 factor_lock 门面:同名互斥,异名互不影响。"""
    import pytest

    from ops.infra.lock import FactorLocked, factor_lock

    _, config = json_config
    repo = FactorRepository(config)
    with repo.lock("AlphaWbaiLock"):
        with pytest.raises(FactorLocked), factor_lock("AlphaWbaiLock", config):
            pass
        with repo.lock("AlphaWbaiOther"):
            pass


def test_factor_aggregate_soft_invariant():
    """snapshot_at != entered_at → warn 不炸(读路径不因坏数据拒绝服务)。"""
    from ops.core.state import FactorRecord

    f = Factor(
        identity=FactorIdentity(name="AlphaX"),
        state=FactorRecord(name="AlphaX", status=FactorStatus.ACTIVE,
                           updated_at="2026-07-09T00:00:00",
                           entered_at="2026-07-09T00:00:00"),
        snapshot=FactorSnapshot(name="AlphaX", snapshot_at="2026-01-01T00:00:00"),
    )
    assert f.snapshot is not None  # 构造成功即通过(warn 走日志)


# ---------------------------------------------------------------------------
# PG 组(ops_test 不可达自动 skip;160 复跑)
# ---------------------------------------------------------------------------

def test_pg_register_atomic_two_tables(test_config):
    """register 一个事务写 info + state:两表都有行,author/状态字段落位。"""
    _, config = test_config
    repo = FactorRepository(config)

    repo.register(
        FactorIdentity(name="AlphaWbaiReg", author="wbai",
                       discovery_method="automated",
                       created_at="2026-07-09T10:00:00"),
        submitted_at="2026-07-09T10:00:00",
    )
    factor = repo.get("AlphaWbaiReg")
    assert factor is not None
    assert factor.identity.author == "wbai"
    assert factor.identity.discovery_method == "automated"
    assert factor.state is not None
    assert factor.state.status == FactorStatus.SUBMITTED
    assert factor.snapshot is None
    assert repo.exists("AlphaWbaiReg") is True


def test_pg_find_factor_set_and_filters(test_config, seed_factor):
    """find 的因子集判据(缺省排除 submitted)+ author/status 过滤 + 快照拼装。"""
    _, config = test_config
    repo = FactorRepository(config)

    seed_factor("AlphaWbaiActive", FactorStatus.ACTIVE,
                entered_at="2026-07-01T00:00:00")
    seed_factor("AlphaWbaiRej", FactorStatus.REJECTED,
                last_fail_stage="correlation", last_fail_reason="corr too high")
    seed_factor("AlphaWbaiSub", FactorStatus.SUBMITTED)
    seed_factor("AlphaLhwActive", FactorStatus.ACTIVE, author="lhw",
                entered_at="2026-07-01T00:00:00")

    names = {f.name for f in repo.find()}
    assert names == {"AlphaWbaiActive", "AlphaWbaiRej", "AlphaLhwActive"}  # 无 submitted

    by_author = repo.find(author="wbai")
    assert {f.name for f in by_author} == {"AlphaWbaiActive", "AlphaWbaiRej"}
    assert all(f.identity.author == "wbai" for f in by_author)

    explicit_sub = repo.find(status="submitted")
    assert {f.name for f in explicit_sub} == {"AlphaWbaiSub"}  # 显式查 submitted

    rej = repo.find(status=FactorStatus.REJECTED)
    assert len(rej) == 1 and rej[0].last_fail_stage == "correlation"
    assert rej[0].correlation_rejected()  # v2b: 谓词在 Factor 聚合(state+last_fail)


def test_pg_attach_snapshot_stamps_entered_at(test_config, seed_factor):
    """attach_snapshot 强制 snapshot_at = entered_at(调用方不填);
    重复 attach 走 stale 自愈(旧行让位,新值可见)。"""
    _, config = test_config
    repo = FactorRepository(config)
    seed_factor("AlphaWbaiSnap", FactorStatus.ACTIVE,
                entered_at="2026-07-02T12:00:00")

    repo.attach_snapshot(FactorSnapshot(name="AlphaWbaiSnap", ret=30.0, shrp=2.5))
    factor = repo.get("AlphaWbaiSnap")
    assert factor is not None and factor.snapshot is not None
    assert factor.snapshot.snapshot_at == "2026-07-02T12:00:00"
    assert factor.snapshot.ret == 30.0

    # stale 自愈:再 attach 不撞 UNIQUE,读到新值
    repo.attach_snapshot(FactorSnapshot(name="AlphaWbaiSnap", ret=99.0))
    factor2 = repo.get("AlphaWbaiSnap")
    assert factor2 is not None and factor2.snapshot is not None
    assert factor2.snapshot.ret == 99.0

    # find 侧拼出同一份快照
    found = repo.find(author="wbai", status=FactorStatus.ACTIVE)
    assert found[0].snapshot is not None and found[0].snapshot.ret == 99.0


def test_pg_snapshot_text_array_roundtrip_and_pushdown(test_config, seed_factor):
    """fields/tables TEXT[](v2b):list 直存直读 + GIN 包含 / glob LIKE 下推。"""
    _, config = test_config
    repo = FactorRepository(config)
    seed_factor("AlphaWbaiArr", FactorStatus.ACTIVE,
                entered_at="2026-07-02T12:00:00")
    seed_factor("AlphaWbaiArr2", FactorStatus.ACTIVE,
                entered_at="2026-07-02T12:00:00")
    repo.attach_snapshot(FactorSnapshot(
        name="AlphaWbaiArr",
        fields=["ashareeodprices.s_dq_close", "Interval5m.close"],
        tables=["ashareeodprices", "Interval5m"]))
    repo.attach_snapshot(FactorSnapshot(
        name="AlphaWbaiArr2", fields=["asharebalancesheet.accounts_payable"],
        tables=["asharebalancesheet"]))

    got = repo.get("AlphaWbaiArr")
    assert got is not None and got.snapshot is not None
    assert got.snapshot.fields == ["ashareeodprices.s_dq_close", "Interval5m.close"]

    by_field = repo.find(field="Interval5m.close")
    assert {f.name for f in by_field} == {"AlphaWbaiArr"}
    by_glob = repo.find(table_glob="ashare*")
    assert {f.name for f in by_glob} == {"AlphaWbaiArr", "AlphaWbaiArr2"}


def test_pg_attach_snapshot_requires_entered_at(test_config, seed_factor):
    import pytest

    _, config = test_config
    repo = FactorRepository(config)
    seed_factor("AlphaWbaiNoEnter", FactorStatus.SUBMITTED)  # 无 entered_at

    with pytest.raises(ValueError):
        repo.attach_snapshot(FactorSnapshot(name="AlphaWbaiNoEnter", ret=1.0))


def test_pg_delete_cascades(test_config, seed_factor):
    """delete = 删 factor_info,FK 级联 state + snapshot。"""
    _, config = test_config
    repo = FactorRepository(config)
    seed_factor("AlphaWbaiDel", FactorStatus.ACTIVE, entered_at="2026-07-01T00:00:00")
    repo.attach_snapshot(FactorSnapshot(name="AlphaWbaiDel", ret=1.0))

    assert repo.delete("AlphaWbaiDel") is True
    assert repo.get("AlphaWbaiDel") is None
    assert repo.record("AlphaWbaiDel") is None
    assert repo.delete("AlphaWbaiDel") is False  # 幂等:再删报"不存在"


def test_pg_find_include_submitted(test_config, seed_factor):
    """include_submitted=True = "任何记录"语义(status/cancel/pack 的批量
    resolve,2026-07-09 阶段 3);显式 status 给定时按其精确过滤,与该开关无关。"""
    _, config = test_config
    repo = FactorRepository(config)
    seed_factor("AlphaWbaiInclA", FactorStatus.ACTIVE, entered_at="2026-07-01T00:00:00")
    seed_factor("AlphaWbaiInclS", FactorStatus.SUBMITTED)

    assert {f.name for f in repo.find()} == {"AlphaWbaiInclA"}
    assert {f.name for f in repo.find(include_submitted=True)} == {
        "AlphaWbaiInclA", "AlphaWbaiInclS"}
    assert {f.name for f in repo.find(status="submitted", include_submitted=True)} == {
        "AlphaWbaiInclS"}


def test_pg_find_surfaces_info_orphans(test_config):
    """info 有行、state 无行的孤儿(register 事务化前的半截写入/手工残留)——
    include_submitted=True 的"任何记录"语义必须让它现形(state=None,status/
    cancel 渲染"需对账");缺省因子集判据(!= 'submitted' 对 NULL 为假)则
    天然排除它,ops list 不受影响。评审修正:find 的 factor_state 边原是
    INNER JOIN,孤儿分支不可达。"""
    from ops.infra.info import FactorInfo, default_info_store

    _, config = test_config
    repo = FactorRepository(config)
    # 只写 info,不写 state —— 制造孤儿
    default_info_store(config).upsert(FactorInfo(
        name="AlphaWbaiOrphan", author="wbai", discovery_method="manual",
        created_at="2026-07-09T00:00:00"))

    assert repo.find() == []                                # 库内因子集不含孤儿
    orphans = repo.find(include_submitted=True)
    assert len(orphans) == 1
    assert orphans[0].name == "AlphaWbaiOrphan"
    assert orphans[0].state is None                         # 孤儿现形
    assert orphans[0].identity.author == "wbai"
    # 显式 status 过滤对 NULL 为假,不误纳
    assert repo.find(status="submitted", include_submitted=True) == []


# ---------------------------------------------------------------------------
# 产物面:archive / recall / unstage(2026-07-10 阶段 3 第二批,json 组)
# ---------------------------------------------------------------------------

def test_archive_moves_all_and_splits_pool(json_config, write_factor):
    """archive 收编 to_lib:src 搬 alpha_src(+@module 重指)、dump/pnl 搬库、
    按来源分流池副本。"""
    _, config = json_config
    repo = FactorRepository(config)
    d = write_factor(config, name="AlphaWbaiArc", discovery_method="manual")
    dump = config.alpha_path / "AlphaWbaiArc"
    dump.mkdir(parents=True)
    (dump / "20260101v2.npy").write_text("x")
    pnl = config.pnl_path / "AlphaWbaiArc"
    pnl.write_text("pnl")

    repo.archive("AlphaWbaiArc", src_dir=d, dump_dir=dump, pnl_file=pnl,
                 discovery_method="manual")

    src = config.alpha_src / "AlphaWbaiArc"
    assert (src / "AlphaWbaiArc.py").exists() and not d.exists()
    assert (config.alpha_dump / "AlphaWbaiArc" / "20260101v2.npy").exists()
    assert (config.alpha_pnl / "AlphaWbaiArc").read_text() == "pnl"
    assert (config.pnl_manual / "AlphaWbaiArc").exists()      # 按来源分流
    assert not (config.pnl_automated / "AlphaWbaiArc").exists()
    # @module 重指到入库后的 .py(独立可跑)
    xml = (src / "Config.AlphaWbaiArc.xml").read_text()
    assert str(src / "AlphaWbaiArc.py") in xml


def test_archive_refuses_identity_divergence(json_config, write_factor):
    """身份兜底断言随迁 repository:src_dir.name != name → 拒绝归档,原物不动。"""
    import pytest

    _, config = json_config
    repo = FactorRepository(config)
    d = write_factor(config, name="AlphaWbaiImp")

    with pytest.raises(RuntimeError, match="identity divergence"):
        repo.archive("AlphaWbaiVictim", src_dir=d,
                     dump_dir=config.alpha_path / "x",
                     pnl_file=config.pnl_path / "x", discovery_method="manual")
    assert d.exists()                                          # 原地未动


def test_recall_roundtrip_and_guards(json_config, write_factor):
    """recall 收编 restage 搬运:alpha_src → staging(move,唯一副本)+ @module
    重指;src 缺失 / staging 占用的守卫。"""
    import pytest

    _, config = json_config
    repo = FactorRepository(config)
    d = write_factor(config, name="AlphaWbaiRec", discovery_method="manual")
    dump = config.alpha_path / "AlphaWbaiRec"
    dump.mkdir(parents=True)
    pnl = config.pnl_path / "AlphaWbaiRec"
    pnl.write_text("pnl")
    repo.archive("AlphaWbaiRec", src_dir=d, dump_dir=dump, pnl_file=pnl,
                 discovery_method="manual")

    repo.recall("AlphaWbaiRec")

    staging = config.staging / "AlphaWbaiRec"
    assert staging.exists() and not (config.alpha_src / "AlphaWbaiRec").exists()
    xml = (staging / "Config.AlphaWbaiRec.xml").read_text()
    assert str(staging / "AlphaWbaiRec.py") in xml             # @module 指回 staging

    with pytest.raises(FileNotFoundError):                     # src 已不在
        repo.recall("AlphaWbaiRec")
    # src 重新出现但 staging 被占 → 拒绝覆盖
    (config.alpha_src / "AlphaWbaiRec").mkdir(parents=True)
    with pytest.raises(FileExistsError):
        repo.recall("AlphaWbaiRec")


def test_unstage(json_config, write_factor):
    _, config = json_config
    repo = FactorRepository(config)
    d = write_factor(config, name="AlphaWbaiUn")
    assert repo.unstage("AlphaWbaiUn") is True and not d.exists()
    assert repo.unstage("AlphaWbaiUn") is False                # 幂等:再删报不存在
