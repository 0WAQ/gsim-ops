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
                # 保存
                originals[attr] = getattr(instance, attr)
                if originals[attr] is None:
                    continue

                # 截断
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
                if ti is None:
                    truncated_data = self._data.raw_data[:self._di]
                else:
                    truncated_data = self._data.raw_data[:self._di, :self._ti]
            else:
                if ti is None:
                    truncated_data = self._data[:self._di]
                else:
                    truncated_data = self._data[:self._di, :self._ti]
            return truncated_data[key]

        def __getattr__(self, name):
            if isinstance(self._data, AlphaData):
                raw = self._data.raw_data
                truncated_data = AlphaData(raw[:self._di])
            else:
                truncated_data = self._data[:self._di]
            return getattr(truncated_data, name)
