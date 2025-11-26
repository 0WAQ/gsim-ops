import os
import sys
import paramiko


class Remote:
    @staticmethod
    # TODO: rename
    def check_is_dir(ssh: paramiko.SSHClient, path: str):
        _, stdout, _ = ssh.exec_command(f"file {path} 2>/dev/null")
        is_dir = (stdout.read().decode('utf-8').strip().split(' ')[-1] == "directory")
        return is_dir
    
    @staticmethod
    def check_path_exists(ssh: paramiko.SSHClient, path: str):
        pass

    @staticmethod
    def ensure_dir_exists(ssh: paramiko.SSHClient, path: str):
        pass


class Local:    
    @staticmethod
    def check_path_exists(path: str):
        if not os.path.exists(os.path.abspath(path)):
            sys.exit(f"路径不存在: {path}")

    @staticmethod
    def ensure_dir_exists(path: str) -> None:
        os.makedirs(path, exist_ok=True)

