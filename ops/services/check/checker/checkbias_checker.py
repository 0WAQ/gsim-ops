import ast
from pathlib import Path
from .base import *
from ops.infra.config import Config
from ops.infra.gsim.runner import Runner, BacktestError
from ops.core.alpha.metadata import AlphaMetadata
from ops.core.alpha.results.checkbias import *

FIREWALL_FILE = Path(__file__).parent / "firewall.py"


class _GenerateDecoratorInjector(ast.NodeTransformer):
    def __init__(self, delay: int):
        self.delay = delay

    def visit_FunctionDef(self, node):
        if node.name == 'generate' and node.args.args and node.args.args[0].arg == 'self':
            decorator = ast.Call(
                func=ast.Name(id='DataFirewall', ctx=ast.Load()),
                args=[],
                keywords=[ast.keyword(arg='delay', value=ast.Constant(value=self.delay))]
            )
            node.decorator_list.insert(0, decorator)
        return node


class CheckbiasSkip(CheckSkip):
    def __init__(self, *args: object):
        super().__init__("checkbias", *args)

class CheckbiasFail(CheckFail):
    def __init__(self, *args: object):
        super().__init__("checkbias", *args)


class CheckbiasChecker(Checker):
    def __init__(self, config: Config):
        self.config = config

    def check(self, factor: AlphaMetadata):
        orignal_content = None

        try:
            # 1. Inject DataFirewall via AST
            with open(factor.py_file, "r", encoding="utf-8") as f:
                orignal_content = f.read()

            firewall_code = FIREWALL_FILE.read_text(encoding="utf-8")
            tree = ast.parse(orignal_content)
            tree = _GenerateDecoratorInjector(delay=factor.delay).visit(tree)
            ast.fix_missing_locations(tree)
            new_content = firewall_code + "\n" + ast.unparse(tree)

            with open(factor.py_file, 'w', encoding="utf-8") as f:
                f.write(new_content)

            # 2. Short Backtest
            Runner.run_backtest(factor.xml_file, self.config)
        except BacktestError as e:
            raise CheckbiasFail(e)
        except Exception as e:
            raise CheckbiasSkip(e)

        finally:
            if orignal_content is not None:
                with open(factor.py_file, 'w', encoding='utf-8') as f:
                    f.write(orignal_content)
