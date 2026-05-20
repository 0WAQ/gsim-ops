import ast
import xmltodict
from pathlib import Path
from .base import *
from ops.infra.config import Config
from ops.infra.gsim.runner import Runner, BacktestError
from ops.core.alpha.metadata import AlphaMetadata
from ops.core.alpha.results.checkbias import *

FIREWALL_FILE = Path(__file__).parent / "firewall.py"
ALWAYS_GUARD = {'valid'}
FIREWALL_PY_SUFFIX = "_firewall.py"


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
        """Inject DataFirewall into a sibling firewall .py (NOT the original),
        point XML at it for the backtest, then restore XML + delete the temp.

        The original factor .py is never mutated, so a killed/crashed run can't
        leave it in a half-injected state that double-decorates on the next run.
        """
        firewall_py = factor.py_file.with_name(factor.py_file.stem + FIREWALL_PY_SUFFIX)
        original_module = None

        try:
            # 1. Build injected source (firewall code + AST-decorated factor)
            source = factor.py_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
            collector = _GetDataAttrCollector()
            collector.visit(tree)
            data_attrs = collector.attrs | ALWAYS_GUARD

            firewall_code = FIREWALL_FILE.read_text(encoding="utf-8")
            tree = _GenerateDecoratorInjector(delay=factor.delay, data_attrs=data_attrs).visit(tree)
            ast.fix_missing_locations(tree)
            firewall_py.write_text(firewall_code + "\n" + ast.unparse(tree), encoding="utf-8")

            # 2. Point XML at the firewall .py; save original @module to restore later
            original_module = factor.xml_config["gsim"]["Modules"]["Alpha"].get("@module")
            factor.xml_config["gsim"]["Modules"]["Alpha"]["@module"] = str(firewall_py)
            factor.xml_file.write_text(
                xmltodict.unparse(factor.xml_config, pretty=True, encoding="utf-8", full_document=False),
                encoding="utf-8",
            )

            # 3. Short Backtest
            Runner.run_backtest(factor.xml_file, self.config)
        except BacktestError as e:
            raise CheckbiasFail(e)
        except Exception as e:
            raise CheckbiasSkip(e)
        finally:
            # Restore XML @module
            if original_module is not None:
                factor.xml_config["gsim"]["Modules"]["Alpha"]["@module"] = original_module
                try:
                    factor.xml_file.write_text(
                        xmltodict.unparse(factor.xml_config, pretty=True, encoding="utf-8", full_document=False),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            # Remove the firewall temp .py (safe even if it never got written)
            try:
                firewall_py.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
