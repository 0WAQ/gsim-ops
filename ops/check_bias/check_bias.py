import os
import re
import sys
import shutil
import multiprocessing as mp
from glob import glob
from datetime import datetime, timedelta
from .xml import do_xml
from ..common.utils import BacktestError, Local, Gsim, debug

DATA_FIREWALL_CODE = """\
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

"""


def run_check_bias(args):
    src = args.dropbox_path
    dst = args.target_path

    # TODO: remove dst/user
    if os.path.exists(dst):
        shutil.rmtree("/tmp/check_bias")

    if not os.path.exists(dst):
        os.makedirs(dst)

    user_src = os.path.join(src, args.unix_id)
    args.user_src = user_src

    # 拷贝到临时目录
    user_dst = os.path.join(dst, args.unix_id)
    args.user_dst = user_dst

    start_date = datetime.strptime(args.start_date, "%Y%m%d")
    end_date = datetime.strptime(args.end_date, "%Y%m%d") if args.end_date is not None else None

    dates = []
    if end_date is None:
        cur_date = args.start_date
        cur_path = os.path.join(user_src, cur_date)
        if not Local.check_is_dir(cur_path):
            print(f"WARN: {cur_path} doesn't exist")
            return  # TODO: return

        dates.append(cur_date)
    else:
        for t in range(int((end_date - start_date).days) + 1):
            cur_date = (start_date + timedelta(1) * t).strftime("%Y%m%d")
            cur_path = os.path.join(user_src, cur_date)
            if not Local.check_is_dir(cur_path):
                print(f"WARN: {cur_path} doesn't exist")
                continue

            dates.append(cur_date)

    args.dates = dates
    for date in dates:
        user_date_src = os.path.join(user_src, date)
        user_date_dst = os.path.join(user_dst, date)
        if not os.path.exists(user_date_src):
            print(f"{user_date_src} doesn't exist")
            continue

        if not os.path.exists(user_date_dst):
            shutil.copytree(user_date_src, user_date_dst)

    do_check_bias(args)


def inject_datafirewall(py_file):
    with open(py_file, "r", encoding="utf-8") as f:
        content = f.read(-1)
    new_content = DATA_FIREWALL_CODE + content

    dr_pattern = re.compile(r"\s*self\.(\w+)\s*=.*dr\.getData\(.*\).*", re.M)
    dr_attrs = dr_pattern.findall(content)
    new_content = new_content.replace("DEFAULT_DATA_ATTRS = []",
                        f"DEFAULT_DATA_ATTRS = [{','.join(t.__repr__() for t in dr_attrs)}]")
    new_content = new_content.replace("self.raw_data = raw_data_object.data", "self.raw_data = raw_data_object")
    generate_pattern = re.compile(r"(\s*)def generate\(self,\s*di\):", re.M)
    new_content = generate_pattern.sub(r"\1@DataFirewall\n\1def generate(self, di):", new_content)

    with open(py_file, 'w', encoding="utf-8") as f:
        f.write(new_content)
    return True


def do_check_bias(args):
    # TODO: to list?
    users = [args.unix_id]

    os.makedirs(f"/tmp/result/{args.unix_id}", exist_ok=True)

    # 遍历 users
    for user in users:
        user_path = os.path.join(args.target_path, user)
        if not os.path.exists(user_path):
            continue

        # 遍历 dates
        for date in args.dates:
            user_date_path = os.path.join(user_path, date)
            if not os.path.exists(user_date_path):
                continue

            f = open(f"/tmp/result/{args.unix_id}/{date}", "w+")

            # 遍历 Alpha
            for alpha in os.listdir(user_date_path):
                if not alpha.startswith("Alpha"):
                    continue

                alpha_path = os.path.join(user_date_path, alpha)

                # 修改 xml
                py_path, xml_path = do_xml(alpha_path)

                # 注入 DataFirewall
                inject_datafirewall(py_path)

                # 回测 cc       
                print("backtest from to cc")
                try:
                    Gsim.run_backtest(xml_path)
                except BacktestError as e:
                    print(e)
                    f.write(str(e))

            f.close()
