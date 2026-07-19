"""Runner.run_backtest 的 log_path 语义:产线 gsim 输出全量落盘不吞。

log_path=None(缺省)保持 capture_output 旧行为(check 链路不变);
产线传 log_path 时 stdout+stderr 合并落盘,失败取日志尾部做 BacktestError。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ops.infra.gsim.runner import BacktestError, Runner


def _config(script: Path) -> SimpleNamespace:
    return SimpleNamespace(python_path=str(sys.executable),
                           run_script=str(script), timeout=30)


def test_log_path_captures_stdout_and_stderr(tmp_path):
    script = tmp_path / "fake_gsim.py"
    script.write_text(
        "import sys\nprint('out-1')\nprint('err-1', file=sys.stderr)\n")
    log = tmp_path / "logs" / "run.log"

    Runner.run_backtest(Path("whatever.xml"), _config(script), log_path=log)

    text = log.read_text()
    assert "out-1" in text and "err-1" in text


def test_log_path_failure_raises_with_tail(tmp_path):
    script = tmp_path / "fake_gsim.py"
    script.write_text(
        "import sys\nprint('noise\\n' * 100, file=sys.stderr)\n"
        "print('real-traceback-line', file=sys.stderr)\nsys.exit(3)\n")
    log = tmp_path / "run.log"

    with pytest.raises(BacktestError) as ei:
        Runner.run_backtest(Path("x.xml"), _config(script), log_path=log)

    assert "real-traceback-line" in str(ei.value)     # 取尾不取头
    assert "noise" in log.read_text()                  # 全量仍在盘上
