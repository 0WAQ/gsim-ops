import argparse
from .transfer import run_scp


def add_scp_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "scp",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage:
  1. remote -> local (download):
     ops scp wbai@10.6.100.146:/mnt/storage/dropbox/wbai/20251030 ../ -p 123456
  2. local -> remote (upload):
     ops scp ../ wbai@10.6.100.146:/mnt/storage/dropbox/wbai/20251030 --key-path ~/.ssh/my_key
""")

    parser.add_argument("source")
    parser.add_argument("dest")

    opt_group = parser.add_argument_group("options")
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

