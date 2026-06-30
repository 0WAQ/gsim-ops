"""combo config 占位符注入。

QR 交完整 config.xml, 环境字段写成占位符; ops 用真实路径/日期替换后跑 gsim。
策略段 (优化器/后处理/权重) qr 自管 ops 不碰; 环境段 ops 注入 (数据隔离)。

占位符 (前缀 + 相对路径, 消除硬编码):
    ${RUN_DIR}        predict 产物目录 (npy 路径前缀; 仅模型型有)
    ${DATA_ROOT}      cc 数据根 (现成因子路径前缀)
    ${START} ${END}   回测区间 yyyymmdd
    ${PNL_DIR}        pnl 输出目录
    ${CHECKPOINT_DIR} gsim checkpoint 目录 (仅 opt 用)
"""
import re
from pathlib import Path

PLACEHOLDER_RE = re.compile(r"\$\{[A-Z_]+\}")


def inject(
    template_xml: str,
    *,
    run_dir: Path,
    data_root: Path,
    start: str,
    end: str,
    pnl_dir: Path,
    checkpoint_dir: Path,
) -> str:
    """把 config 模板里的占位符替换成真实值。

    替换后断言无残留 ${...}; 有则说明占位符拼写错或本函数漏了某个键, 直接报错
    (而非让 gsim 拿着 ${X} 路径跑出莫名其妙的错)。
    """
    mapping = {
        "${RUN_DIR}": str(run_dir),
        "${DATA_ROOT}": str(data_root),
        "${START}": str(start),
        "${END}": str(end),
        "${PNL_DIR}": str(pnl_dir),
        "${CHECKPOINT_DIR}": str(checkpoint_dir),
    }
    out = template_xml
    for k, v in mapping.items():
        out = out.replace(k, v)

    leftover = PLACEHOLDER_RE.findall(out)
    if leftover:
        raise ValueError(f"config 注入后残留未知占位符: {sorted(set(leftover))}")
    return out
