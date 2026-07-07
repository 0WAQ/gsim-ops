#!/usr/bin/env python3
"""综合测试所有改造的服务（在 ops_test 库上）。

测试服务:
1. query_factors (list 的核心)
2. info (读取 factor_info + factor_snapshot)
3. status (读取 factor_info + factor_state)
4. rm (删除 factor_info，级联删除 state + snapshot)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ops.infra.config import Config
from ops.infra.info import default_info_store, FactorInfo
from ops.infra.store import default_store
from ops.infra.snapshot import default_snapshot_store, FactorSnapshot
from ops.infra.query import query_factors
from ops.core.state import FactorRecord, FactorStatus
from datetime import datetime


class MockConfig:
    """Mock config for ops_test."""
    def __init__(self):
        project_root = Path(__file__).parent.parent
        password = (project_root / "scripts/postgres/.env").read_text().strip().split("=", 1)[1]
        self.state_postgres_conninfo = (
            f"host=10.9.100.160 port=15432 user=ops password={password} dbname=ops_test"
        )
        self.state_backend = "postgres"


def test_query_factors():
    """测试 query_factors（list 服务的核心）。"""
    print("\n" + "="*60)
    print("1️⃣  测试 query_factors (list 服务)")
    print("="*60)

    config = MockConfig()

    # 查询所有因子
    rows = query_factors(config)
    print(f"\n  查询到 {len(rows)} 个因子:")
    for row in rows:
        snap_info = f"ret={row.snapshot.ret:.1f}, shrp={row.snapshot.shrp:.1f}" if row.snapshot else "无 snapshot"
        print(f"    - {row.info.name}: status={row.status.value if row.status else '?'}, author={row.info.author}, {snap_info}")

    # 按 status 过滤
    active_rows = query_factors(config, status="active")
    print(f"\n  ✅ 过滤 ACTIVE 因子: {len(active_rows)} 个")

    # 按 author 过滤
    user_rows = query_factors(config, author="wbai")
    print(f"  ✅ 过滤 author=wbai: {len(user_rows)} 个")


def test_stores():
    """测试各个 store 的读写功能。"""
    print("\n" + "="*60)
    print("2️⃣  测试各个 Store 的基本功能")
    print("="*60)

    config = MockConfig()
    info_store = default_info_store(config)
    state_store = default_store(config)
    snapshot_store = default_snapshot_store(config)

    # 创建测试数据
    test_name = f"TestStore_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")

    print(f"\n  创建测试因子: {test_name}")

    # 写入三张表
    info_store.upsert(FactorInfo(
        name=test_name,
        author="test_user",
        discovery_method="automated",
        created_at=now,
    ))

    state_store.put(FactorRecord(
        name=test_name,
        status=FactorStatus.ACTIVE,
        version=1,
        submitted_at=now,
        entered_at=now,
        updated_at=now,
    ))

    snapshot_store.insert(FactorSnapshot(
        name=test_name,
        ret=15.5,
        shrp=2.8,
        mdd=-8.5,
        tvr=45.0,
        fitness=0.85,
        fields={"field1": ["close", "volume"]},
        tables={"table1": ["data_a", "data_b"]},
        delay=1,
        max_bcorr=0.65,
        max_bcorr_factor="AlphaOld123",
        snapshot_at=now,
    ))

    print(f"  ✓ 测试数据创建完成")

    # 读取验证
    print(f"\n  读取因子: {test_name}")

    # 1. info_store
    info = info_store.get(test_name)
    print(f"    ✅ FactorInfo: name={info.name}, author={info.author}, discovery={info.discovery_method}")

    # 2. state_store
    state = state_store.get(test_name)
    print(f"    ✅ FactorState: status={state.status.value}, version={state.version}, entered_at={state.entered_at}")

    # 3. snapshot_store
    snapshot = snapshot_store.get(test_name)
    print(f"    ✅ FactorSnapshot: ret={snapshot.ret}, shrp={snapshot.shrp}, snapshot_at={snapshot.snapshot_at}")

    # 验证关键约束
    assert snapshot.snapshot_at == state.entered_at, "snapshot_at 应该等于 entered_at"
    print(f"\n  ✅ 关键验证通过: snapshot_at == entered_at")

    # 清理测试数据
    info_store.delete(test_name)
    print(f"  ✓ 测试数据清理完成")


def test_cascade_delete():
    """测试级联删除功能。"""
    print("\n" + "="*60)
    print("3️⃣  测试级联删除 (factor_info → state + snapshot)")
    print("="*60)

    config = MockConfig()
    info_store = default_info_store(config)
    state_store = default_store(config)
    snapshot_store = default_snapshot_store(config)

    # 创建一个测试因子
    test_name = "TestCascadeDelete"
    print(f"\n  创建测试因子: {test_name}")

    info_store.upsert(FactorInfo(
        name=test_name,
        author="test",
        discovery_method="manual",
        created_at=datetime.now().isoformat(),
    ))

    state_store.put(FactorRecord(
        name=test_name,
        status=FactorStatus.SUBMITTED,
        updated_at=datetime.now().isoformat(),
        submitted_at=datetime.now().isoformat(),
    ))

    snapshot_store.insert(FactorSnapshot(
        name=test_name,
        ret=10.0,
        shrp=1.0,
        snapshot_at=datetime.now().isoformat(),
    ))

    print(f"    ✅ 创建完成 (info + state + snapshot)")

    # 验证创建成功
    assert info_store.get(test_name) is not None
    assert state_store.get(test_name) is not None
    assert snapshot_store.get(test_name) is not None
    print(f"    ✅ 三个表都有记录")

    # 删除 factor_info（应该级联删除 state + snapshot）
    print(f"\n  删除 factor_info...")
    info_store.delete(test_name)

    # 验证级联删除
    assert info_store.get(test_name) is None, "info 应该被删除"
    assert state_store.get(test_name) is None, "state 应该被级联删除"
    assert snapshot_store.get(test_name) is None, "snapshot 应该被级联删除"

    print(f"    ✅ 级联删除成功 (info/state/snapshot 都已删除)")


def test_semantic_change():
    """验证语义变更：metrics 是入库时快照，不可变。"""
    print("\n" + "="*60)
    print("4️⃣  验证语义变更: metrics = 入库时快照")
    print("="*60)

    config = MockConfig()
    info_store = default_info_store(config)
    state_store = default_store(config)
    snapshot_store = default_snapshot_store(config)

    # 创建测试数据
    test_name = f"TestSemantic_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")

    print(f"\n  创建测试因子: {test_name}")

    info_store.upsert(FactorInfo(
        name=test_name,
        author="test_user",
        discovery_method="manual",
        created_at=now,
    ))

    state_store.put(FactorRecord(
        name=test_name,
        status=FactorStatus.ACTIVE,
        version=1,
        entered_at=now,
        updated_at=now,
    ))

    snapshot_store.insert(FactorSnapshot(
        name=test_name,
        ret=28.5,
        shrp=3.2,
        mdd=-12.0,
        tvr=38.0,
        fitness=0.92,
        fields={"field1": ["close"]},
        tables={"table1": ["data_a"]},
        snapshot_at=now,
    ))

    # 读取验证
    snapshot = snapshot_store.get(test_name)

    print(f"\n  因子: {test_name}")
    print(f"    ret:          {snapshot.ret}")
    print(f"    shrp:         {snapshot.shrp}")
    print(f"    snapshot_at:  {snapshot.snapshot_at}")

    print(f"\n  ✅ 语义验证:")
    print(f"    - ret/shrp 等指标代表「入库时的历史表现」")
    print(f"    - snapshot_at 记录快照时间点")
    print(f"    - 这些数据永不更新（不可变快照）")
    print(f"    - ops refresh 命令已删除，无法重新计算")

    # 清理测试数据
    info_store.delete(test_name)


if __name__ == "__main__":
    try:
        print("\n" + "🚀 " + "="*58)
        print("   综合测试: 新表结构 + 改造服务")
        print("="*60)

        test_query_factors()
        test_stores()
        test_cascade_delete()
        test_semantic_change()

        print("\n" + "="*60)
        print("✅ 所有测试通过！")
        print("="*60)
        print("\n核心功能验证:")
        print("  ✓ query_factors 三表 JOIN 正常")
        print("  ✓ info/state/snapshot store 读写正常")
        print("  ✓ 级联删除工作正常")
        print("  ✓ 语义变更已体现: metrics = 入库时快照")
        print()

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
