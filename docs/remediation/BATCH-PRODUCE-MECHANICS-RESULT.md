# batch 计算生产因子 —— 机制实证结果

日期 2026-07-18/19(实验窗口 17:25 CST → 次日 00:46 CST)。机器:server-170
(128 核 / 1TB RAM)。分支 `claude/factor-production-features-pdnjdc`
(实验期 HEAD `1d75252`,实验未改仓库任何文件)。
实验目录 `/tmp/scratch_batch_test/`(已清理,脚本未入库;复跑方法以本文为准)。

**定位**:现行产线设计正主是 `docs/design/factor-produce-v3.md`(per-factor
形态,已过金丝雀)。本文是 **batch/分组形态**的机制实证储备 —— 钉死事实,
不构成对现产线的设计变更;分组方案本身**未拍板**。

## TL;DR

1. **per-leg dump/pnl 原生可行**:sibling `<Alpha>` 平铺(不经 Alphas 容器),
   产物逐因子落盘,命名直接对上 `alpha_pnl/<name>` 约定;与单进程产物位级一致。
2. **无腿级故障隔离**:任一条腿加载或运行失败,整组进程死。组 = 故障域,
   跑前必须 pre-check 全部腿的 `.py` 可加载。
3. **checkpoint 按序号反序列化,不按名字**:组成员一旦确定即冻结 —— 加腿崩、
   删末尾腿安全、删中间腿静默数据污染。成员变化 = 删 checkpoint 全史重建。
4. **`dumpAlphaFile=false` 静音开关不改 checkpoint 布局**:入库/退库的合法
   通道,不动组成员、不重建 checkpoint。
5. **checkpoint 续跑位级一致**:续跑段 580 个 dump 文件与一遍跑全部 byte-equal;
   save 点 = endIndex − checkpointDays(5)。
6. **规模**(短窗口 36 交易日):200 腿 137s / 6.5GB → 7475 全量约 86min /
   166GB。**全史**:200 腿 2h40m / 264GB → 7475 全量单流外推 ~100h / ~9.9TB,
   必须分组。
7. **全史 bootstrap 测算**:700 腿/组 × 11 组 ≈ 8.7 天,或 300 腿/组 × 25 组
   ≈ 8.3 天(串行);bootstrap 一次性,日常续跑只跑当天增量,分钟级。

## 实验设置

- 腿:真实 ACTIVE 因子(从 `ops list --format json` 取);XML 由脚本生成,
  公共 `<Modules>` 注册一次 Data,`<Portfolio>` 下 sibling `<Alpha>` 各带
  `dumpAlphaFile="true"` `dumpAlphaDir=<scratch>`。
- 短窗口 = 20250101–20250301(36 交易日);全史 = 20110104–20260717
  (~3700 交易日)。
- 跑法:`cd /usr/local/gsim && /usr/bin/time -v .venv/bin/python run.py <xml>`;
  checkpoint 实验用 `run_cp.py`。
- 取证三路交叉:gsim XSD(lxml 校验)+ 生产态 XML 旁证 + `.so` strings。

## ① dump 语义

- 现役 mode0 的 combo dump 是**容器级**:`combo_dump/<user>/<combo_id>/` 只有
  一个容器子目录,下挂 `YYYY/MM` 日期文件(v1.npy/v2.npy)与 `weight/YYYYMMDD`,
  **不是 per-leg**;`dumpAlphaFile/Dir` 设在 `<Alphas>` 容器上时,dump 的是
  combo 合成后的结果。
- 但 gsim XSD **原生支持腿级 dump**:`AlphaType` 有可选属性
  `dumpAlphaFile: boolean`、`dumpAlphaDir: string`;`PortfolioType` 允许
  unbounded 个 sibling `<Alpha>`。生产态单因子 XML 本来就是这个模式
  (`<Alpha dumpAlphaFile="true" dumpAlphaDir="/nvme125/alpha_dump">`)。
- **结论**:sibling `<Alpha>` 平铺天然 per-leg dump,复用生产态 XML 的 dump
  模式,零后处理。

## ② 小批位级一致(5 因子,短窗口)

| 指标 | 值 |
|---|---|
| wall | 7.91s |
| 峰值 RSS | 2.08GB |
| dump 文件 | 5×72 = 360 个,每因子独立 `dump/<因子名>/YYYY/MM/` |

与生产单进程产物 byte-diff:**尾部 corr = 1.000000000,位级一致**。头部差异
纯因短窗口 warmup 不足,decay 越大越明显(SPD40 corr 0.9994、SMD8 0.9958,
区间 0.9957~0.9999)—— `backdays=256` 对 decay=40 的因子 warmup 不够,是
窗口问题不是 batch vs 单进程的差异;生产用 checkpoint/长窗口不存在此问题。

## ③ 故障隔离:无,整组死

三种炸法全部整组崩:

| 炸法 | 崩点 |
|---|---|
| 腿的 `.py` 不存在 | `Portfolio.__init__` → `createModInst` AttributeError |
| 类继承错 | IndexError |
| `generate()` 运行期抛异常 | `portfolio.generate` 未 catch,进程退出 |

→ 生产必须 **pre-check 所有腿的 `.py` 存在且可加载**(gsim 无腿级容错);
分组策略必须容忍"单组当天失败"。

## ④ 规模标定

短窗口(36 交易日):

| 腿数 | wall | 峰值 RSS |
|---|---|---|
| 5 | 7.91s | 2.08GB |
| 50 | ~55s | 2,136,864 KB(~2.1GB) |
| 200 | ~137s | 6,471,812 KB(~6.5GB) |

7475 全量外推(短窗口):wall ~86min;RSS ≈ 基线 ~1.6GB + 22MB/腿 ≈ **~166GB**
(1TB 机器兜得住)。按 author 分组:fguo 4612 + Fguo 712 = 5324 分 3 组
(~1800 腿/组,RSS ~40GB/组)、lhw 663 一组、jzhang 882 一组、其余 ~600
一组,**6 组串行 ~2h**。

全史(200 腿,20110104→20260717,带 checkpoint):

| 指标 | 值 |
|---|---|
| wall | 2:40:43(exit 0) |
| 峰值 RSS | 277,025,008 KB(~264GiB) |
| archive.bin | 187MB |
| dump | 1,509,200 文件 / 64GB |
| RSS 曲线 | 65→95→173→(回落 93)→141→232→255→264 GB |

7475 全史外推:wall ~100h(~4 天)、RSS ~9.9TB → **必须分组并行**。
bootstrap 方案测算:

- 700 腿/组(264GB × 700/200 ≈ 924GB < 1TB),7475÷700 = 11 组串行,
  每组 ~19h → **~8.7 天**;
- 或 300 腿/组(~400GB RSS),25 组,每组 ~8h → **~8.3 天**;
- bootstrap 只做一次;日常续跑 per-group 只跑当天增量,**分钟级**。

全库 ACTIVE 计数 7475(2026-07-18 实测,`ops list`):fguo 4612、jzhang 882、
Fguo 712、lhw 663、hwang 348、zxu 158、xmf 69、ybai 19、sli 5、cchang 4、
wbai 2、interp 1。

## ⑤ checkpoint 续跑(5 因子,run_cp.py)

0101→0301 写 archive.bin,再续到 0401:

- phase2(→0401)wall 6.15s vs 一遍跑 9.29s;
- **续跑段 580 个 dump 文件与一遍跑全部位级一致**;
- archive.bin 5,628,226 B(≈1.1MB/因子);
- save 点 = endIndex − `checkpointDays(5)`;续跑 `if di<=savedi: continue`
  跳前段。

## ⑥ 成员增删 × checkpoint(分组语义判决)

| 操作 | 结果 |
|---|---|
| 加腿 | **崩**:`gsim_checkpoint.load` → `AlphaOpDecay.checkpointLoad` → EOFError |
| 删末尾腿 | 安全:尾部状态被截断忽略,4 腿位级一致 |
| 删中间腿 | **不崩但静默数据污染**:删除点之后全部 DIFFER |

机理:checkpoint **按序号反序列化,不按名字**。→ 硬约束:**组成员变化 =
必须删 checkpoint 全史重建**。

## ⑦ pnl per-leg + 静音开关

- pnl 也是 per-leg:`/nvme125/combo/test/pnl/` 下 `AlphaWbaiReversal`(腿级)
  与 `author_combo_eq`(容器级)并存;lhw mode0 `combo_pnl/lhw/mode0/` 共
  **743 个文件 = 742 腿 + 1 容器**。sibling 模式只产腿级(5 因子 = 5 个无后缀
  单文件,文件名 = Alpha id),**与 `alpha_pnl/<name>` 约定完全对上**。
- 静音微实验(5 因子 × 2 个月):phase1 五腿全开写 checkpoint;phase2 第 2 腿
  `dumpAlphaFile="false"` 续跑 —— 续跑不崩、静音腿 0 个新 dump、其余 4 腿
  续跑段 **54/54 位级一致**。→ **入库/退库用静音开关,不动组成员、不重建
  checkpoint**。

## 对分组设计的约束清单(若启用)

1. **组 = 故障域**:跑前 pre-check 全部腿的 `.py` 可加载;单组失败不波及
   其他组。
2. **组成员冻结(密封组)**:新因子攒成新组,不进既有组;成员变化 = 删
   checkpoint 全史重建该组(重建单价参考 ④:200 腿 2h40m / 264GB)。
3. **入库/退库走 `dumpAlphaFile` 静音开关**,不增删腿。
4. **组大小由全史重建内存峰值决定**(~1.3GB/腿):1TB 机器上限 ~700 腿/组;
   日常增量远轻(~22MB/腿,短窗口口径)。
5. per-leg 产物命名直接对上现有 dataset 约定,零后处理。

## 已知残尾与方法备注

- 200 腿全史实验的 `pnl_fullhist/` 目录为空(XML 里 `dumpPnl`/pnlDir 没配对),
  标注"不影响结论",未重跑补验。
- 任务①"Alphas 容器内腿级属性是否被读取"由 XSD + 生产 XML 旁证回答;
  `.so` strings 只证属性字符串存在,无反编译级确证。
- 实验脚本(`gen_batch_xml*.py`、`diff_check*.py`、`BogusAlpha.py` 等)随
  scratch 清理未入库;如需复跑,方法以本文记录为准。
