import re
from .base import *
from ops.common.config import Config
from ops.common.runner import Runner, BacktestError
from ops.common.alpha.metadata import AlphaMetadata
from ops.common.alpha.results.checkbias import *

DATA_FIREWALL_CODE = """\
import numpy as np

class AlphaData:
    pass

class DataFirewall:
    DEFAULT_DATA_ATTRS = []
    DEFAULT_DATA_ATTRS = list(set(DEFAULT_DATA_ATTRS))

    def __init__(self, func):
        self.func = func

    def __get__(self, instance, owner):
        if instance is None:
            return self
        def wrapper(*args, **kwargs):
            return self.__decorator__(instance, *args, **kwargs)
        return wrapper

    def __decorator__(self, *args, **kwargs):
        instance = args[0]
        di = args[1]
        ti = None
        ii = args[-1]
        if len(args) > 2:
            ti = args[1]

        attrs_to_protect = self.DEFAULT_DATA_ATTRS
        
        originals = {}
        for attr in attrs_to_protect:
            if hasattr(instance, attr):
                originals[attr] = getattr(instance, attr)
                if originals[attr] is None:
                    continue
                setattr(instance, attr, self._SafeProxy(originals[attr], di, ti, attr))

        try:
            return self.func(*args, **kwargs)
        finally:
            for attr, orig in originals.items():
                setattr(instance, attr, orig)


    class _SafeProxy:
        def __init__(self, data, di, ti, attr):
            self._data = data
            self._di = di
            self._ti = ti
            self._attr = attr

        def check(self, index, max_pos):
            if isinstance(index, slice):
                start, stop = index.start, index.stop
                if start is None:
                    start = 0
                elif start < 0:
                    start = max(0, max_pos + start)
                if stop is None:
                    stop = max_pos
                elif stop < 0:
                    stop = max(0, max_pos + stop)
                if start >= max_pos:
                    raise IndexError(f"{self._attr} looking forward!!!")
                if stop > max_pos:
                    raise IndexError(f"{self._attr} looking forward!!!")
            elif isinstance(index, int):
                if index >= max_pos or index < 0:
                    raise IndexError(f"{self._attr} looking forward!!!")

        def _truncate(self, raw):
            arr = np.asarray(raw)
            if arr.ndim == 0:
                return arr
            elif arr.ndim == 1:
                return arr[:self._di]
            else:
                if self._ti is None:
                    return arr[:self._di]
                else:
                    return arr[:self._di, :self._ti]

        def __getitem__(self, key):
            di = ti = None
            if isinstance(key, tuple):
                di = key[0]
                if len(key) > 2:
                    ti = key[1]
            else:
                di = key

            self.check(di, self._di)
            if self._ti is not None:
                self.check(ti, self._ti)
            
            if isinstance(self._data, AlphaData):
                truncated_data = self._truncate(self._data.raw_data)
            else:
                truncated_data = self._truncate(self._data)
            
            if truncated_data.ndim == 0:
                return truncated_data
            return truncated_data[key]

        def __getattr__(self, name):
            # 先获取原始属性
            original_attr = getattr(self._data, name)
            
            # 尝试转换并截断
            try:
                arr = np.asarray(original_attr)
                if arr.ndim == 0:
                    return original_attr
                elif arr.ndim == 1:
                    return arr[:self._di]
                else:
                    if self._ti is None:
                        return arr[:self._di]
                    else:
                        return arr[:self._di, :self._ti]
            except (TypeError, ValueError):
                # 无法转换为数组，直接返回原始属性
                return original_attr

"""

class CheckbiasSkip(CheckSkip):
    def __init__(self, *args: object):
        super().__init__("checkbias", *args)

class CheckbiasFail(CheckFail):
    def __init__(self, *args: object):
        super().__init__("checkbias", *args)


class CheckbiasChecker(Checker):
    def __init__(self, config: Config):
        self.config = config

    def check(self, factor: AlphaMetadata):
        orignal_content = None

        try:
            # 1. Inject DatafireWall
            with open(factor.py_file, "r", encoding="utf-8") as f:
                orignal_content = f.read()
            new_content = DATA_FIREWALL_CODE + orignal_content

            dr_pattern = re.compile(r"\s*self\.(\w+)\s*=.*dr\.getData\(.*\).*", re.M)
            dr_attrs = dr_pattern.findall(orignal_content)
            new_content = new_content.replace("DEFAULT_DATA_ATTRS = []",
                                f"DEFAULT_DATA_ATTRS = [{','.join(t.__repr__() for t in dr_attrs)}]")
            new_content = new_content.replace("self.raw_data = raw_data_object.data", "self.raw_data = raw_data_object")
            generate_pattern = re.compile(r"(\s*)def generate\(self,\s*di\):", re.M)
            new_content = generate_pattern.sub(r"\1@DataFirewall\n\1def generate(self, di):", new_content)

            with open(factor.py_file, 'w', encoding="utf-8") as f:
                f.write(new_content)

            # 2. Short Backtest
            Runner.run_backtest(factor.xml_file, self.config)
        except BacktestError as e:
            raise CheckbiasFail(e)
        except Exception as e:
            raise CheckbiasSkip(e)

        finally:
            if orignal_content is not None:
                with open(factor.py_file, 'w', encoding='utf-8') as f:
                    f.write(orignal_content)
