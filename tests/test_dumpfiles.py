"""core/dumpfiles + core/universe 单测(无需 PG / gsim)。

dumpfiles 是 alpha_dump 逐日布局的走查正主(pack 已切换,produce 消费);
universe 是 cc 数据根元数据读取(轴 + .meta 快照锁)。
"""
import numpy as np
import pytest

from ops.core.dumpfiles import dump_dates, iter_dump_files, month_dir, parse_dump_name
from ops.core.universe import CcMeta, load_universe, read_cc_meta

# ---------------------------------------------------------------------------
# dumpfiles
# ---------------------------------------------------------------------------

def test_parse_dump_name_both_spellings():
    """存量并存无点 / 有点两种写法,解析都认;非布局名拒绝。"""
    assert parse_dump_name("20260102v2.npy") == (20260102, "v2")
    assert parse_dump_name("20260102.v1.npy") == (20260102, "v1")
    assert parse_dump_name("20260102v3.npy") is None       # 未知版本
    assert parse_dump_name("20260102v2x.npy") is None      # 尾随杂质(旧 pack 解析会误收)
    assert parse_dump_name("2026010v2.npy") is None        # 日期不足 8 位
    assert parse_dump_name("readme.txt") is None
    assert parse_dump_name("20260102v2") is None           # 缺 .npy


def _mk(dump_dir, rel):
    f = dump_dir / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.touch()


def test_iter_and_dump_dates(tmp_path):
    d = tmp_path / "AlphaX"
    assert list(iter_dump_files(d)) == []          # 目录不存在 → 空
    assert dump_dates(d) == set()

    _mk(d, "2026/01/20260105v1.npy")
    _mk(d, "2026/01/20260105v2.npy")
    _mk(d, "2026/01/20260106v2.npy")               # 半日:只有 v2
    _mk(d, "2026/02/20260202.v1.npy")              # 有点写法
    _mk(d, "2026/02/20260202.v2.npy")
    (d / "logs").mkdir()                           # 非布局目录忽略
    _mk(d, "2026/01/notes.txt")                    # 非布局文件忽略

    assert dump_dates(d) == {20260105, 20260106, 20260202}
    # require_both:半日按缺失计(安装中断自愈的判据)
    assert dump_dates(d, require_both=True) == {20260105, 20260202}


def test_month_dir():
    from pathlib import Path
    assert month_dir(Path("/x"), 20260105) == Path("/x/2026/01")


# ---------------------------------------------------------------------------
# universe
# ---------------------------------------------------------------------------

def _write_universe(root, dates):
    uni = root / "__universe"
    uni.mkdir(parents=True)
    np.array(dates, dtype=np.int64).tofile(uni / "Dates.npy")
    np.array(["000001", "000002"], dtype="U32").tofile(uni / "Instruments.npy")


def test_load_universe_raw_memmap(tmp_path):
    """轴文件是 gsim 自定义二进制(无 numpy header),raw memmap 直读。"""
    _write_universe(tmp_path, [20260105, 20260106, 20260107])
    dates, ins, date_to_idx = load_universe(tmp_path)
    assert list(dates) == [20260105, 20260106, 20260107]
    assert list(ins) == ["000001", "000002"]
    assert date_to_idx[20260106] == 1


def test_read_cc_meta(tmp_path):
    (tmp_path / ".meta").write_text("20260716\ndateCapacity 4032\ninstrumentCapacity 5484\n")
    assert read_cc_meta(tmp_path) == CcMeta(
        last_date=20260716, date_capacity=4032, instrument_capacity=5484)


def test_read_cc_meta_loud_failure(tmp_path):
    """缺失 / 格式异常必须响亮抛 —— .meta 是 gsim 可见性唯一凭据,静默猜值
    = 就绪判定失真。"""
    with pytest.raises(FileNotFoundError):
        read_cc_meta(tmp_path)
    (tmp_path / ".meta").write_text("garbage\n")
    with pytest.raises(ValueError):
        read_cc_meta(tmp_path)
