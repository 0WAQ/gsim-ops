import os
import sys
import paramiko
from typing import Optional, Union
from scp import SCPClient, SCPException
from tqdm import tqdm
from datetime import datetime, timedelta
from ..common.ssh_utils import init_ssh_client
from ..compiler.factor import run_compiler


def remote_path_exists(ssh: paramiko.SSHClient, path: str):
    _, stdout, _ = ssh.exec_command(f"file {path} 2>/dev/null")
    is_remote_dir = (stdout.read().decode('utf-8').strip().split(' ')[-1] == "directory")
    return is_remote_dir


def run_cp(args):
    unix_id = args.unix_id.lower()  # TODO:
    s = datetime.strptime(args.start_date, "%Y%m%d")
    e = datetime.strptime(args.end_date, "%Y%m%d")

    path = f"/mnt/storage/dropbox/{unix_id}"
    
    args.venv_path = "/home/wbai/.venvs/gsim/"
    args.compile_opt = "-O2"
    args.xml_backup = False
    args.enable_backtest = True

    ssh = init_ssh_client(
        host="10.6.100.146",
        port=22,
        password="123456"
    )

    delta = timedelta(days=1)
    with SCPClient(ssh.get_transport(), socket_timeout=10) as scp:
        if args.end_date is None:
            cur_date = args.start_date
            cur_path = os.path.join(path, cur_date)
            if not remote_path_exists(ssh, cur_path):
                print(f"WARN: {cur_path} doesn't exist.")
                return # TODO:
            args.date_dir = cur_date
            scp.get(remote_path=cur_path, local_path=path, recursive=True)
            run_compiler(args)
        else:
            for t in range(int((e - s).days) + 1):
                cur_date = (s + delta * t).strftime("%Y%m%d")
                cur_path = os.path.join(path, cur_date)
                args.date_dir = cur_date
                # print(cur_path)
                if not remote_path_exists(ssh, cur_path):
                    print(f"WARN: {cur_path} doesn't exist.")
                    continue
                # continue
                scp.get(remote_path=cur_path, local_path=path, recursive=True)
                run_compiler(args)
        sys.exit(1)
        # TODO: