class Result:
    """checker 结果的标记基类(`Checker.check() -> Result | None`)。

    具体结果只有两个:CompResult(compliance 持仓摘要)、CorrResult(correlation
    指标 + bcorr,流水线捕获后喂 `_persist_derived` 落 snapshot)。
    """
