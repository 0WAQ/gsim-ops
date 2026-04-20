import numpy as np

class DataFirewall:
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
        ti = args[2] if len(args) > 2 else None

        originals = {}
        for attr_name, attr_val in instance.__dict__.items():
            if attr_val is None:
                continue
            if isinstance(attr_val, np.ndarray):
                originals[attr_name] = attr_val
                setattr(instance, attr_name, self._SafeProxy(attr_val, di, ti, attr_name))
            elif hasattr(attr_val, 'data') and isinstance(attr_val.data, np.ndarray):
                originals[attr_name] = attr_val
                setattr(instance, attr_name, self._SafeProxy(attr_val, di, ti, attr_name))

        try:
            return self.func(*args, **kwargs)
        finally:
            for attr_name, orig in originals.items():
                setattr(instance, attr_name, orig)


    class _SafeProxy:
        def __init__(self, obj, di, ti, attr_name):
            self._obj = obj
            self._di = di
            self._ti = ti
            self._attr = attr_name
            self._data = obj.data if (hasattr(obj, 'data') and isinstance(obj.data, np.ndarray)) else obj

        def _check_index(self, index, max_pos):
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

        def _truncate(self, arr):
            arr = np.asarray(arr)
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

            self._check_index(di, self._di)
            if self._ti is not None and ti is not None:
                self._check_index(ti, self._ti)

            truncated_data = self._truncate(self._data)
            if truncated_data.ndim == 0:
                return truncated_data
            return truncated_data[key]

        def __getattr__(self, name):
            original_attr = getattr(self._obj, name)
            if name == 'data':
                return self._truncate(original_attr)
            try:
                arr = np.asarray(original_attr)
                if arr.ndim == 0:
                    return original_attr
                return self._truncate(arr)
            except (TypeError, ValueError):
                return original_attr
