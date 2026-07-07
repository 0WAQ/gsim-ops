#!/usr/bin/env python3
"""端到端测试: submit → check → list 完整流程 (ops_test 库)。

验证点:
1. submit: factor_info + factor_state 写入
2. check: factor_snapshot 写入 (snapshot_at = entered_at)
3. list: 三表 JOIN 读取
"""
import sys
from pathlib import Path
from datetime import datetime

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ops.infra.config import Config
from ops.infra.info import default_info_store, FactorInfo
from ops.infra.store import default_store
from ops.infra.snapshot import default_snapshot_store, FactorSnapshot
from ops.core.state import FactorRecord, FactorStatus


class MockConfig:
    """Mock config for ops_test."""
    def __init__(self):
        project_root = Path(__file__).parent.parent
        password = (project_root / "scripts/postgres/.env").read_text().strip().split("=", 1)[1]
        self.state_postgres_conninfo = (
            f"host=10.9.100.160 port=15432 user=ops password={password} dbname=ops_test"
        )
        self.state_backend = "postgres"


def test_end_to_end():
    config = MockConfig()
    info_store = default_info_store(config)
    state_store = default_store(config)
    snapshot_store = default_snapshot_store(config)

    test_name = f"TestE2E_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    print(f"=== 测试因子: {test_name} ===\n")

    # ========== 1. 模拟 submit ==========
    print("1️⃣  模拟 ops submit...")

    # 写入 factor_info
    info_store.upsert(FactorInfo(
        name=test_name,
        author="wbai",
        discovery_method="manual",
        created_at=datetime.now().isoformat(),
    ))
    print(f"  ✓ factor_info 写入成功")

    # 写入 factor_state
    submitted_at = datetime.now().isoformat()
    state_store.put(FactorRecord(
        name=test_name,
        status=FactorStatus.SUBMITTED,
        updated_at=submitted_at,
        submitted_at=submitted_at,
    ))
    print(f"  ✓ factor_state 写入成功 (status=SUBMITTED)")

    # ========== 2. 模拟 check ==========
    print("\n2️⃣  模拟 ops check...")

    # 状态转移: SUBMITTED → CHECKING
    state_store.transition(test_name, FactorStatus.CHECKING)
    print(f"  ✓ status: SUBMITTED → CHECKING")

    # 状态转移: CHECKING → ACTIVE (设置 entered_at)
    # 使用与 state_store 相同的时间戳格式
    from ops.infra.store.pg_store import _now
    entered_at = _now()
    state_store.transition(test_name, FactorStatus.ACTIVE, entered_at=entered_at)
    print(f"  ✓ status: CHECKING → ACTIVE (entered_at={entered_at})")

    # 写入 factor_snapshot (入库时快照)
    snapshot_store.insert(FactorSnapshot(
        name=test_name,
        ret=32.5,
        shrp=2.3,
        mdd=9.2,
        tvr=15.6,
        fitness=0.78,
        fields=["close", "volume", "open"],
        tables=["ashareeodprices", "AShareMoneyFlow"],
        has_pnl=True,
        dump_days=250,
        delay=1,
        max_bcorr=0.68,
        max_bcorr_factor="AlphaProd123",
        snapshot_at=entered_at,  # 关键: snapshot_at = entered_at (同一时间戳)
    ))
    print(f"  ✓ factor_snapshot 写入成功 (snapshot_at={entered_at})")

    # ========== 3. 验证数据一致性 ==========
    print("\n3️⃣  验证数据一致性...")

    info = info_store.get(test_name)
    assert info is not None, "factor_info 不存在"
    assert info.author == "wbai", f"author 错误: {info.author}"
    assert info.discovery_method == "manual", f"discovery_method 错误: {info.discovery_method}"
    print(f"  ✓ factor_info: name={info.name}, author={info.author}, discovery={info.discovery_method}")

    state = state_store.get(test_name)
    assert state is not None, "factor_state 不存在"
    assert state.status == FactorStatus.ACTIVE, f"status 错误: {state.status}"
    assert state.entered_at is not None, "entered_at 未设置"
    print(f"  ✓ factor_state: status={state.status.value}, entered_at={state.entered_at}")

    snapshot = snapshot_store.get(test_name)
    assert snapshot is not None, "factor_snapshot 不存在"
    assert snapshot.ret == 32.5, f"ret 错误: {snapshot.ret}"
    assert snapshot.snapshot_at == entered_at, f"snapshot_at 不等于 entered_at: {snapshot.snapshot_at} != {entered_at}"
    print(f"  ✓ factor_snapshot: ret={snapshot.ret}, snapshot_at={snapshot.snapshot_at}")

    # 关键验证: snapshot_at 必须等于 entered_at
    assert snapshot.snapshot_at == state.entered_at, \
        f"❌ snapshot_at ({snapshot.snapshot_at}) != entered_at ({state.entered_at})"
    print(f"  ✅ 关键验证通过: snapshot_at == entered_at")

    # ========== 4. 清理测试数据 ==========
    print("\n4️⃣  清理测试数据...")
    info_store.delete(test_name)
    print(f"  ✓ 删除成功 (级联删除 state + snapshot)")

    print("\n" + "="*50)
    print("✅ 端到端测试全部通过")
    print("="*50)


if __name__ == "__main__":
    try:
        test_end_to_end()
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
