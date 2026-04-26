import ast
from pathlib import Path
from .base import *
from ops.infra.config import Config
from ops.infra.gsim.runner import Runner, BacktestError
from ops.core.alpha.metadata import AlphaMetadata
from ops.core.alpha.results.checkbias import *

FIREWALL_FILE = Path(__file__).parent / "firewall.py"
ALWAYS_GUARD = {'valid'}


class _GenerateDecoratorInjector(ast.NodeTransformer):
    def __init__(self, delay: int, data_attrs: set[str]):
        self.delay = delay
        self.data_attrs = data_attrs

    def visit_FunctionDef(self, node):
        if node.name == 'generate' and node.args.args and node.args.args[0].arg == 'self':
            decorator = ast.Call(
                func=ast.Name(id='DataFirewall', ctx=ast.Load()),
                args=[],
                keywords=[
                    ast.keyword(arg='delay', value=ast.Constant(value=self.delay)),
                    ast.keyword(arg='data_attrs', value=ast.Set(
                        elts=[ast.Constant(value=a) for a in sorted(self.data_attrs)]
                    )),
                ]
            )
            node.decorator_list.insert(0, decorator)
        return node


class _GetDataAttrCollector(ast.NodeVisitor):
    """Collect attribute names assigned from dr.getData() in __init__."""

    def __init__(self):
        self.attrs: set[str] = set()

    def visit_FunctionDef(self, node):
        if node.name == '__init__':
            self.generic_visit(node)

    def visit_Assign(self, node):
        if len(node.targets) != 1:
            return
        target = node.targets[0]
        if self._is_self_attr(target) and self._is_getdata_call(node.value):
            self.attrs.add(target.attr) # type: ignore

    def _is_self_attr(self, node):
        return (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == 'self')

    def _is_getdata_call(self, node):
        # dr.getData(...) or *.getData(...)
        if isinstance(node, ast.Call):
            return self._is_getdata_func(node.func)
        # dr.getData(...).data
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Call):
            return self._is_getdata_func(node.value.func)
        return False

    def _is_getdata_func(self, node):
        return (isinstance(node, ast.Attribute)
                and node.attr == 'getData')


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

            tree = ast.parse(orignal_content)

            # Collect getData attribute names
            collector = _GetDataAttrCollector()
            collector.visit(tree)
            data_attrs = collector.attrs | ALWAYS_GUARD

            firewall_code = FIREWALL_FILE.read_text(encoding="utf-8")
            tree = _GenerateDecoratorInjector(delay=factor.delay, data_attrs=data_attrs).visit(tree)
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
