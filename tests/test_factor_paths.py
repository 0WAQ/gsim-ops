"""ops/core/paths.py FactorPaths 的布局契约测试(纯拼接,无 I/O、无 PG)。

盘面布局的正主从散布 40+ 处收编到 FactorPaths 后,这里把布局事实钉死:
路径拼法、feature 命名、单文件/目录之分的语义载体。改布局 = 改这里 + paths.py。
"""
from pathlib import Path
from types import SimpleNamespace

from ops.core.paths import FEATURE_VERSIONS, META_FILENAME, FactorPaths


def _fake_config(root: Path):
    return SimpleNamespace(
        alpha_src=root / "alpha_src",
        staging=root / "staging",
        alpha_dump=root / "alpha_dump",
        alpha_pnl=root / "alpha_pnl",
        pnl_automated=root / "pnl_automated",
        pnl_manual=root / "pnl_manual",
        alpha_feature=root / "alpha_feature",
    )


def test_layout(tmp_path):
    p = FactorPaths.of("AlphaX", _fake_config(tmp_path))
    assert p.src == tmp_path / "alpha_src" / "AlphaX"
    assert p.staging == tmp_path / "staging" / "AlphaX"
    assert p.dump == tmp_path / "alpha_dump" / "AlphaX"
    assert p.pnl == tmp_path / "alpha_pnl" / "AlphaX"          # 单文件
    assert p.pool_automated == tmp_path / "pnl_automated" / "AlphaX"
    assert p.pool_manual == tmp_path / "pnl_manual" / "AlphaX"


def test_feature_naming(tmp_path):
    p = FactorPaths.of("AlphaX", _fake_config(tmp_path))
    assert p.feature("v1") == tmp_path / "alpha_feature" / "AlphaX.v1.npy"
    assert p.features == tuple(
        tmp_path / "alpha_feature" / f"AlphaX.{v}.npy" for v in FEATURE_VERSIONS
    )
    assert FEATURE_VERSIONS == ("v1", "v2")


def test_pools_and_meta(tmp_path):
    p = FactorPaths.of("AlphaX", _fake_config(tmp_path))
    assert p.pools == (p.pool_automated, p.pool_manual)
    assert p.src_meta == p.src / META_FILENAME
    assert p.staging_meta == p.staging / META_FILENAME
    assert META_FILENAME == "meta.json"


def test_picklable(tmp_path):
    """pack 的 ProcessPool worker 直接收 FactorPaths —— 必须可 pickle。"""
    import pickle
    p = FactorPaths.of("AlphaX", _fake_config(tmp_path))
    assert pickle.loads(pickle.dumps(p)) == p
