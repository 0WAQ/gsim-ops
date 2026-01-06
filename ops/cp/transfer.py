import os
from scp import SCPClient
from datetime import datetime, timedelta
from ..common.utils import Local, Remote
from ..common.ssh import init_ssh_client
from ..compiler.factor import run_compiler


def run_cp(args):
    ssh = init_ssh_client(host="10.6.100.146", port=22, password="123456")

    transport = ssh.get_transport()
    if transport is None:
        return  # TODO: return
    
    dropbox_user_path = os.path.join(args.dropbox_directory, args.unix_id)
    args.dropbox_user_path = dropbox_user_path

    # TODO: 检查本地和远程用户目录是否存在
    Local.ensure_dir_exists(dropbox_user_path)
    Remote.ensure_dir_exists(ssh, dropbox_user_path) # TODO: to be implemented

    start_date = datetime.strptime(args.start_date, "%Y%m%d")
    end_date = datetime.strptime(args.end_date, "%Y%m%d") if args.end_date is not None else None

    with SCPClient(transport, socket_timeout=10) as scp:
        if end_date is None:
            cur_date = args.start_date
            cur_path = os.path.join(dropbox_user_path, cur_date)
            if not Remote.check_is_dir(ssh, cur_path):
                print(f"WARN: {cur_path} doesn't exist.")
                return  # TODO: return

            # print(f"remote_path: {cur_path}, local_path: {dropbox_user_path}")
            scp.get(remote_path=cur_path, local_path=dropbox_user_path, recursive=True)
            args.dropbox_user_date_path = os.path.join(dropbox_user_path, cur_date)
            run_compiler(args)
        else:
            for t in range(int((end_date - start_date).days) + 1):
                cur_date = (start_date + timedelta(1) * t).strftime("%Y%m%d")
                cur_path = os.path.join(dropbox_user_path, cur_date)
                if not Remote.check_is_dir(ssh, cur_path):
                    print(f"WARN: {cur_path} doesn't exist.")
                    continue
                scp.get(remote_path=cur_path, local_path=dropbox_user_path, recursive=True)
                args.dropbox_user_date_path = os.path.join(dropbox_user_path, cur_date)
                run_compiler(args)

        # TODO:
        # run_compiler(args)
