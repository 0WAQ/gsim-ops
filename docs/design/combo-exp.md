# combo 实验框架(讨论稿 v0.1,2026-07-20)

> 状态:**讨论中,未收敛**。背景:combo 产线 v1 已落地(Combo_su10 + TOP3000 +
> delay1,`scripts/build_combo_xml.py` 生成,生产根 `/nvme125/production/combo/`)。
> 但首跑发现 2026 年口径与现役差异巨大(本版全负 vs 现役强正,差异变量:
> TOP3000 / 中性化 / Combo 模块 / 腿集合)——调参迭代需要一套低成本实验框架。
> 本文先把数据流与三类实验形态钉下来,后续逐节讨论收敛。

## 1. 数据流与 v1/v2 资产

```
cc/feature 数据 → 因子模块 → v1(因子原值)
                  v1 → Operators 链 → v2(算子变换后)
                  v2 →(Combo 的输入 / Stats 的输入)
Stats 只消费数据出 pnl,不改变数据本身。
```

**v1/v2 不是概念,是已落盘的资产**:dump 目录每个交易日双版本
(`YYYYMMDDv1.npy` / `YYYYMMDDv2.npy`,因子级 alpha_dump 与组合级
combo_dump 同构)。**v1 落盘 = 因子/cc 数据重算可跳过**,这是实验低成本的关键。

**例外(依赖 Stats 状态的 Combo)**:依赖 pnl 历史/权重的 Combo(如 bj202 的
IR 权重)不在"两点变化"的纯管线里,实验设计单独对待。

## 2. 三类实验形态(成本排序)

| # | 实验 | 输入 | 动作 | 成本 |
|---|---|---|---|---|
| E1 | **Stats 参数** | 现有 v2 dump | 只换 Stats 配置(thres/index_ret/费率/…)→ 新 pnl;mode1/mode2 形态,`run.py` | 秒级 |
| E2 | **Operation 参数** | **v1 dump** | `AlphaLoad ver="v1"` → 重算 Operators 链 → 新 v2 → Stats;同样 mode 形态,多一步算子重算 | 分钟级 |
| E3 | **Combo 更换** | 腿 v2 dump | 新 Combo 模块/参数重算 → 新 dump → 新 pnl | 小时级(全量) |

要点:E1/E2 都不动因子、不动 combo 本体;E2 之所以便宜,是因为 v1 已落盘。
E3 改变的是"哪些腿、什么权重进组合"的本体,必须逐日重算优化器。

## 3. 目录结构(2026-07-21 收敛定稿)

**combo = 自包含目录;`combo/` 下每一个文件夹都是一个 combo。**

```
/nvme125/production/combo/
  fguo/                    # 一个 combo(生产态)
    dump/fguo/             # dump(SSOT,gsim 直写:2020/…/2026/ + weight/;
                           #   容器名套一层是 gsim dumpAlphaDir+id 的规矩,
                           #   与现役 combo_dump/lhw/lhw 同套路)
    xml/                   # mode0/1/2.xml(生成器产出)
    checkpoint/            # mode0 的 archive.bin
    pnl/mode0/ mode1/ mode2/
    logs/
  lhw/ zxu/ combo_eq/      # 同构;combo_eq 不特殊,就是第四个 combo
```

机制:`dumpAlphaDir` 指 `<combo>/dump`,gsim 自建 `<容器id>/` —— dump 收在
`dump/` 子目录内(2026-07-21 用户定:年目录不平铺);mode1/2 与 combo_eq 腿
的 `alphaDir` 指目标 combo 的 `dump/` 根,引用关系极简。

**dump SSOT 不变量**:同一份 dump 只有一个写入者(该 combo 的 mode0);
一切 Stats 类 XML(mode1/2 与 stats 变体)一律 `dumpAlphaFile=false`,只产
pnl,绝不重算 dump。

**实验与生产同构**:实验变体 = `exp/<variant>/` 下一个同构的 combo 目录
(非生产态),跑完要么删要么晋升进生产根 —— 一套布局语义覆盖生产与实验。
变体命名 = dump 级身份:`<combo>-<universe>-<opstag>`(如
`su10-top3000-neut` / `su10-alltrd-raw`);stats 变体不建目录,XML 落所属
combo 的 `xml/`,pnl 落 `pnl/stats-<tag>/`,直读所属 dump。

## 4. 工具形态(提议,待议)

`scripts/build_combo_xml.py` 加三个实验生成模式,产物落
`/nvme125/production/combo/exp/<实验名>/`(与产线 `xml/` 物理隔离,实验产物
不进 dump/pnl 产线根,落 exp 自己的子根):

- `exp-stats <基线XML> --set Stats.thres=85 --set index_ret=...`:从现役产线
  XML 派生,只改 Stats 参数块 → 直跑 run.py;
- `exp-ops <基线XML> --ops "AlphaOpDecay(days=5)" --ops "AlphaOpRank" ...`:
  AlphaLoad 改 ver=v1 + 重写 Operations 链 → 直跑 run.py;
- `exp-combo <基线XML> --combo Combo_su10 --set window=600 ...`:换 Combo
  模块/参数 → 全量重跑入口(确认制)。

实验结果物 = 各自 pnl(exp 子根下)+ simsummary 汇总表(2026 段优先,
当前关注窗)。消融矩阵(单变量对照)直接由这三个模式组合出来。

## 5. 首个真实用例:2026 口径差异消融

首跑差异对照(2026 段,年化 ret%):本版(su10+TOP3000+中性化)全负
(-12%~-20%)vs 现役强正(su10 +23.1 / bj202 lhw +31.3 / zxu +30.1)。
嫌疑排序:① TOP3000(砍掉小微盘收益段)② 中性化(中和风格收益)
③ Combo 模块(lhw/zxu bj202→su10)④ 腿集合(delay1-only)。
消融建议从 zxu 起步(最小,~15min/组):现役原样 / 只换 TOP3000 /
只加中性化 / 只换 su10,四组对照定真凶。

## 6. 开放问题(讨论清单)

1. ~~实验产物放 `exp/` 子根 vs 直接进产线~~(已收敛:自包含 combo 目录,实验 = exp/ 下同构目录)
2. E2 的 Operations 链参数化:参数面用声明式 CLI(`--ops "Mod(k=v)"`)
   还是实验专用 XML 模板手改?
3. 消融/对照的判读标准:以 2026 段 simsummary 为主,还是分年度对齐?
4. Combo(bj202 类)依赖 Stats 状态的实验怎么设计(先算 Stats 再喂 Combo?)
5. ~~实验与生产的口径防呆~~(已收敛:dump SSOT 不变量,Stats 类 XML 一律 dumpAlphaFile=false,生成器硬校验)
