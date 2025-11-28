import os
import sys
import shutil
from datetime import datetime, timedelta

from .xml import do_xml
from ..common.utils import Local, Gsim


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


def do_check_bias(args):
    # TODO: to list?
    users = [args.unix_id]

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

            # 遍历 Alpha
            for alpha in os.listdir(user_date_path):
                alpha_path = os.path.join(user_date_path, alpha)

                # 修改 xml
                pnl_path, pnl_cc0_path, xml_path, xml_cc0_path = do_xml(alpha_path)

                # 回测 cc       
                print("backtest from to cc")
                Gsim.run_backtest(xml_path)
                cc = Gsim.run_simsummary(pnl_path)

                # 回测 cc0
                print("backtest cc0")
                Gsim.run_backtest(xml_cc0_path)
                cc0 = Gsim.run_simsummary(pnl_cc0_path)

                if cc is None or cc0 is None:
                    print(f"{cc} or {cc0} is None")
                    sys.exit(0)

                # diff
                output = Gsim.run_diff(cc, cc0, "/tmp/result")
                if output is None:
                    sys.exit(0)
