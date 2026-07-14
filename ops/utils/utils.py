"""CLI 共享的 argparse 小件(现仅 LowerAction)。

回测能力唯一的家是 ops/infra/gsim/runner.py —— 别把 runner 逻辑塞回这个 utils 模块。
"""
import argparse


class LowerAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if isinstance(values, str):
            values = values.lower()
        setattr(namespace, self.dest, values)
