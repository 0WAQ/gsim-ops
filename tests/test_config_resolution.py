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
    """144 现场:tool install + 任意 cwd,解析落空 → 干净退出带修法。

    site-packages 里 get_project_root() 找不到 pyproject.toml 退化成 cwd ——
    repo 内跑测试无法自然复现,monkeypatch 模拟该退化。"""
    import ops.infra.config as cfg_mod
    monkeypatch.delenv("OPS_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cfg_mod, "get_project_root", lambda: tmp_path)
    missing = tmp_path / "config.yaml"
    with pytest.raises(SystemExit) as ei:
        Config.load(missing)
    msg = str(ei.value)
    assert str(missing) in msg
    assert "OPS_CONFIG" in msg          # 提示里必须有修法
    assert "全部落空" in msg            # 走的是"解析落空"分支,不是 -c 分支
    assert "FileNotFoundError" not in msg


def test_load_missing_file_names_bad_env(tmp_path, monkeypatch):
    """OPS_CONFIG typo:错误消息点名是环境变量指错了。"""
    missing = tmp_path / "typo.yaml"
    monkeypatch.setenv("OPS_CONFIG", str(missing))
    with pytest.raises(SystemExit) as ei:
        Config.load(Path(str(missing)))
    assert "OPS_CONFIG" in str(ei.value)
    assert "unset" in str(ei.value)


def test_load_missing_explicit_c_names_the_flag(tmp_path, monkeypatch):
    """显式 -c 传缺失路径:消息说"-c 拼写",不谎报"三级解析落空"(评审 M1)。

    含 OPS_CONFIG 指向有效文件的变体 —— 更不能暗示 env 落空。"""
    monkeypatch.delenv("OPS_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    explicit = tmp_path / "i-typed-this-wrong.yaml"
    with pytest.raises(SystemExit) as ei:
        Config.load(explicit)
    assert "-c/--config-path" in str(ei.value)
    assert "全部落空" not in str(ei.value)

    good = tmp_path / "good.yaml"
    good.write_text("vars: {}\n")
    monkeypatch.setenv("OPS_CONFIG", str(good))
    with pytest.raises(SystemExit) as ei:
        Config.load(explicit)
    assert "-c/--config-path" in str(ei.value)
    assert "全部落空" not in str(ei.value)


def test_project_root_fallback_third_level(monkeypatch, tmp_path):
    """第三级:env 未设、cwd 无 config → 项目根 config.yaml(repo 内跑必存在)。"""
    monkeypatch.delenv("OPS_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)                     # cwd 无 config.yaml
    p = get_default_config_path()
    assert p.name == "config.yaml" and p.is_file()  # 本仓库根的那份


def test_exit_carries_message_not_code(tmp_path, monkeypatch):
    """SystemExit 载荷是消息字符串(shell 退出码 1),不是裸 int。"""
    monkeypatch.delenv("OPS_CONFIG", raising=False)
    with pytest.raises(SystemExit) as ei:
        Config.load(tmp_path / "config.yaml")
    assert isinstance(ei.value.code, str)


def test_help_survives_deleted_cwd(monkeypatch, tmp_path):
    """cwd 被删除时 get_default_config_path 不抛(评审 M2:"永不抛"契约密闭)。"""
    monkeypatch.delenv("OPS_CONFIG", raising=False)
    doomed = tmp_path / "gone"
    doomed.mkdir()
    monkeypatch.chdir(doomed)
    doomed.rmdir()
    p = get_default_config_path()                   # 不得抛 OSError
    assert p == Path("config.yaml")


def test_produce_block_parsing():
    """produce 块(v3,factor-produce-v3.md §7):键齐全时全解析;块缺失不炸
    构造且属性 None/缺省 —— 消费方(归档生产化/produce 驱动)入口自行响亮报错。"""
    import yaml

    from ops.infra.config import get_project_root

    base = yaml.safe_load((get_project_root() / "config.yaml").read_text())
    raw, _, _ = Config._resolve_vars(dict(base), "server-170")
    c = Config(raw)
    assert str(c.produce_nio_data_path) == "/nvme125/datasvc/data/cc_all"
    assert c.produce_enddate == "TODAY"
    assert c.produce_startdate == "20110101"
    assert c.produce_backdays == 256
    assert str(c.produce_checkpoint_root) == "/nvme125/checkpoint"
    assert str(c.produce_dump_root) == "/nvme125/alpha_dump"
    assert str(c.produce_pnl_root) == "/nvme125/alpha_pnl"
    assert c.produce_datasvc_prefix == "/nvme125"
    assert str(c.produce_module_prefix) == "/mnt/storage/alphalib/alpha_src"

    base.pop("produce")
    raw2, _, _ = Config._resolve_vars(dict(base), "server-170")
    c2 = Config(raw2)
    assert c2.produce_nio_data_path is None
    assert c2.produce_checkpoint_root is None
    assert c2.produce_enddate == "TODAY"      # 无害缺省;路径类键无缺省
    assert c2.produce_backdays == 256
