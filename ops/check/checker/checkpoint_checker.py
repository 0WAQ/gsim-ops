#!/usr/bin/env python3
"""
因子断点检测脚本 (支持多配置文件)
功能：
1. 获取每个因子最后一天 alpha 值的 MD5
2. 批量运行多个配置文件的断点恢复
3. 重新计算 MD5 并对比
4. 输出检测结果
"""
from ...common.utils import md5sum
from ...common.config import Config
from ...common.runner import Runner
from ...common.alpha.metadata import AlphaMetadata
from ...common.alpha.results.checkpoint import *


class CheckpointChecker:
    def __init__(self, config: Config):
        self.config = config

    def _get_v1md5(self, factor: AlphaMetadata) -> str | None:
        file = factor.get_last_v1npy_file()
        md5 = None
        if file:
            md5 = md5sum(file)
        return md5

    def _get_v2md5(self, factor: AlphaMetadata) -> str | None:
        file = factor.get_last_v2npy_file()
        md5 = None
        if file:
            md5 = md5sum(file)
        return md5

    def check_one(self, factor: AlphaMetadata) -> tuple[bool, str]:
        old = self._get_v2md5(factor)

        status, msg = Runner.run_backtest(factor.xml_file, self.config)

        new = self._get_v2md5(factor)
        if not old or not new:
            # print(f"⚠️  {factor.name}: Missing")
            # print(f"    原始 MD5: {old}")
            # print(f"    新的 MD5: {new}")
            return False, "Missing"
        
        if old != new:
            status = False
            # print(f"❌ {factor.name}: 失败")
            # print(f"    原始 MD5: {old}")
            # print(f"    新的 MD5: {new}")
        else:
            # print(f"✅ {factor.name}: 通过")
            # print(f"    MD5: {old}")
            ...

        return status, msg
