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
    ssh = init_ssh_client(
        host="10.6.100.146",
        port=22,
        password="123456"
    )

    transport = ssh.get_transport()
    if transport is None:
        # TODO:
        return

    with SCPClient(transport, socket_timeout=10) as scp:

        dropbox_user_path = os.path.join(args.dropbox_directory, args.unix_id)
        args.dropbox_user_path = dropbox_user_path

        start_date = datetime.strptime(args.start_date, "%Y%m%d")
        end_date = datetime.strptime(args.end_date, "%Y%m%d") if args.end_date is not None else None

        if end_date is None:
            cur_path = os.path.join(dropbox_user_path, args.start_date)
            if not remote_path_exists(ssh, cur_path):
                print(f"WARN: {cur_path} doesn't exist.")
                # TODO: ?
                return
            scp.get(remote_path=cur_path, local_path=dropbox_user_path, recursive=True)
            args.dropbox_user_date_path = os.path.join(dropbox_user_path, args.start_date)
            run_compiler(args)
        else:
            for t in range(int((end_date - start_date).days) + 1):
                cur_date = (start_date + timedelta(1) * t).strftime("%Y%m%d")
                cur_path = os.path.join(dropbox_user_path, cur_date)
                if not remote_path_exists(ssh, cur_path):
                    print(f"WARN: {cur_path} doesn't exist.")
                    continue
                scp.get(remote_path=cur_path, local_path=dropbox_user_path, recursive=True)
                args.dropbox_user_date_path = os.path.join(dropbox_user_path, cur_date)
                run_compiler(args)

        # TODO: ?
        sys.exit(1)
