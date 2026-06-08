# 本地 cc 数据审计报告

**日期**: 2026-06-08 (v2 工具升级后重跑)
**报告人**: wbai (claude assist)
**范围**: 4 个本地 cc root, ≤ 20241231 切片
**工具**: `scripts/data-audit/cc_validate.py` (v2: 含 1D / 3D 支持) + `cc_fingerprint_diff.py`

## 0. TL;DR

- 三个新 build root (`cc_all`, 新 `cc_2024`, 新 `cc_2025`) 数据**几乎完全等价**, 都是当前 source_ref/dm_src 代码 build 的产物
- 老 `cc_2024` (`/datasvc/data/cc_2024`) 严重不完整, 缺 140 个派生 feature + 95 个 AMF 空壳, **该淘汰**
- 4 root 共同 critical 问题 (代码 / 源数据层面, 不是 build 漏):
  - **13 个 all_zero** (build 漏, 含 cc_all 上 **Interval5m 的 3 个 derived 字段** — v2 才发现)
  - **10 个 inf** (Dipv/Dpv 派生 module 除零 bug)
  - **3 个 HK_HOLDVOL_CHG_*20 freshness 失守** (20240816 起停更)
  - **37 个金融字段 stale** (last_data 2013-2022, 推测 wind 源停推)
- 174 个 `neg_in_nonneg` 是 heuristic 误报, 跳过

> **v2 升级说明**: 工具之前 skip 了 3D (`Interval5m`, `ashareconsensusrollingdata_*`) 和 1D (`aindexeodprices/*`), 共 90 个文件是审计盲区。v2 加了 1D/3D 支持后这些都覆盖了, 新发现的就是 cc_all 上 Interval5m 的 3 个 all_zero 字段。

---

## 1. 4 Root 总览 (v2)

| Root | 路径 | scan | ok | warn | crit | stale |
|---|---|---|---|---|---|---|
| **cc_all** | `/datasvc/data/cc_all` | 2197 | 1926 | 51 | 220 | 40 |
| **cc_2024_old** | `/datasvc/data/cc_2024` | 2056 | 1703 | 146 | 207 | 40 |
| **cc_2024_new** | `/tank/vault/datasvc/data/cc_2024` | 2196 | 1925 | 54 | 217 | 40 |
| **cc_2025_new** | `/tank/vault/datasvc/data/cc_2025` | 2196 | 1925 | 54 | 217 | 40 |

观察:
- cc_all critical=220 = 3 个 Interval5m all_zero + 10 个 dpv inf + 174 误报 + 3 个 stale + 30 个其他 (包含财务 stale 40 个里的 critical 化)
- cc_all 比新 cc_2024 多 3 critical (Interval5m derived), 跟之前手动发现完全对应
- 老 cc_2024 缺整组 7 个 Dpv 派生 module + 95 个 AMF 空壳, 已确认

---

## 2. 跨 root 一致性矩阵 (sum_diff + nan_diff)

| A vs B | common | match | sum_diff | nan_diff | only_A | only_B |
|---|---|---|---|---|---|---|
| cc_all ↔ cc_2024_old | 1965 | 1808 | 21 | 136 | 142 | 0 |
| cc_all ↔ cc_2024_new | 2105 | 2027 | 21 | 57 | **2** | 0 |
| cc_all ↔ cc_2025_new | 2105 | 2027 | 21 | 57 | **2** | 0 |
| cc_2024_old ↔ cc_2024_new | 1965 | 1872 | 0 | 93 | 0 | **140** |
| cc_2024_old ↔ cc_2025_new | 1965 | 1872 | 0 | 93 | 0 | **140** |
| cc_2024_new ↔ cc_2025_new | 2105 | 2091 | 0 | 14 | 0 | 0 |

要点:
- cc_all 跟新 cc_2024 / cc_2025 几乎完全一致 (差异都是 cc_all 缺 pwang 487 天 + 2 个废弃文件)
- 新 cc_2024 跟新 cc_2025 之间只差 14 个 Dpva/Dpvb 文件 (NaN 不一致, sum 等价 — 异步 build 微差)
- 老 cc_2024 缺 140 个文件 (Dipv/Dipva/Dpv/Dpva/Dpvb/Dpvc/Dpvd 整组), 是早期不完整快照

完整数据见 `data/diff_*.json`。

---

## 3. 真问题 (4 root 共同, 修代码 / 源 / config 才能解决)

### 3.1 ⚠ Freshness 失守 — `HK_HOLDVOL_CHG_*20` 三组 (真问题)

```
equ_fancy_factors_table4.HK_HOLDVOL_CHG_ALL20: last=20240816, gap=89d
equ_fancy_factors_table4.HK_HOLDVOL_CHG_B20:   last=20240816, gap=89d  
equ_fancy_factors_table4.HK_HOLDVOL_CHG_C20:   last=20240816, gap=89d
```

同 `equ_fancy_factors_table4/` 下其他 17 个字段末日都是 20241230 (cohort median)。这 3 个港股 20 日窗口字段在 2024-08-16 后突然停更。

**待查**:
- datayes 源 CSV 里 2024-08-17 后是否还有这 3 列 (源停推?)
- `/production/build_cc/config.xml` 里 equ_fancy_factors_table4 module 配置有没有改动
- Dmgr_equ_fancy_factors_table4 module 代码对 *_20 窗口的处理

### 3.2 ⚠ 13 个 all_zero — build 漏 (含 Interval5m 3 个, v2 才发现)

**Interval5m 3 个 (cc_all only, 新 build 上没漏)**:
```
Interval5m/Interval5m.pctchange.npy   ← (T, 49, N), 全 0.0 (新 build 有数据)
Interval5m/Interval5m.ret.npy
Interval5m/Interval5m.vwap.npy
```

**财务相关 10 个 (4 root 都漏)**:
```
asharecashflow/asharecashflow.spe_bal_netcash_inc_undir.npy
ashareincome/ashareincome.capitalized_comstock_div.npy
ashareincome/ashareincome.comshare_dvd_payable.npy
ashareincome/ashareincome.prfshare_dvd_payable.npy
ashareincome/ashareincome.withdr_buzexpwelfare.npy
ashareincome/ashareincome.withdr_othersurpreserve.npy
ashareincome/ashareincome.withdr_reservefund.npy
cash_flow_statement_fore_annual/cash_flow_statement_fore_annual.CF15.npy
cash_flow_statement_fore_annual/cash_flow_statement_fore_annual.CF19.npy
income_statement_fore_annual/income_statement_fore_annual.IS29.npy
```

这些字段在文件里但**全是 0.0 (不是 NaN)**, 说明 NIO_MATRIX 预分配后从未被 `loadData` 触达填值。

**待查**:
- Interval5m 3 个: `interval_5m_zx.py` 当前 build_cc config 是否启用 (跟之前 incident 一致)
- 10 个财务字段: 对应 Dmgr 源码看为啥 loadData 没填

### 3.3 ⚠ 10 个 inf — Dipv / Dpv 除零 bug

```
Dpv.dpv16:     339,880 次 +inf  (max=2.8e4)
Dpv.dpv17:     339,880 次 +inf  (max=8.2e4)
Dipv.dipv1:    199,422 次 +inf  (max=0.44)
Dipv.dipv5:    199,422 次 +inf
Dipv.dipv9:    199,422 次 +inf
Dipv.dipv19:   219,328 次 +inf
Dipva.dipva1:  199,423 次 +inf
+ Dipv.dipv6/7/10  各 1-2 次
```

跟 `interval_5m_zx.py` 同性质 (`docs/incidents/2026-06-07-interval5m-bugs.md`)。

**待修代码**: `dm_src/dmgr_dipv.py`, `dm_src/dmgr_dipva.py`, `dm_src/dmgr_dpv.py` 等, 加除零保护。

### 3.4 · 37 个金融字段 stale (大部分应是源停推)

```
asharebalancesheet (9):  最早 20130425, 最晚 20240429, 中位 gap ~ 800d
asharecashflow (11):     最早 20150421, 最晚 20241030, 中位 gap ~ 700d
ashareincome (17):       最早 20120426, 最晚 20240822, 中位 gap ~ 1900d
```

具体清单见 §A.1。

**推测**:
- 多数是 wind 财务报告字段在准则改革后废弃 (例 `withdr_legalsurplus` 是旧法定盈余公积, 新会计准则后基本停用)
- 个别 gap 较小的 (例 `incl_seat_fees_exchange` 164d, `tot_opt_inc_dif` 85d, `less_beg_bal_cash_equ` 43d) 可能是源近期才停, 值得单独看

**不一定是 bug**, 但建议跟反馈给者 / 因子设计者确认这些字段是否还在用。

---

## 4. 设计行为 / 误报 (不展开)

### 4.1 174 个 `neg_in_nonneg` — heuristic 误报

字段名匹配上 `value/volume/trades/...` 但语义实际允许负 (财务 / 派生 / log volatility / 比率)。具体白名单见 `cc-data-auditor.md` §3。**不是 bug**。

### 4.2 45 个 `all_nan` (warn) — 多数是源数据真没

`ashareconsensusrollingdata_*` 的 `est_dps` (dividend per share 预测), `est_pb`, `est_cfps` 等 — datayes 一致预期里没这些字段是常态。`Dipva.dipva3` 类似。

具体清单见 §A.2。**多数不是 bug**, 但反馈给者用这些字段会拿到全 NaN, 应该意识到。

### 4.3 cc_2024 上 95 个 AShareMoneyFlow 全 NaN (warn 计入 137 个里的 95 个)

跟之前已知一致, 老 cc_2024 build 时 AMF 整个 module 没跑。**新 cc_2024 已修**。

---

## 5. 跨 root 仅 cc_all 独有的 2 个文件

```
Dmgr_MktRet/Dmgr_MktRet.mkt_avg_ret.npy   - 市场平均收益, 待确认新 build 是否有意省略
signal_rsh/signal_rsh.value.npy            - 已弃用外部研究员信号
```

**建议**: 确认 `Dmgr_MktRet` 是否还要 → 不要就从 cc_all 删掉, 要就加进 build_cc config 让新 build 也产出。`signal_rsh` 直接删。

---

## 6. 推荐下一步 (按优先级)

| P | 行动 | 谁 | 依赖 |
|---|---|---|---|
| P0 | 修 `dm_src/dmgr_dipv.py` 等 10 个除零 bug + `interval_5m_zx.py` (一起修, 之前 incident 已记) | wbai | 自主 |
| P0 | 查 `HK_HOLDVOL_CHG_*20` 为啥从 20240816 停更 (源 vs config vs module) | wbai | 自主, 看 datayes 源 |
| P1 | 重 build cc_all 含新代码 | wbai | 等 P0 修完 |
| P1 | 调查 10 个 all_zero 字段 (源没字段? Dmgr 写错?) | wbai | 自主, 看源 CSV header |
| P2 | 确认 37 个 stale 金融字段是否还在用; 不用就从 build_cc config 删, 用就报 wind 找回 | wbai + 因子设计者 | 跨人 |
| P2 | 决定 `Dmgr_MktRet` 是否保留, 清理 `signal_rsh` | wbai | 自主 |
| P3 | 三地拓扑代码漂移 (147 vs 160) — 等领导决策 | 上级 | 阻塞 |

---

## 附录

### A.1 全部 40 stale 字段详情

#### asharebalancesheet (9)

| 字段 | last_data | gap |
|---|---|---|
| acc_exp | 20220105 | 723d |
| consumptive_bio_assets | 20190828 | 1294d |
| deferred_exp | 20220426 | 651d |
| deferred_inc | 20210422 | 895d |
| deposit_received | 20130425 | 2840d |
| incl_pledge_loan | 20141027 | 2477d |
| incl_seat_fees_exchange | 20240429 | 164d |
| subr_rec | 20210429 | 890d |
| unconfirmed_invest_loss | 20170510 | 1858d |

#### asharecashflow (11)

| 字段 | last_data | gap |
|---|---|---|
| conv_debt_into_cap | 20200814 | 1061d |
| decr_deferred_exp | 20220721 | 593d |
| fa_fnc_leases | 20210428 | 891d |
| incr_acc_exp | 20220929 | 544d |
| less_beg_bal_cash_equ | 20241030 | 43d |
| net_incr_disp_faas | 20190523 | 1362d |
| other_impair_loss_assets | 20241030 | 43d |
| spe_bal_netcash_inc | 20200918 | 1036d |
| spe_bal_netcash_inc_undir | 20150423 | 2357d |
| tot_bal_netcash_inc_undir | 20220829 | 566d |
| unconfirmed_invest_loss | 20150421 | 2359d |

#### ashareincome (17)

| 字段 | last_data | gap |
|---|---|---|
| adjlossgain_prevyear | 20151028 | 2232d |
| capitalized_comstock_div | 20130829 | 2756d |
| comshare_dvd_payable | 20130829 | 2756d |
| distributable_profit | 20210827 | 808d |
| distributable_profit_shrhder | 20210827 | 808d |
| insurance_expense | 20160329 | 2129d |
| prfshare_dvd_payable | 20130829 | 2756d |
| spe_bal_net_profit | 20120426 | 3082d |
| spe_bal_oper_profit | 20201028 | 1014d |
| spe_bal_tot_profit | 20201221 | 976d |
| tot_opt_inc_dif | 20240822 | 85d |
| unconfirmed_invest_loss | 20161031 | 1986d |
| undistributed_profit | 20210827 | 808d |
| withdr_buzexpwelfare | 20130829 | 2756d |
| withdr_legalsurplus | 20130829 | 2756d |
| withdr_othersurpreserve | 20130829 | 2756d |
| withdr_reservefund | 20130829 | 2756d |

#### equ_fancy_factors_table4 (3) — **真问题**

| 字段 | last_data | gap |
|---|---|---|
| HK_HOLDVOL_CHG_ALL20 | 20240816 | 89d |
| HK_HOLDVOL_CHG_B20 | 20240816 | 89d |
| HK_HOLDVOL_CHG_C20 | 20240816 | 89d |

### A.2 45 个 warn (all_nan) 字段按目录

- AShareMoneyFlow (2): tot_volume_ask, tot_volume_bid (源 CSV 字段为空字符串, wind 没给)
- Dipva (1): dipva3
- asharebalancesheet (3): agency_bus_assets, agency_bus_liab, spe_bal_liab_eqy_diff
- asharecashflow (3): conv_corp_bonds_due_within_1y, free_cash_flow, others
- ashareconsensusrollingdata_CAGR (7): est_bps, est_cfps, est_dps, est_pb, est_pe, est_peg, est_roe
- ashareconsensusrollingdata_FTTM (4): est_bps, est_dps, est_pb, est_roe
- ashareconsensusrollingdata_FY0 (1): est_dps
- ashareconsensusrollingdata_YOY (6): 同 CAGR
- ashareconsensusrollingdata_YOY2 (6): 同
- ashareincome (9): ebit, ebitda, net_after_ded_nr_lp_correct, etc.
- income_statement_fore_quarter (3): IS40_q, IS41_q, IS42_q

### A.3 原始 JSON 数据

- `data/validate_cc_all.json` — cc_all 完整验证报告
- `data/validate_cc_2024_old.json` — 旧 cc_2024
- `data/validate_cc_2024_new.json` — 新 cc_2024 (`/tank/vault`)
- `data/validate_cc_2025_new.json` — 新 cc_2025 (`/tank/vault`)
- `data/diff_*.json` × 6 — 6 对跨 root 比对

### A.4 复现

```bash
cd /home/wbai/gsim-ops/scripts/data-audit
# 单 root 验证
python3 cc_validate.py --root <ROOT> --out /tmp/v.json

# 跨 root 比对 (需要先 fingerprint)
python3 cc_fingerprint.py --root <ROOT> --out /tmp/fp.npz
python3 cc_fingerprint_diff.py /tmp/fp_a.npz /tmp/fp_b.npz --out /tmp/d.json
```

或用 skill: `/audit-cc <root>` / `/compare-cc <a> <b>` / `/verify-data-claim <文本>`。
