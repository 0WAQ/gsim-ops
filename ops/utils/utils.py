"""CLI 共享的 argparse 小件。

2026-07-07 清理:原本这里还有 Remote(paramiko stub)/ Local / Gsim(硬编码
/usr/local/gsim 路径的旧 runner,与 infra/gsim/runner.Runner 整段重复)三个
cp/scp 时代的死类 —— 全部零 importer,且让 9 个 CLI 模块为一个 3 行的
argparse Action 每次启动付 paramiko import 税(full-review 第三部分 G5/V)。
现仅保留唯一被使用的 LowerAction;回测能力唯一的家是 ops/infra/gsim/runner.py。
"""
import argparse


class LowerAction(argparse.Action):
    def __call__(self, parser, namespace, values: str, option_string=None):
        setattr(namespace, self.dest, values.lower())
