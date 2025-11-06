import argparse

from .transfer import run_scp


def add_scp_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "scp",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例：
  1. 远程→本地 (下载目录):
     ops scp wbai@10.6.100.146:/mnt/storage/dropbox/wbai/20251030 ../ -p 123456
  2. 本地→远程 (上传目录):
     ops scp ../ wbai@10.6.100.146:/mnt/storage/dropbox/wbai/20251030 --key-path ~/.ssh/my_key
  3. 传输单个文件：
     ops scp wbai@10.6.100.146:/tmp/test.so ./ -P 2222
     
远程路径格式: 用户名@主机IP:远程路径 (如 wbai@10.6.100.146:/mnt/xxx)
注意: 源和目标路径中仅允许一个远程路径 (自动判断传输方向)
        """
    )

    parser.add_argument("source")
    parser.add_argument("dest")

    opt_group = parser.add_argument_group("可选配置参数")
    opt_group.add_argument("-p", "--password")
    opt_group.add_argument("--port", default=22, type=int)
    opt_group.add_argument("--key-path", default="~/.ssh/id_rsa")

    parser.set_defaults(func=run_scp)


def add_scp_subparser1(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "scp",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    required_group = parser.add_argument_group("核心必填参数")
    required_group.add_argument("--host", required=True)
    required_group.add_argument("-u", "--username", required=True)
    required_group.add_argument("-l", "--local-path", required=True)
    required_group.add_argument("-r", "--remote-path", required=True)

    opt_group = parser.add_argument_group("可选配置参数")
    opt_group.add_argument("-p", "--password")
    opt_group.add_argument("--port", default=22, type=int)
    opt_group.add_argument("--direction", default="local2remote", choices=["local2remote", "remote2local"])    # TODO:
    opt_group.add_argument("--key-path", default="~/.ssh/id_rsa")

    parser.set_defaults(func=run_scp)

