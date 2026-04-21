import numpy as np

DELAY0_MAX_TI = 44  # exclusive: ti <= 43 (14:30) allowed for delay=0
ALWAYS_ALLOW_DI = {'valid'}  # attributes known before market open (defined in AlphaBase)


class DataFirewall:
    def __init__(self, delay=1):
        self.delay = delay

    def __call__(self, func):
        self.func = func
        return self

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
            is_ndarray = isinstance(attr_val, np.ndarray)
            is_nio = hasattr(attr_val, 'data') and isinstance(attr_val.data, np.ndarray)
            if is_ndarray or is_nio:
                originals[attr_name] = attr_val
                setattr(instance, attr_name,
                        self._SafeProxy(attr_val, di, ti, attr_name, self.delay))

        try:
            return self.func(*args, **kwargs)
        finally:
            for attr_name, orig in originals.items():
                setattr(instance, attr_name, orig)


    class _SafeProxy:
        def __init__(self, obj, di, ti, attr_name, delay):
            self._obj = obj
            self._di = di
            self._ti = ti
            self._attr = attr_name
            self._delay = delay
            self._data = obj.data if (hasattr(obj, 'data') and isinstance(obj.data, np.ndarray)) else obj
            self._ndim = self._data.ndim

            # valid: always allow di (defined in AlphaBase, known before market open)
            # delay=0 + 3D data: allow accessing di, but enforce ti <= 43
            # otherwise: di is future, cannot access
            if attr_name in ALWAYS_ALLOW_DI:
                self._max_di = di + 1
            elif delay == 0 and self._ndim >= 3:
                self._max_di = di + 1  # exclusive: can access di
            else:
                self._max_di = di      # exclusive: cannot access di

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
                if index < 0:
                    return
                if index >= max_pos:
                    raise IndexError(f"{self._attr} looking forward!!!")

        def _check_ti_for_delay0(self, di_key, ti_key):
            if self._delay != 0 or self._ndim < 3:
                return
            # only enforce ti constraint when accessing the current day (di)
            di_val = di_key
            if isinstance(di_key, slice):
                return  # slice access, hard to check precisely
            if di_val != self._di:
                return
            if ti_key is None:
                raise IndexError(f"{self._attr} delay0 accessing di={self._di} without ti constraint!!!")
            if isinstance(ti_key, int):
                if ti_key >= DELAY0_MAX_TI:
                    raise IndexError(f"{self._attr} delay0 ti={ti_key} >= {DELAY0_MAX_TI}, looking forward!!!")
            elif isinstance(ti_key, slice):
                stop = ti_key.stop
                if stop is not None and stop > DELAY0_MAX_TI:
                    raise IndexError(f"{self._attr} delay0 ti stop={stop} > {DELAY0_MAX_TI}, looking forward!!!")

        def _truncate(self, arr):
            arr = np.asarray(arr)
            if arr.ndim == 0:
                return arr
            elif arr.ndim == 1:
                return arr[:self._max_di]
            else:
                return arr[:self._max_di]

        def __getitem__(self, key):
            di_key = ti_key = None
            if isinstance(key, tuple):
                di_key = key[0]
                if len(key) > 1:
                    ti_key = key[1]
            else:
                di_key = key

            self._check_index(di_key, self._max_di)
            self._check_ti_for_delay0(di_key, ti_key)

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
