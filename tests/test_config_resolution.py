"""config 路径解析与加载失败语义(2026-07-17,144 uv tool install 崩溃修复)。

两条铁律:
1. OPS_CONFIG 一旦设置就是唯一候选 —— 不存在也不回落(typo 静默换 config
   操作因子库比崩溃可怕);
2. 缺配置文件 = 干净 SystemExit + 可行动提示,不是裸 FileNotFoundError
   双 traceback(uv tool install 从任意 cwd 跑,三级解析可能全落空)。
"""
from pathlib import Path

import pytest

from ops.infra.config import Config, get_default_config_path


def test_ops_config_env_wins_even_if_missing(monkeypatch, tmp_path):
    """OPS_CONFIG 指向不存在的路径:返回它(不静默回落),错误留给 load 报。"""
    missing = tmp_path / "no-such-config.yaml"
    monkeypatch.setenv("OPS_CONFIG", str(missing))
    # cwd 放一个真 config,验证它不会被偷偷选中
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("vars: {}\n")
    assert get_default_config_path() == missing


def test_ops_config_env_used_when_exists(monkeypatch, tmp_path):
    cfg = tmp_path / "my.yaml"
    cfg.write_text("vars: {}\n")
    monkeypatch.setenv("OPS_CONFIG", str(cfg))
    assert get_default_config_path() == cfg


def test_cwd_config_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("OPS_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("vars: {}\n")
    assert get_default_config_path() == tmp_path / "config.yaml"


def test_load_missing_file_exits_with_hint(tmp_path, monkeypatch):
    """144 现场:tool install + 任意 cwd,解析落空 → 干净退出带修法。"""
    monkeypatch.delenv("OPS_CONFIG", raising=False)
    missing = tmp_path / "config.yaml"
    with pytest.raises(SystemExit) as ei:
        Config.load(missing)
    msg = str(ei.value)
    assert str(missing) in msg
    assert "OPS_CONFIG" in msg          # 提示里必须有修法
    assert "FileNotFoundError" not in msg


def test_load_missing_file_names_bad_env(tmp_path, monkeypatch):
    """OPS_CONFIG typo:错误消息点名是环境变量指错了。"""
    missing = tmp_path / "typo.yaml"
    monkeypatch.setenv("OPS_CONFIG", str(missing))
    with pytest.raises(SystemExit) as ei:
        Config.load(Path(str(missing)))
    assert "OPS_CONFIG" in str(ei.value)
    assert "unset" in str(ei.value)
