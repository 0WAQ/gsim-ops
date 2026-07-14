---
name: reference-cc-all-data-layout
description: /datasvc/data/{cc, cc_2024, cc_2025, cc_all} 特征仓库布局、形状、对齐规则,因子挖掘前必读
metadata:
  node_type: memory
  type: reference
  originSessionId: 4a9f6f46-e2eb-4f7f-a5ba-0c151976b968
---

# /datasvc/data/ 数据布局

## 数据分层(公司内部约定)

```
rawdata → cc (common cache) → dm (data manager) → alpha-feature
```

- **rawdata** `/datasvc/rawdata/{rawdata_wind, rawdata_datayes, rawdata_citics, rawdata_datayes_unused}` — 外部数据商落盘
- **cc** `/datasvc/data/{cc, cc_2024, cc_2025, cc_all}` — rawdata 的标准化 cache(memmap 格式)
- **dm** cc_all 下 `Dmgr*` 前缀目录 — gsim DataManager 模块基于 cc 的衍生
- **alpha-feature** cc_all 下 `cn_equity_feature*` / `realtime` / `delta` — 我们基于 L2 行情 build 的多频率特征

cc_all 是混合层,既存 rawdata 镜像,也吸纳 dm + alpha-feature 产出。

## rawdata 源对应

- `rawdata_wind/`(30 目录):行情、财务三大表、分析师一致预期 rolling、资金流、指数权重、ST、行业分类
- `rawdata_datayes/`(37 目录):**424 因子** equ_factor_*、**精品因子** equ_fancy_factors_*、**AI 盈利预测** *_fore_*、行业景气
- `rawdata_citics/`(1 目录):Interval5m(5min 行情)
- `rawdata_datayes_unused/`(36 目录,**已停更**):h2l 因子 + hf_daily_* 高频衍生表 + con_sec_* + dy1*_cne6_sw21

## 内部命名对照

| 内部叫法 | cc_all 目录 | 增量 |
|---|---|---|
| 424 因子 | equ_factor_*(15 表 433 字段) | ✅ |
| 精品因子 | equ_fancy_factors_table1~10(208) | ✅ |
| AI 盈利预测 | *_fore_annual / *_fore_quarter(9 表 213) | ✅ |
| 分析师预测 | ashareconsensusrollingdata_*(8 口径 136) | ✅ |
| h2l 高频转低频因子 | equ_h2l_factor_t1~t4(122) | ❌ **datayes_unused** |
| 高频衍生表 | hf_daily_der_table_1~9 + hf_daily_auction_table(247) | ❌ **datayes_unused** |
| L2 feature | cn_equity_feature/*(6315 日级) + cn_equity_feature_5min/*(917 5min) | ✅ 自研,各频率 |
| 行业 + 精品因子聚合 | DmgrPwang_industry_equ_fancy_factors(82) | ✅ pwang 同事 |
| 资金流 | AShareMoneyFlow(95) | ✅ |
| 外部研究员信号 | signal_rsh | ❌ **已弃用** |

## 顶层目录关系(快照式 view)

```
/datasvc/data/
├── cc_all/       1.6T   真实物理存储,持续日增,各子 .meta 水位 ~ 当前日 (例: 20260602/3997 @ 06-06)
├── cc_2024/      ~500G  2024 年 T 轴快照,主要走物理副本,但仍允许追加新 feature 类型
├── cc_2025/      ~23M   2025 年 T 轴快照,绝大多数文件 symlink → cc_all
├── cc -> cc_2024 软链
├── cc_link/      L2 feature 精选 symlink 视图 (link.py 维护)
└── link.py       辅助脚本
```

**注意**: cc_2024 / cc_2025 不是"严格冻结快照", **T 轴冻结但新 feature 类型仍可追加**。例如 2026-06-05 在 cc_2024 下新加了 AIFcst 系列目录, 各自 .meta cutoff = 20241231。所以"size 比上次记的大" 不一定是 bug, 可能是 backfill。

**另一层重要含义**: cc_2024 / cc_2025 也是**机器间分发的最小单元**。本地办公室 144 拿到的就是这两个快照 (没 cc_all), 见 [[reference-server-topology]]。所以在 144 上做研究天然只有 ≤20251231 的数据, 是硬 OOS 边界。

**三地 cc 不是 byte-identical**: 北京 160 / 上海 147 / 本地 144 三地各跑 build_cc, 同 rawdata CSV 但 build 时间 + config 可能不同, 输出会有微小差异。严格 reproducibility 看机器, 别假设跨机一致。

**`.meta` 是 gsim 的硬约束**:gsim 读数据时严格按 `.meta.lastDate / dateCapacity` 截断, **保证时间快照下不偷未来 / 不用未审计数据**。同时也是 writer 侧的水位 (data-writer 跑增量时, 从 `.meta` cutoff + 1 接着算到 XML cfg endDate)。`.meta` schema 三件套见 [[reference-company-data-architecture]]。

- `cc_2024`:物理文件是 2024 末快照(close.npy=160M=3657*5484*8),独立副本。gsim 跑 2024 末时点回测用这个。
- `cc_2025`:`.meta` 写 3900,但 close.npy **symlink 到 cc_all**(节省盘空间)。gsim 看到 .meta 就只读前 3900 行,即使物理文件已扩展到 3995。
- `cc_all`:真实持续增长的数据底,**因子挖掘 / 探索默认用它**(能用到最新)。
- 切换数据集 = 切换"时点":cc_all 看研发实时;cc_2025 reproduce 2025 末回测;cc_2024 reproduce 2024 末回测。

## cc_all 全局坐标

存储:gsim 自定义二进制(raw memmap, 无 npy header),用 `np.memmap` 直读。

- `__universe/Dates.npy`: int64 YYYYMMDD,**3995 天**,20091214 → 20260529
- `__universe/Instruments.npy`: U32 unicode(6 字符股票代码,如 `000001`/`688796`),**5484 只**
- 标准形状:`(T, N)` float64 / int8;5min:`(T, 49, N)` f64;一致预期:`(T, 3, N)` 年度 / `(T, 12, N)` 季度

## "最后一天 NaN" 规则 (重要)

**任何 build_cc 跑的 `<enddate>=YYYYMMDD` 那一天, 在最后一行永远是 NaN 占位**, 真实数据在 `[-2]` 及以前。这是 by-design, 不是 bug:

- 跑 build_cc 时 `<enddate>` = "目标 build 到这一天", 但那天本身不会被填实数据 (因为盘前 / 盘中数据还没产生)
- 第二天再跑增量时, 才会把 `<enddate>` 那天的真实值刷到 `[-1]` 并新增一行 NaN

含义对**跨 cc 集比对**:
```
cc_2024     (enddate=20241231): idx=3656 (20241231) = NaN 占位, 数据止于 20241230
cc_2025_new (enddate=20251231): idx=3656 (20241231) = 真实数据 (因为目标是 20251231)
cc_all      (持续滚动):         idx=3656 (20241231) = 真实数据
```

也就是 **任意两个 enddate 不同的 cc 集, 在前者的 `<enddate>` 那一天必然存在 NaN diff**, 这不算漂移。

含义对 `cc_all[-1]` 协议: 这是同一规则的特例 (cc_all 的 `<enddate>` 永远 = "今天", 所以 `[-1]` 永远 NaN)。

## 切换数据集 = 切换"时点"


## cc_all 各组真实填充进度(实测扫尾部非 NaN)

| 数据类 | 文件容量 T_cap | 真实填到 idx | 最后日期 | 备注 |
|---|---|---|---|---|
| Basedata/ashare*/equ_factor_*/hf_*/AShareMoneyFlow/Interval5m/行情/universe (T=3995) | 3995 | 3994 | 20260529 | 基准,日增 |
| cn_equity_feature/* (6315 维日级 L2) | 4636 | 3994 | 20260529 | 日增,与 Basedata 同步 |
| cn_equity_feature_5min/* (917 维 5min L2) | 4636 | ~3958 | ~20260403 | 日增,~30 日 lag |
| realtime/feature_cuts_1430/* (32 维) | 4636 | ~3959 | ~20260407 | 同 5min |
| cn_equity/forward_returns/* (64 标签) | 4636 | ~3869 | ~20251119 | 标签需未来收益,自带 lag |
| signal_rsh | 3900 | 3900 | 20251231 | 已上线信号(冻结) |
| delta/daily/YYYYMMDD/{date}.npy | 切片式 (F, N) | 3635 → 3994 (360 天) | 20260529 | 日落盘,F 自增(2531→3214) |

L2-feature 用 NioData memmap 预分配到 4636 行,日增直接 in-place 写。

`.meta.dateCapacity` 与"实际物理填充末端"可能不一致:`.meta` 是 gsim 框架认的;物理填充由 ETL 流程在写。**gsim 框架内**严格按 `.meta`;**探索/挖掘**可以扫实际非 NaN 末端用满最新数据。

## 关键 universe / mask

- 默认池(信号密度最高):`TOP3000` (T,N) i8
- 指数:`HS300` `ZZ500` `ZZ1000`
- 全市场口径:`ALL` (上市可交易) / `ALL_GIM` / `ALL_TRD` / `FULL`
- 状态:`Basedata/{st,status,industry,sector}.npy`、`PriceLimit/{upper,lower}_limit.npy`、`ipo/ipodate.npy`

## 主要数据分组

- **Universe/Mask**: ALL/ALL_GIM/ALL_TRD/FULL, HS300/ZZ500/ZZ1000, TOP1000~4000, __universe, ipo (int8 mask)
- **行情**: Basedata (18), ashareeodprices (20), adjfactor, PriceLimit, aindexeodprices (81 指数 (T,) 一维), Interval5m (9 字段 × 49 bar)
- **量价衍生**: Dpv/Dpva/Dpvb/Dpvc/Dpvd 各 20, Dipv/Dipva 各 20, Dmgr_adv20, Dmgr_MktRet, DmgrWbai_AIndexCSI500/1000Weight
- **资金流**: AShareMoneyFlow (95 字段, exlarge/large/med/small × buy/sell × trades/value/volume)
- **高频日级衍生**: hf_daily_auction_table (24), hf_daily_der_table_1~9 (共 223)
- **5min 高频**: Interval5m (9), cn_equity_feature_5min (917, 三子集 yq_212/fb_224/dw_57)
- **gsim 风格因子**: equ_factor_* 15 表共 433 字段(obos/trend/return/volume/power/ma/derive/growth/af/cf/oc/pq/psi/sc/vs)
- **fancy 因子**: equ_fancy_factors_table1~10 (208), equ_h2l_factor_t1~t4 (122), DmgrPwang_industry_equ_fancy_factors (82 行业聚合)
- **财务原始**: asharebalancesheet (174), asharecashflow (118), ashareincome (103)
- **一致预期**: ashareconsensusrollingdata_{FY0/1/2/3/FTTM/CAGR/YOY/YOY2} 各 17,(T,3,N) 年度 fore / (T,12,N) 季度 fore
- **L2 已聚合**: cn_equity_feature/* 6315 维(fguo_max/trade2/trade3/ywang/0105/0106/1208/1209/1224/1230, sli_0206/0210~15, zzk_19, feature_cuts_1430)
- **Y 标签**: cn_equity/forward_returns/fwd_return_close_delay{0~3}_{1~60}d (64 个)
- **日增切片**: delta/daily/YYYYMMDD/{date}.npy + features.csv + meta.json(列出 feature_idx, feature_name, memmap_dir, source_table)

## 读法 snippet

```python
import numpy as np
ROOT='/datasvc/data/cc_all'   # or cc_2025 / cc_2024 for snapshots
T,N=3995,5484        # Basedata/ashare* 等真实形状
T_FEAT=4636          # cn_equity_feature / cn_equity / realtime 文件容量

dates = np.fromfile(f'{ROOT}/__universe/Dates.npy', dtype='int64')
insts = np.fromfile(f'{ROOT}/__universe/Instruments.npy', dtype='U32')

close = np.memmap(f'{ROOT}/Basedata/close.npy', dtype='f8', mode='r', shape=(T, N))
top3000 = np.memmap(f'{ROOT}/TOP3000/TOP3000.npy', dtype='i1', mode='r', shape=(T, N))
h5 = np.memmap(f'{ROOT}/Interval5m/Interval5m.high.npy', dtype='f8', mode='r', shape=(T, 49, N))
feat = np.memmap(f'{ROOT}/cn_equity_feature/fguo_max/fguo_max.vol_trade_h1_h4_ratio.npy',
                 dtype='f8', mode='r', shape=(T_FEAT, N))
y = np.memmap(f'{ROOT}/cn_equity/forward_returns/fwd_return_close_delay1_5d.npy',
              dtype='f8', mode='r', shape=(T_FEAT, N))

# delta/daily 切片:(num_features, N)
import json
with open(f'{ROOT}/delta/daily/20260529/meta.json') as f: m=json.load(f)
day = np.memmap(f'{ROOT}/delta/daily/20260529/20260529.npy',
                 dtype=m['dtype'], mode='r', shape=tuple(m['shape']))
```

## 因子挖掘对齐规则

1. **训练样本期**:日级 L2 跟 Basedata 同步到 idx 3994 (20260529),可直接对齐。
2. **5min L2 / realtime 有 ~30 日 lag**,用之前扫一下最后非 NaN 行确认。
3. **Y 标签**:`forward_returns/fwd_return_close_delay1_5d` 只到 ~20251119;若要更近 OOS,自己用 `Basedata/close × adjfactor` 算 fwd ret(也可用 `ashareeodprices.s_dq_adjclose_backward.npy`)。
4. **Universe 过滤**:训练默认 `TOP3000 & ALL_TRD & ~st`。
5. **形状不一致警告**:`Basedata` 等 (3995, N),`cn_equity_feature/*` (4636, N),对齐时按 idx 切。
6. **快照切换**:回测时把 ROOT 切到 cc_2024 / cc_2025 可 reproduce 当时的状态;研究/挖掘用 cc_all。

完整字段清单见 `gsim-ops/docs/gsim/cc_all_fields.csv`(9902 条)。

相关:[[reference-gsim-architecture]], [[reference-company-data-architecture]], [[reference-gsim-data-modules]], [[reference-gsim-xml-config]], [[reference-server-topology]], [[alpha-dump-to-feature-migration]]
