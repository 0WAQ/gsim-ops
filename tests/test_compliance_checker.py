"""ComplianceChecker 单测(2026-07-16 重做:全史每日 + 容忍 + 硬顶)。

快、无 gsim:直接喂合成 dump 向量给 checker,验四条政策不变量 ——
跳过无效日 / 软线容忍 K / 硬顶单日立拒 / 全空跳过。逐日 numpy 表达式与
scripts/compliance_survey.py 同源,violations.csv 是生产侧影子回归材料。
"""
from types import SimpleNamespace

import numpy as np
import pytest

from ops.services.check.checker.base import CheckFail, CheckSkip
from ops.services.check.checker.compliance_checker import ComplianceChecker

CFG = {
    "max_position_pct": 0.05,
    "min_total_stocks": 100,
    "min_long_stocks": 50,
    "min_short_stocks": 50,
    "violation_tolerance": 10,
    "hard_position_mult": 2.0,          # 硬顶 = 10%
}


def _checker(**overrides):
    c = dict(CFG, **overrides)
    return ComplianceChecker(SimpleNamespace(compliance=c))


def _factor(alpha_dir):
    return SimpleNamespace(alpha_dir=alpha_dir)


def _write_day(root, date: str, weights: np.ndarray):
    """写一份 <root>/YYYY/MM/<yyyymmdd>.v2.npy(v2npy_files 的盘面布局)。"""
    d = root / date[:4] / date[4:6]
    d.mkdir(parents=True, exist_ok=True)
    # 文件名已以 .npy 结尾,np.save 不再追加 —— 直接落成 glob 的 *v2.npy
    np.save(d / f"{date}.v2.npy", weights)


def _clean(n_long=60, n_short=60):
    """合规日:多空各 n 只、等权,单股占比 = 1/(n_long+n_short) 远低于 5%。"""
    return np.array([1.0] * n_long + [-1.0] * n_short)


def _dates(n, start=20200101):
    # 仅需唯一、可排序的 8 位串;不必是真交易日(checker 只取 name[:8] 当标签)
    return [str(start + i) for i in range(n)]


def test_all_clean_passes(tmp_path):
    for dt in _dates(30):
        _write_day(tmp_path, dt, _clean())
    res = _checker().check(_factor(tmp_path))
    assert res.total_checked == 30


def test_violations_within_tolerance_pass(tmp_path):
    dates = _dates(30)
    for dt in dates[:8]:                 # 8 个违规日 <= 容忍 10
        _write_day(tmp_path, dt, _clean(n_long=10, n_short=10))   # total 20 < 100
    for dt in dates[8:]:
        _write_day(tmp_path, dt, _clean())
    res = _checker().check(_factor(tmp_path))   # 不抛 = 放行毛刺
    assert res.total_checked == 30


def test_violations_over_tolerance_reject(tmp_path):
    dates = _dates(30)
    for dt in dates[:15]:                # 15 个违规日 > 容忍 10
        _write_day(tmp_path, dt, _clean(n_long=10, n_short=10))
    for dt in dates[15:]:
        _write_day(tmp_path, dt, _clean())
    with pytest.raises(CheckFail, match="超容忍"):
        _checker().check(_factor(tmp_path))


def test_hard_ceiling_single_day_reject(tmp_path):
    """单日个股 > 10% 立拒,即使总违规日数在容忍内。"""
    dates = _dates(30)
    for dt in dates[1:]:
        _write_day(tmp_path, dt, _clean())
    # 1 只 90 + 99 只 ±0.1 → max/total ≈ 0.90 > 硬顶 0.10;仅此 1 天违规 <= 容忍
    spike = np.array([90.0] + [0.1] * 50 + [-0.1] * 49)
    _write_day(tmp_path, dates[0], spike)
    with pytest.raises(CheckFail, match="硬顶"):
        _checker().check(_factor(tmp_path))


def test_hard_ceiling_beats_tolerance(tmp_path):
    """硬顶优先于容忍:即便软线违规日已超容忍,报的也应是硬顶(单日立拒语义)。"""
    dates = _dates(30)
    spike = np.array([90.0] + [0.1] * 50 + [-0.1] * 49)
    _write_day(tmp_path, dates[0], spike)
    for dt in dates[1:]:                 # 29 个软线违规日,远超容忍
        _write_day(tmp_path, dt, _clean(n_long=5, n_short=5))
    with pytest.raises(CheckFail, match="硬顶"):
        _checker().check(_factor(tmp_path))


def test_violations_exactly_at_tolerance_pass(tmp_path):
    """边界:违规日数恰等于容忍上限(10)放行 —— 拒的判据是严格 >。"""
    dates = _dates(30)
    for dt in dates[:10]:
        _write_day(tmp_path, dt, _clean(n_long=10, n_short=10))
    for dt in dates[10:]:
        _write_day(tmp_path, dt, _clean())
    res = _checker().check(_factor(tmp_path))
    assert res.total_checked == 30


def test_below_hard_ceiling_is_soft_only(tmp_path):
    """单日 9.9%:超软线(5%)但低于硬顶(10%)→ 只记 1 个违规日,容忍内放行。"""
    dates = _dates(30)
    for dt in dates[1:]:
        _write_day(tmp_path, dt, _clean())
    # 1 只 s + 49 只 1.0 多 + 50 只 1.0 空:s/(99+s)=0.099 → s≈10.88
    s = 0.099 * 99 / (1 - 0.099)
    w = np.array([s] + [1.0] * 49 + [-1.0] * 50)
    frac = w.max() / np.abs(w).sum()
    assert 0.05 < frac < 0.10                      # 软线上、硬顶下
    _write_day(tmp_path, dates[0], w)
    res = _checker().check(_factor(tmp_path))      # 1 违规日 <= 容忍 → 放行
    assert res.total_checked == 30


def test_invalid_days_skipped_not_counted(tmp_path):
    """全 NaN / 零敞口日是无效日:跳过,不计违规(缺数据的早期天天然免疫)。"""
    dates = _dates(40)
    # 前 20 天无效(缺数据),后 20 天合规 → 0 违规日,放行
    for dt in dates[:10]:
        _write_day(tmp_path, dt, np.full(120, np.nan))
    for dt in dates[10:20]:
        _write_day(tmp_path, dt, np.zeros(120))          # 零敞口
    for dt in dates[20:]:
        _write_day(tmp_path, dt, _clean())
    res = _checker().check(_factor(tmp_path))
    assert res.total_checked == 20              # 只有 20 个有效日进统计


def test_empty_dir_skips(tmp_path):
    with pytest.raises(CheckSkip):
        _checker().check(_factor(tmp_path))


def test_all_invalid_skips(tmp_path):
    for dt in _dates(5):
        _write_day(tmp_path, dt, np.zeros(120))
    with pytest.raises(CheckSkip, match="全空"):
        _checker().check(_factor(tmp_path))


def test_maxpos_soft_boundary_not_violation(tmp_path):
    """边界 = 阈值不算违规(严格 >):单股恰 5% 放行。"""
    # 20 只等权 → 每只 1/20 = 5.0% = 阈值,不违规;但 total 20 < 100 会违规,
    # 故补足到 100 只、其中一只权重设成恰好 5%。构造:99 只 base + 1 只 spike。
    dates = _dates(5)
    # total_abs = 99*b + s;要 s/total = 0.05 且 count=100(50 多 50 空)
    base = 1.0
    # 解 s = 0.05*(99*base + s) → s = 0.05*99*base/0.95 = 5.2105*base
    s = 0.05 * 99 * base / 0.95
    w = np.array([s] + [base] * 49 + [-base] * 50)   # 50 多(含 spike)50 空,total 100
    assert abs(w[w > 0].max() / np.abs(w).sum() - 0.05) < 1e-9
    for dt in dates:
        _write_day(tmp_path, dt, w)
    res = _checker().check(_factor(tmp_path))   # 恰 5% 不违规 + total=100 不违规
    assert res.total_checked == 5
