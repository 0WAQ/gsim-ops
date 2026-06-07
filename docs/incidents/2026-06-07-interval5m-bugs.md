# Interval5m 数据缺陷报告 (代码 + build 双问题)

**日期**: 2026-06-07
**报告人**: wbai
**严重级别**: MEDIUM
**状态**: 待修, 本地不动手
**关联代码**: `/usr/local/gsim/source_ref/interval_5m_zx.py` (118 行)

---

## 1. 摘要

Interval5m 派生字段 `{pctchange, ret, vwap}` 在三个 cc root 上有两个独立问题:

1. **cc_all 上这三个字段全是 0.0** (~10 亿 cells), 不是 NaN 是真的 0 —— **build 漏了**, 文件只有 zero-initialized memmap, 从未被填实值。wbai 已确认要重 build cc_all 来修。
2. **cc_2024 / cc_2025 上数据基本可用** (99.9999% finite 值合理), 但**源码有 3 个除零保护缺失 bug**, 在尾部小比例触发 `+inf` 和负 `vwap`。

总数据量: 3657 × 49 × 5484 ≈ 9.83 亿 cells/字段 (≤2024 范围)。

---

## 2. 问题 #1: cc_all 上 derived 字段全 0 (build 漏)

### 证据

`/datasvc/data/cc_all/Interval5m/Interval5m.{pctchange,ret,vwap}.npy` 三个文件:

```
shape       = (3997, 49, 5484) float64
size        = 8.59 GB / 个
all_nan     = 0 天 (≤2024 范围)
no_nan_days = 3657 天 (≤2024 范围 — 全部!)
min = max = mean = 0
n_valid_cells = 982,694,412 (全部都是 finite 0.0)
```

不是数据被 NaN 标记成无效, 是文件**真的填的全是 0.0**。看起来是 NIO_CUBE 预分配 + zero-init 之后, build 流程跑 `loadDay` 时没有触达这些字段或被 short-circuit 了。

`Interval5m.{open, close, high, low, vol, amo}` (原始字段) 在 cc_all 上**应该是有数据的** (未直接验证, 但比对 cc_2024/cc_2025 时 cc_all 跟它们差异巨大说明 cc_all 上至少没有 derived 字段的同步)。

### 影响

任何在 cc_all 上跑回测/因子的代码, 用 `dr.getData('Interval5m.{pctchange|ret|vwap}')` **不会报错, 拿到全 0 矩阵, 因子算出来全错**, 没有任何告警。

### 怀疑根因

- 可能是 `build_cc` 的 config 把 Interval5m 这几个 derived 字段砍了, 留下空壳
- 或者 build 跑到某一步 short-circuit / silent fail, 没填 derived
- 或者源码改过 `loadDay` 后某次跑没回填历史
- **wbai 已确认要重 build 来修**, 根因待具体确认

### 同步状态对照

- cc_2024 / cc_2025 (new): 有数据, **互相 byte-identical**, 三字段都正常 (除尾部 bug)
- cc_all: 三字段全 0

cc_2024 跟 cc_2025 byte-identical 说明它们是同一源 build (或者直接 copy)。cc_all 跟它们差异 100%, 是 cc_all 独立 build 时漏了。

---

## 3. 问题 #2: `interval_5m_zx.py` 代码缺陷 — 3 处除零 / 异常未保护

源码: `/usr/local/gsim/source_ref/interval_5m_zx.py` (118 行)

### Bug 2.1 — `ret` 不防 `open == 0` (line 103)

```python
self.ret[di, ti, ii] = self.close[di, ti, ii] / self.open[di, ti, ii] - 1.0
```

`open == 0` 时 `close / 0 = inf`。NaN 输入自动传播为 NaN, 不会触发 (np.nan + 1.0 = nan), 但**零开盘价**直接产生 inf。

**实测影响 (cc_2024 ≤20241231)**:
```
+inf:     200,968 次 (0.0205% of total cells, 0.045% of finite)
-inf:     0 次
```

**修法**:
```python
op = self.open[di, ti, ii]
self.ret[di, ti, ii] = (self.close[di, ti, ii] / op - 1.0) if (op != 0) else np.nan
```

或者用 numpy where 模式, 视风格选。

### Bug 2.2 — `pctchange` 不防 `close[ti-1] == 0` (line 104)

```python
self.pctchange[di, ti, ii] = self.close[di, ti, ii] / self.close[di, ti-1, ii] - 1.0 if ti != 0 else np.nan
```

`close[di, ti-1, ii] == 0` 时 inf。这个量比 ret bug 量级小很多 (前 bar 收 0 比当前 bar 开 0 罕见), 但同性质。

`ti==0` 已经 hardcode NaN, OK。

**实测影响**:
```
+inf:     132 次 (0.00001% of cells)
```

**修法**:
```python
prev_close = self.close[di, ti-1, ii] if ti != 0 else 0
self.pctchange[di, ti, ii] = (self.close[di, ti, ii] / prev_close - 1.0) if (ti != 0 and prev_close != 0) else np.nan
```

### Bug 2.3 — `vwap` 防了 `vol` 没防 `amo < 0` (line 106-110)

```python
vol = self.vol[di, ti, ii]
if vol == 0 or np.isnan(vol):
    self.vwap[di, ti, ii] = np.nan
else:
    self.vwap[di, ti, ii] = self.amo[di, ti, ii] / self.vol[di, ti, ii]
```

只防了 `vol == 0` 和 `vol == NaN`, **没防 `amo < 0`**。citics 5min 源 CSV 偶尔给负 `turnover` (脏数据), 直接除出来 `vwap` 是负的。

**实测影响**:
```
finite < 0:  249 次 (0.00003% of cells)
最小值:      -8133
```

**修法 (二选一)**:

A. **本地 sanitize**: 加 `amo < 0` 检查, 输出 NaN, 警告上游
```python
amo = self.amo[di, ti, ii]
vol = self.vol[di, ti, ii]
if vol == 0 or np.isnan(vol) or amo < 0 or np.isnan(amo):
    self.vwap[di, ti, ii] = np.nan
else:
    self.vwap[di, ti, ii] = amo / vol
```

B. **保留原值, 加上游告警**: vwap 异常累计计数 → 跑完该日记 log, 让数据上游 (中信) 解决根因, 不在 build 阶段吃错

建议 A (build 端 sanitize), 因为下游消费者已经默认 vwap > 0, 不应该自己再防。

### Bug 2.X — 风险点 (不一定是 bug)

- **line 76 `code = raw_code[0:6]`**: 硬截 6 字符。A 股 / 北交所 OK, 但港股 (4-5 位) / ETF 会截错。当前 universe 是纯 A 股可忽略, 加新市场要注意
- **line 81 + 114 count 检测**: 同一股一天 >49 bar 只 warning 不拒绝, 如果发生后写覆盖前写, 静默错。低概率但应改 hard fail

---

## 4. 量化数据有效性 (≤20241231 范围, cc_2024 上)

总 cells = 982,425,696 (trim 最后一天 enddate 占位)

| 字段 | NaN | finite (有效) | inf | 异常占 finite | "合理" 占 finite |
|---|---|---|---|---|---|
| `ret` | 5.39 亿 (54.9%) | 4.43 亿 (45.1%) | 20.1 万 +inf | 0.045% | `\|x\|<0.5` → 100.0% |
| `pctchange` | 5.48 亿 (55.8%) | 4.34 亿 (44.2%) | 132 +inf | <0.001% | `\|x\|<0.5` → 100.0% |
| `vwap` | 5.41 亿 (55.0%) | 4.42 亿 (45.0%) | 0 inf, 249 负值 | 0.0001% | `[0.01, 1e5]` → 99.9999% |

**总结**:
- **大部分数据 (45%) 是有效的**, NaN 55% 主要是早期年份没 5min 数据 (~1226 天全 NaN) + 停牌
- **异常值 (inf + 负 vwap) 比例 1e-3% ~ 1e-4%**, 量级小到不影响整体分布
- **有限值的"业务合理性"约 100%** —— 只要拿到的不是 inf/负数, 数据就能用

**结论**: cc_2024 / cc_2025 上 Interval5m 三字段**有效部分基本可用**, 异常是已知尾部, 修代码后 rebuild 会消除。

---

## 5. 修复 + Rollout 建议

### 短期 (修代码 + 重 build)

1. 修 `/usr/local/gsim/source_ref/interval_5m_zx.py` 三处 bug (2.1, 2.2, 2.3)
2. 重 build cc_all 的 Interval5m 字段 (顺便修 #1 的 build 漏)
3. 可选: 重 build cc_2024 / cc_2025 来清掉历史 inf / 负 vwap (~25 万 cells, 不重 build 也能用)

### 中期 (防回归)

1. `loadDay` 末尾加自检: 扫一遍 `np.any(np.isinf(self.ret[di]))` 等, 异常时记录到 log
2. data-writer 框架加通用"build 后异常值统计" hook, 任何 Dmgr 跑完该日打印 (n_nan, n_inf, n_finite) 摘要
3. ops 这边: `ops check` 流水线某个阶段抽样 check 因子读到的数据是否 inf-free, 跨机不一致 → 告警

### 长期

- 三地数据脑裂检测 (跨机 fingerprint 定期跑) — 跟之前 [`2026-06-06-cc-data-drift-160-vs-147.md`] 报告里 Q5 同一件事

---

## 6. 复现

```bash
# 数据质量量化
python3 /tmp/quality_interval5m.py

# 跨 root 一致性
python3 /tmp/check_interval5m.py

# 看代码
cat /usr/local/gsim/source_ref/interval_5m_zx.py  | head -120

# 查 cc_all 上零文件
python3 -c "
import numpy as np
arr = np.memmap('/datasvc/data/cc_all/Interval5m/Interval5m.ret.npy',
                dtype='float64', mode='r', shape=(3997, 49, 5484))
print(arr[:3657].min(), arr[:3657].max(), arr[:3657].mean())
# 0.0 0.0 0.0
"
```

---

## 7. 关联

- [`2026-06-06-gsim-code-drift-three-sites.md`](2026-06-06-gsim-code-drift-three-sites.md) — gsim 代码三地漂移 (CRITICAL)
- [`2026-06-06-cc-data-drift-160-vs-147.md`](2026-06-06-cc-data-drift-160-vs-147.md) — cc 数据 160 vs 147 漂移 (MEDIUM)
- 本报告 = 上面那批审计的延伸, 暴露了 Interval5m 这个特定 module 的代码缺陷 + cc_all 上的 build 缺失
