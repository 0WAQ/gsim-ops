---
name: project-firewall-1d-instrument-vector
description: DataFirewall 前视检查按 getData tag 排除框架级静态数据 (ipodate 等 instrument 维静态向量)
metadata: 
  node_type: memory
  type: project
  originSessionId: 35dec44c-d031-42f8-9f6d-286d76b4dcba
---

checkbias 阶段的 `DataFirewall` 通过 **getData tag 白名单** 排除框架级静态数据,不做前视检查。

**机制**: `ops/services/check/checker/checkbias_checker.py` 里 `STATIC_TAGS = {'ipodate'}`。`_GetDataAttrCollector.visit_Assign` 收集 `self.xxx = dr.getData('tag')` 的 attr 时,若 tag 命中 `STATIC_TAGS` 就跳过 → 该 attr 不进 `data_attrs` → firewall 根本不 wrap 它。按 **tag** 而非 attr 名排除,所以 QR 命名成 `self.ipo` / `self.ipodate`、带不带 `.data` 都兜得住。

**为什么 ipodate 要排除**: `ipodate` 是 gsim 框架数据 (`dr.getData('ipodate')`, `gsim/data/module/ipo.py` 定义为 `NIO_VECTOR(NIO_INT)`, 长度=instrument 数如 5484, 每只股票上市日期)。它是 **1D 票维静态向量**,不随交易日变化、开盘前已知、不含未来信息。factor 常 `self.ipodate[:n]` 按**票维**切片 (n=股票数),但 firewall 的 `_check_index` 把 key 的 0 轴当**日期**查,`stop=5484 > max_di` 就误报 `looking forward!!!`。

**曾走过的弯路**: 最初想在 firewall `__getitem__` 里对 `ndim==1` 无条件跳过前视检查 —— 被否决,因为按维度一刀切语义含糊,且会漏掉真正的 1D 日期序列前视。正解是按数据来源 (tag) 精确排除,与 `valid` 白名单同思路。`valid` 走另一条路径 (`ALWAYS_GUARD`/`ALWAYS_ALLOW_DI`: 注入 firewall 但放行 `[di]`)。

**Why**: xmf 批次 `AlphaXmfRpt1270LowLiqD5Ind` 因 ipodate 误判 checkbias 前视被 REJECTED,实际无 bug。

**How to apply**: 以后遇到别的框架级静态票维数据被误报前视,加进 `STATIC_TAGS` 即可,不要动 firewall 本体。相关: [[reference-factor-validation-pipeline]] [[reference-gsim-data-modules]]。
