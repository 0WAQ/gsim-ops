"""check 工作区 alpha_dump 目录的扫描函数(compliance / checkpoint 两个
checker 共用)。

不放 core/alpha/metadata.py:AlphaMetadata 是领域类型,不该带盘面 I/O
(与 Factor 聚合同一条分层原则);扫的又是 check 期工作区(gsim dump 输出),
消费方只有本包 —— 落地在此。

gsim dump 盘面布局:<alpha_dir>/<YYYY>/<MM>/<yyyymmdd>.*v{1,2}.npy。
(同一布局的另两份走查在 core/library.py 的 _count_dump_days /
_get_dump_date_range —— 那边扫的是入库后的 alpha_dump(doctor 对账域),此处不动。)

不裸 `except Exception` 吞错:目录不存在返回空/None 是正常语义
(glob 天然如此),真正的 OSError 应该冒泡进流水线的 unexpected 臂
(revert SUBMITTED + 完整日志),而不是静默当"没有文件"。
"""
from pathlib import Path


def _digit_dirs(parent: Path, pattern: str, *, reverse: bool = False) -> list[Path]:
    return sorted((d for d in parent.glob(pattern) if d.is_dir()), reverse=reverse)


def v2npy_files(alpha_dir: Path) -> list[Path]:
    """全部 v2 dump 文件,按时序(年/月/文件名)升序。目录不存在 → []。"""
    return [f
            for y in _digit_dirs(alpha_dir, "[0-9][0-9][0-9][0-9]")
            for m in _digit_dirs(y, "[0-9][0-9]")
            for f in sorted(m.glob("*v2.npy"))]


def last_v2npy_file(alpha_dir: Path) -> Path | None:
    """最新月份目录里时序最末的 v2 dump 文件;该月没有 v2 → None。

    **有意不回退更早月份**:checkpoint 用本函数比对断点续跑前后"本次运行刚
    写出"的同一份 dump,回退会把陈旧残留卷进比较 —— None → CheckSkip
    (revert SUBMITTED 重试)是正确的保守路由。也因此只 glob 最新年/最新月,
    不物化整棵树。"""
    years = _digit_dirs(alpha_dir, "[0-9][0-9][0-9][0-9]", reverse=True)
    if not years:
        return None
    months = _digit_dirs(years[0], "[0-9][0-9]", reverse=True)
    if not months:
        return None
    files = sorted(months[0].glob("*v2.npy"), reverse=True)
    return files[0] if files else None
