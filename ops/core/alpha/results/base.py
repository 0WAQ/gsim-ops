class Result:
    """checker 结果的标记基类(`Checker.check() -> Result | None`)。

    具体结果只有两个:CompResult(compliance 持仓摘要)、CorrResult(correlation
    指标 + bcorr,流水线捕获后喂 `_persist_derived` 落 snapshot)。2026-07-11
    退化器官清理:原同居的 Status 空 Enum / Results 空集合类及三份 *Status/
    *Results 子类(full-review 病灶 V)全部删除 —— 定义至今无一处消费。
    """
