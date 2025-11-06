import os
import sys
import paramiko
from typing import Optional
from scp import SCPClient
from tqdm import tqdm
from ..common.ssh_utils import init_ssh_client
from ..common.utils import check_path_exists, ensure_dir_exists


def scp_local2remote(ssh: paramiko.SSHClient, local_path: str, remote_path: str) -> bool:
    try:
        # 确保远程目录存在
        ssh.exec_command(f"mkdir -p {os.path.dirname(remote_path)}")

        with SCPClient(ssh.get_transport(), socket_timeout=10) as scp:
            if os.path.isdir(local_path):
                # 遍历所有文件, 显示进度条
                file_list = []
                for root, dirs, files in os.walk(local_path):
                    for file in files:
                        file_list.append(os.path.join(root, file))

                print(f"\n=== 传输方向：本地 → 远程 ===")
                print(f"本地目录：{local_path}")
                print(f"远程目录：{remote_path}")
                for local_file in tqdm(file_list, desc="传输进度"):
                    remote_file = os.path.join(remote_path, os.path.relpath(local_file, local_path))
                    scp.put(local_file, remote_file)
                print(f"传输完成, 共 {len(file_list)} 个文件")
            else:
                print(f"\n=== 传输方向：本地 → 远程 ===")
                print(f"本地文件：{local_path}")
                print(f"远程文件：{remote_path}")
                scp.put(local_path, remote_path)
                print(f"传输完成")
        return True
    except Exception as e:
        print(f"传输失败: {str(e)}")
        return False


def scp_remote2local(ssh: paramiko.SSHClient, local_path: str, remote_path: str) -> bool:
    try:
        ensure_dir_exists(os.path.dirname(local_path))

        with SCPClient(ssh.get_transport(), socket_timeout=10) as scp:
            stdin, stdout, stderr = ssh.exec_command(f"file {remote_path} 2>/dev/null")
            is_remote_dir = (stdout.read().decode('utf-8').strip().split(' ')[-1] == "directory")

            if is_remote_dir:
                # 递归下载目录
                print(f"\n=== 传输方向：远程 → 本地 ===")
                print(f"远程目录：{remote_path}")
                print(f"本地目录：{local_path}")
                scp.get(remote_path, local_path, recursive=is_remote_dir)
                print("✅ 传输完成")
            else:
                # 下载单个文件
                print(f"\n=== 传输方向：远程 → 本地 ===")
                print(f"远程文件：{remote_path}")
                print(f"本地文件：{local_path}")
                scp.get(remote_path, local_path)
                print("✅ 传输完成")
        return True
    except Exception as e:
        print(f"传输失败: {str(e)}")
        return False


class SSHConfig:
    def __init__(self, args):
        self.host = ""
        self.port = 22
        self.username = ""
        self.password = args.password
        self.key_path = args.key_path


def parse_remote_path(path: str) -> tuple[Optional[dict], str]:
    if "@" not in path or ":" not in path:
        return None, path
    
    user_host, remote_path = path.split(":", 1)
    if "@" not in user_host or not remote_path:
        return None, path
    
    username, host = user_host.split("@", 1)
    if not user_host or not host:
        return None, path
    
    remote_path = remote_path.rstrip("/")
    return {
        "username": username,
        "host": host,
        "remote_path": remote_path
    }, ""


def run_scp(args):
    # 1. 解析源和目标路径
    source_remote, source_loacl = parse_remote_path(args.source)
    dest_remote, dest_local = parse_remote_path(args.dest)

    if source_remote and dest_remote:
        sys.exit("❌ 错误：源和目标不能同时为远程路径")
    if not source_remote and not dest_remote:
        sys.exit("❌ 错误：源和目标必须有一个是远程路径（格式：用户名@主机:路径）")

    # 2. 判断传输方向和关键参数
    direction = ""
    ssh_config = SSHConfig(args)
    local_path = ""
    remote_path = ""

    if source_remote:
        # 源是远程, 下载
        direction = "remote2local"
        ssh_config.host = source_remote['host']
        ssh_config.username = source_remote['username']
        remote_path = source_remote["remote_path"]
        local_path = dest_local
        print(f"🔍 识别传输方向：远程 → 本地")
        print(f"远程：{source_remote['username']}@{source_remote['host']}:{remote_path}")
        print(f"本地：{local_path}")
    elif dest_remote:
        # 目的是远程, 上传
        direction = "local2remote"
        ssh_config.host = dest_remote['host']
        ssh_config.username = dest_remote['username']
        remote_path = dest_remote['remote_path']
        local_path = source_loacl
        print(f"🔍 识别传输方向：本地 → 远程")
        print(f"本地：{local_path}")
        print(f"远程：{dest_remote['username']}@{dest_remote['host']}:{remote_path}")

    if direction == "local2remote":
        check_path_exists(local_path)

    # 3. 初始化 ssh 连接
    ssh = init_ssh_client(
        host=ssh_config.host,
        port=ssh_config.port,
        username=ssh_config.username,
        password=ssh_config.password,
        key_path=ssh_config.key_path
    )

    try:
        if direction == "local2remote":
            scp_local2remote(ssh, local_path, remote_path)
        else:
            scp_remote2local(ssh, local_path, remote_path)
    finally:
        ssh.close()
