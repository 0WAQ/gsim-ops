import os
import sys
import paramiko
import argparse
import subprocess as sp
from pathlib import Path
from typing import Optional

from .exception.exception import BacktestError


class LowerAction(argparse.Action):
    def __call__(self, parser, namespace, values: str, option_string=None):
        setattr(namespace, self.dest, values.lower())


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
    def check_is_dir(path: str):
        return os.path.isdir(path)

    @staticmethod
    def check_path_exists(path: str):
        if not os.path.exists(os.path.abspath(path)):
            sys.exit(f"路径不存在: {path}")

    @staticmethod
    def ensure_dir_exists(path: str) -> None:
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)


class Gsim:
    @staticmethod
    def run_backtest(xml_path: Path):
        try:
            python = "/usr/local/gsim/.venv/bin/python"
            run_py = "/usr/local/gsim/run.py"
            sp.run([python, run_py, xml_path],
                   stdin=sp.PIPE, stdout=sp.PIPE, stderr=sp.PIPE,
                   text=True, check=True, timeout=3600)
            print("✅ backtest succeed")
        except sp.CalledProcessError as e:
            raise BacktestError(f"❌ looking forward!!! ({xml_path}) {e.stderr}") from e
        except sp.TimeoutExpired as e:
            ...


    @staticmethod
    def run_simsummary(pnl_path: str) -> Optional[str]:
        try:
            python = "/usr/local/gsim/.venv/bin/python"
            simsummary_py = "/usr/local/gsim/tools/simsummary.py"
            sim_path = pnl_path + ".sim"
            with open(sim_path, 'w+') as f:
                sp.run([python, simsummary_py, pnl_path], stdout=f, text=True)
            print("✅ simsummary succeed")
            return sim_path
        except Exception as e:
            print(f"❌ simsummary failed: {e}")
            return None

    
    @staticmethod
    def run_diff(lhs: str, rhs: str, out: str) -> Optional[str]:
        try:
            output_path = os.path.join(os.path.dirname(lhs), "diff.txt")
            with open(output_path, 'w+') as f:
                _ = sp.run(["diff", lhs, rhs], stdout=f, text=True)
                print(f"running diff: {output_path}")
                size = f.seek(0, 2)
                if size != 0:
                    with open(out, 'w+') as f1:
                        f1.write(os.path.dirname(lhs))
                        f1.writelines(f.readlines())
                    print(f"❌ {os.path.dirname(output_path)} has forward looking!") 
                else:
                    print(f"✅ {os.path.dirname(output_path)} doesn't have forward looking!")
            return output_path
        except sp.CalledProcessError as e:
            print(f"run diff failed: {e}")