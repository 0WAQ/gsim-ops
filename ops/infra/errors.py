"""infra 层类型化异常。

原则:变更操作要么返回 bool(delete → "存在且已删"),要么抛这里的类型化异常;
调用方 catch 具体类型,不再 catch Exception。

本模块只定义**当前有 raise 方的**异常(不预留"未来可能用"的空壳),按需扩充。
"""


class FactorNotFound(KeyError):
    """按名字定位因子失败(state 无该行)。

    继承 KeyError:transition/append_check 历史上抛裸 KeyError,存量调用方
    `except KeyError` 继续有效;新代码请捕获本类型。
    """


class StateConflict(RuntimeError):
    """transition(expect=...) 的 CAS 失败:当前 status 与期望不符。

    TOCTOU 修复的一半:resolve 与执行之间状态可能被并发操作改变,transition
    提供 from-status 条件更新,调用方捕获后按'跳过'处理而不是盲改。
    """
