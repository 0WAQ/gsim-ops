"""infra 层类型化异常(full-review D3,factor-aggregate-plan 阶段 1)。

原则:变更操作要么返回 bool(delete → "存在且已删"),要么抛这里的类型化异常;
调用方 catch 具体类型,不再 catch Exception。此前同一动词三种契约
(delete → bool / None 混杂)、not-found 用裸 KeyError —— 调用方只能靠字符串
匹配或宽捕获区分。

本模块只定义**当前有 raise 方的**异常(不预留"未来可能用"的空壳 —— W3 幽灵
状态的教训);Repository 门面落地时(阶段 2)按需扩充。
"""


class FactorNotFound(KeyError):
    """按名字定位因子失败(state 无该行)。

    继承 KeyError:transition/append_check 历史上抛裸 KeyError,存量调用方
    `except KeyError` 继续有效;新代码请捕获本类型。
    """


class StateConflict(RuntimeError):
    """transition(expect=...) 的 CAS 失败:当前 status 与期望不符。

    TOCTOU 修复的一半 (full-review 第三部分 §3.2):resolve 与执行之间状态
    可能被并发操作改变,transition 提供 from-status 条件更新,调用方捕获后
    按'跳过'处理而不是盲改。
    """
