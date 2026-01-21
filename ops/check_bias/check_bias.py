import os
import re
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from .xml import do_xml
from ..common.utils import BacktestError, Gsim

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
    src: Path = args.dropbox_path
    dst: Path = args.target_path

    # TODO: remove dst/user
    if dst.exists():
        shutil.rmtree("/mnt/storage/work/wbai/check_bias")

    if not dst.exists():
        os.makedirs(dst)

    user_src: Path = src / args.user
    args.user_src = user_src

    # 拷贝到临时目录
    user_dst: Path = dst / args.user
    args.user_dst = user_dst

    start_date = datetime.strptime(args.start_date, "%Y%m%d")
    end_date = datetime.strptime(args.end_date, "%Y%m%d") if args.end_date is not None else None

    dates = []
    if end_date is None:
        cur_date = args.start_date
        cur_path = user_src / cur_date
        if not cur_path.is_dir():
            print(f"WARN: {cur_path} doesn't exist")
            return  # TODO: return

        dates.append(cur_date)
    else:
        for t in range(int((end_date - start_date).days) + 1):
            cur_date = (start_date + timedelta(1) * t).strftime("%Y%m%d")
            cur_path: Path = user_src / cur_date
            if not cur_path.is_dir():
                print(f"WARN: {cur_path} doesn't exist")
                continue

            dates.append(cur_date)

    args.dates = dates
    for date in dates:
        user_date_src: Path = user_src / date
        user_date_dst: Path = user_dst / date
        if not user_date_src.exists():
            print(f"{user_date_src} doesn't exist")
            continue

        if not user_date_dst.exists():
            shutil.copytree(user_date_src, user_date_dst)

    do_check_bias(args)


def inject_datafirewall(py_file: Path):
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
    users: list[str] = [args.user]

    os.makedirs(f"/mnt/storage/work/wbai/check_bias/result/{args.user}", exist_ok=True)

    # 遍历 users
    for user in users:
        user_path: Path = args.target_path / user
        if not user_path.exists():
            continue

        # 遍历 dates
        for date in args.dates:
            user_date_path: Path = user_path / date
            if not user_date_path:
                continue

            f = open(f"/mnt/storage/work/wbai/check_bias/result/{args.unix_id}/{date}", "w+")

            # 遍历 Alpha
            for alpha_path in user_date_path.iterdir():
                if not alpha_path.name.startswith("Alpha"):
                    continue

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
