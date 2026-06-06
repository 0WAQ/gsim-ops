# gsim 代码三地双向漂移 — 严重事件报告

**日期**: 2026-06-06
**报告人**: wbai
**严重级别**: **CRITICAL** — 实盘机 (147) 跟研究/数据机 (160) 长期独立演化, 同名源文件内容不一致, 部分编译产物 (`.so`) 时差 6 个月、体积差 6 倍。两边都有未推到对方的修改, 没有 single source of truth, 不能局部修复, 需上级决策。
**当前状态**: 待上级请示, 本地不动手 sync。

---

## 1. 摘要 (给非技术读者)

公司的因子回测框架 (`gsim`) 在 3 台关键服务器上**各有一份代码副本**, 长期没有同步机制。这次审计发现:

- **实盘机 147** 和 **研究/数据生产机 160** 上的 `gsim/` 代码**不一致**, 既有源码差异也有编译产物 (`.so`) 差异
- 不是简单的"一边新一边旧" —— **两边都有对方没有的改动**, 长期各自迭代
- 受影响的核心模块包括: 回测引擎 (`alpha_node.so`)、组合算法 (`combo_equal_prod.py`)、相关性计算 (`Oputil.py`)、universe 定义 (`umgr_all.py`)、Alpha 加载 (`alpha_load.py`)、实盘专用算子和统计 (`AlphaOpRiskOpt*`, `StatsOptV1` 等)

**业务含义**: 我们当前**无法在 160 上 reproduce 147 实盘的行为**, 反之亦然。任何"研究→实盘部署"的链路默认前提 (两边算出来一致) 都不成立。

---

## 2. 涉及的机器

| 机器 | IP | 角色 | 本次审计状态 |
|---|---|---|---|
| 160 | 10.9.100.160 | 北京 IDC, 数据生产 + 研究 + NFS owner + JFS master | 本机审计 |
| 147 | 10.12.174.152 | 上海中信 IDC, **实盘 combo 机器** + rawdata 抓取 + cc first build | 通过 rclone 拉取 `external-sync/147/` 镜像审计 |
| 144 | 10.6.100.144 | 本地办公室, 冷副本 (只有 cc_2024/cc_2025) | `source_ref/` `dm_src/` 已对比无 silent drift (见 §6), `gsim/` 未对 |

---

## 3. 审计范围与方法

```
对比目录:
  /usr/local/gsim/source_ref/     # rawdata → cc 转换 module
  /usr/local/gsim/dm_src/          # cc → dm 派生 + L2 read-only adapter
  /usr/local/gsim/gsim/            # gsim 核心 Python 包 + C++ 编译产物

方法:
  - 拉取 147 镜像到 /tmp/147/
  - 对每个 .py / .so / .xsd 计算 sha256
  - 文件名集合 diff (160 ONLY / 147 ONLY / 共同)
  - 共同文件 sha256 对比 (silent drift 检测)
  - 共同但内容不同的文件: 逐个 `diff -u`
  - .so 文件: 大小对比 + `nm -D` 符号对比
  - 排除 `__pycache__/` 和 `build/`
```

---

## 4. 发现总览

### 4.1 `source_ref/` (62 文件 vs 52 文件)

- 共同 52 文件 **byte-identical** (零内容漂移)
- 160 独有 10 个 (147 没有), **147 独有 0**
- 160 独有清单见 §6.1

### 4.2 `dm_src/` (43 文件 vs 17 文件)

- 共同 16 文件 **byte-identical** (零内容漂移)
- 160 独有 26 个, **147 独有 0**
- 160 独有清单见 §6.2

### 4.3 `gsim/` 核心包 (183 vs 188 文件)

| 类别 | 文件数 | 严重度 |
|---|---|---|
| 共名 + 内容不一致 (silent drift) | **9** | CRITICAL — 包括 `alpha_node.so` 和 universe / combo / 加载逻辑 |
| 160 独有 | 6 | 含 FeatureReader (`alpha_load_feature.py`) — 因子库 feature 迁移依赖, 147 缺 |
| 147 独有 | 11 | 含实盘优化算子 + StatsOptV1 — 160 缺 |

---

## 5. CRITICAL 级别 — 9 个共名 silent drift (gsim/)

> 这是本次报告最严重的发现。两台机器在同名源/二进制文件上**长期独立演化**, 任何一方运行另一方的 XML config 都可能产生不可预期的行为。

### 5.1 `alpha_node.cpython-310-x86_64-linux-gnu.so` — 编译版回测核心

| | 147 | 160 |
|---|---|---|
| sha256 (前 16 位) | `b302a9f956e7568b` | `0d3d0c8e60bb44e4` |
| 文件大小 | 624,968 字节 (~610 KB) | 99,080 字节 (~97 KB) |
| 修改时间 | 2026-03-02 11:24 | 2025-09-09 17:14 |
| Cython 模块名 | `alpha_node` | `gsim__alpha_node` |
| 时差 | **半年** | |
| 体积差 | **6.3 倍** | |

**含义**: 这是 `.so` 编译产物, **无法读源码 diff**。147 上多出来的 ~500KB 编译代码是什么、是否改了语义, 本地无法判断。**这是黑箱漂移, 必须由 gsim 维护者 (上级或框架团队) 澄清**。

### 5.2 `combo/combo_equal_prod.py` — 实盘组合算法

147 比 160 多出 "production gating" 整段逻辑:

```python
# 147 有, 160 无
self.proddates = np.full(self.numalphas, 0)
for ai in range(self.numalphas):
    self.proddates[ai] = self.node.children[ai].cfg.getAttributeDefault("__productionday", 0)

# loadDay 里:
self.valid[self.proddates >= today] = False   # 屏蔽未上线 alpha

# computeWeights 里:
if self.valid[ai]:
    self.node.children[ai].weight = self.node.children[ai].cfgWeight
else:
    self.node.children[ai].weight = 0.          # 未上线给 0 权重
```

**含义**:
- 147 (实盘) 按 alpha 的 `__productionday` 把"上线日期晚于今天"的 alpha 权重置 0
- 160 (研究) 没有这层屏蔽, 默认所有 alpha 全开
- **同一份 combo XML, 160 跟 147 算出的权重完全不一样**, PNL 也不一样
- 任何"在 160 上 reproduce 实盘 PNL" 直接不成立

### 5.3 `utils/Oputil.py` — 相关性 / 数值计算工具

147 在相关性公式里加了 `+ 1e-8` 数值保护:

```python
# 147 (mtime 2026-01-10):
r = (n * sxy - sx * sy) / (np.sqrt(n * sxx - sx * sx) + 1e-8) / (np.sqrt(n * syy - sy * sy) + 1e-8)

# 160 (mtime 2025-09-20):
r = (n * sxy - sx * sy) / np.sqrt(n * sxx - sx * sx) / np.sqrt(n * syy - sy * sy)
```

**含义**:
- 160 在 zero variance 情况下输出 NaN / inf, 147 输出接近 0 的数
- **`ops check` correlation 阶段卡阈值 0.7**, 边缘 case 可能 160 拒、147 通过 (或反之)
- 影响 `bcorr` 等所有用 Oputil 的相关性计算

### 5.4 `data/module/umgr_all.py` — ALL universe 定义

仅 1 行 debug print 差异 (147 多一个 print), **无语义影响**, 仅噪音。

### 5.5 `alpha/module/alpha_load.py` — Alpha 加载

160 (2026-05-28) 给 `lag` 加了 `int()` cast:

```python
# 160:  self.lag = int(cfg.getAttributeDefault('lag', 0))
# 147:  self.lag = cfg.getAttributeDefault('lag', 0)
```

**含义**: 147 上 lag 可能是 string 类型, 隐性类型 bug。中等严重。

### 5.6 `operator/__init__.py` — 算子 re-export

147 和 160 走完全不同的算子注册集合:

```python
# 147 上能 import:
from .op_hump import AlphaOpHump
from .op_keep_top import AlphaOpKeepTop      # 空气指增
from .AlphaOpTopt import AlphaOpTopt
from .AlphaOpRiskOpt20 import AlphaOpRiskOpt20

# 160 上能 import:
from .AlphaOpHump import AlphaOpHump          # 走 .so 不同路径
# (没有 keep_top / Topt / RiskOpt20)
```

**含义**: **任何引用 RiskOpt20 / Topt / KeepTop 的 XML, 在 160 上直接 ImportError 跑不起来**。147 实盘 config 在 160 是无法 reproduce 的。

### 5.7 `stats/__init__.py` — Stats 注册

```python
# 147 (2026-04-30): 比 160 多 from .StatsOptV1 import StatsOptV1
# 160 (2026-06-05): 没有 StatsOptV1
```

`StatsOptV1.so` 也只有 147 有。**StatsOptV1 是 147 实盘 stats 模块, 160 无法跑**。

### 5.8 `alpha/__init__.py` + `alpha/module/__init__.py` — FeatureReader 集成

160 (2026-05-28) 比 147 多 re-export `AlphaLoadFeat`:

```python
# 160 alpha/__init__.py 多: 'AlphaLoadFeat'
# 160 alpha/module/__init__.py 多: from .alpha_load_feature import AlphaLoadFeat
```

`alpha_load_feature.py` 也是 160 独有文件。**147 没有 FeatureReader 能力**, 意味着 `ops pack` 出的 alpha_feature 矩阵 147 读不了, **因子库就算投产 147 也消费不了**, 除非先把这些推到 147。

---

## 6. 独有文件清单 (非 silent drift, 但说明各自演化方向)

### 6.1 `source_ref/` — 160 独有 10 个 (147 无)

```
bak_Dmgr_GFAA_5M.py                    # backup, 应该可忽略
Dmgr_asharebalancesheet_3d12q.py       # wind 财务 rolling 视图
Dmgr_asharecashflow_3d12q.py
Dmgr_ashareincome_3d12q.py
Dmgr_fguo_L2.py                        # L2 / fguo
Dmgr_gfaa_2.py
Dmgr_gfv2aa.py
Dmgr_L2ZZK.py                          # L2 ZZK
Dmgr_yf169.py                          # yf169
signal_rsh.py                          # 外部研究员信号 (已弃用)
```

### 6.2 `dm_src/` — 160 独有 26 个 (147 无)

```
# L2 / 5min adapter (5 个):
Dmgr_dw_57_5min.py
DmgrFguo_fb_224_5min.py
DmgrLhw_L2FeatureCuts1430.py
DmgrWbai_L2_yq_212_5min.py
Dmgr_L2ZZK.py

# Fguo 派生 (11 个):
DmgrFguo_{0105, 0106, 1208, 1209, 1224_order, 1224_trade, 1230, max, trade2, trade3, ywang}.py

# Sli 派生 (7 个):
DmgrSli_{0206, 0210, 0211, 0212, 0213, 0214, 0215}.py

# 其它 (3 个):
DmgrWbai_AIndexCSI500Weight.py
DmgrWbai_AIndexCSI1000Weight.py
Dmgr_gfv2aa.py
```

### 6.3 `gsim/` — 160 独有 (6)

```
./alpha/module/alpha_load_feature.py              # FeatureReader 实现 (2026-05-28 上线)
./data/Universe.cpython-310-x86_64-linux-gnu.so   # 编译版 Universe
./data/module/interval_5m.py                       # 5min 数据 module
./data/module/stats_simple2.cpython-310-x86_64-linux-gnu.so  (+ build/ 副本)
./operator/AlphaOpHump.cpython-310-x86_64-linux-gnu.so
```

### 6.4 `gsim/` — 147 独有 (11)

```
./AlphaOpRiskOpt10.cpython-310-x86_64-linux-gnu.so  + ./operator/AlphaOpRiskOpt10.so   # 风险优化
./AlphaOpRiskOpt20.cpython-310-x86_64-linux-gnu.so  + ./operator/AlphaOpRiskOpt20.so
./AlphaOpTopt.cpython-310-x86_64-linux-gnu.so       + ./operator/AlphaOpTopt.so       # Top 优化
./StatsOptV1.cpython-310-x86_64-linux-gnu.so        + ./stats/StatsOptV1.so           # 优化统计
./data/Universe.py                                  + ./data/bak.Universe.abi3.so     # Python 源 + abi3 备份
./operator/op_keep_top.py                                                              # 截断算子
```

### 6.5 Universe 实现的根本差异

```
160:  data/Universe.cpython-310-x86_64-linux-gnu.so  (cp310 编译版)
147:  data/Universe.py + data/bak.Universe.abi3.so    (Python 源码 + abi3 备份)
```

这是**结构性不同**: 160 走编译版, 147 走 Python 源版。`umgr_all.py` 的差异 (§5.4) 加上 Universe 实现入口本身就不同, **universe 这一层全公司因子的根基, 两台机器是不同的代码路径**。

---

## 7. 漂移方向分析 — 双向, 不是单向

按文件 mtime + 新增内容判断:

| 谁更新了 | 内容 |
|---|---|
| **160 单边更新** | `alpha_load.py` int cast; `alpha/__init__.py` 加 AlphaLoadFeat; `alpha_load_feature.py` 新增 (FeatureReader); `stats/__init__.py` 删除 StatsOptV1; `interval_5m.py` 新增; `data/Universe.so` 切换到编译版 |
| **147 单边更新** | `combo_equal_prod.py` 加 production gating; `Oputil.py` 加 1e-8 数值保护; `alpha_node.so` 大幅重编 (体积 6x, 半年内); 实盘优化算子套件 (RiskOpt10/20, Topt, KeepTop, StatsOptV1); `Universe.py` 改回源版 |
| **共名内容不同** | 不存在单向滞后, 是真双向 fork |

**结论**: 这不是"一边忘了同步"的问题, 是**两台机器长期作为独立 fork 在演化**, 各自服务不同目标 (147 服务实盘性能 + 风险控制, 160 服务研究效率 + FeatureReader 等新能力)。

---

## 8. 业务影响 (按风险倒序)

### 8.1 实盘 PNL 无法在研究机复现

`combo_equal_prod.py` 的 production gating 差异 + `alpha_node.so` 半年版本差 + 优化算子缺失 → **任何"看 147 上昨天 PNL 怎么来的, 在 160 上拉一遍"的尝试都拿不到一致结果**。这意味着实盘出问题时, 调试基本上只能在 147 上现场看, 没有研究环境 reproduce 兜底。

### 8.2 因子库 → 实盘的桥梁断裂

160 上的 FeatureReader (`alpha_load_feature.py` + `AlphaLoadFeat` re-export) 是 ops 这边因子库 (`/tank/vault/alphalib/alpha_feature/`) 投产的关键依赖。**147 没有这套**, 即使 alphalib 准备好投产, 147 也消费不了, 必须先迁移代码。

### 8.3 correlation check 阈值边缘 case 不一致

`Oputil.py` 数值保护差异 → 同一个因子, 在 160 上 `ops check` 的 correlation 数值跟 147 跑出来不一样。理论上**入库标准跨机器不一致**。

### 8.4 XML config 跨机器不可移植

147 实盘 config 引用 `AlphaOpRiskOpt20` / `AlphaOpTopt` / `StatsOptV1` 等, 在 160 上 ImportError; 160 引用 `AlphaLoadFeat` 在 147 上 ImportError。**没有"一份 XML 两边都能跑"的共同子集是个未明状态**, 需要进一步审计 XML config。

### 8.5 universe 根基代码路径不同

Universe 加载本身实现不同 (.so vs .py) + `umgr_all.py` 内容不同。理论上 ALL universe 的 (T, N) bool mask 算出来可能存在差异。**整个公司的因子都建在 universe 之上**, 这一层不一致影响面是全员。

---

## 9. 为什么无法本地修复

1. **`alpha_node.so` 是黑箱**: cython 编译产物, 没源码, wbai 没有编译它的能力 / 权限 / 源码访问。147 上多出来的 500KB 编译代码改了什么, 本地完全不可见
2. **其它优化算子 `.so`** (RiskOpt10/20, Topt, StatsOptV1) 同样黑箱, 147 上谁编的、源码在哪、能不能在 160 重编, 都不清楚
3. **双向 fork** 不能一刀切 sync: 任何方向单向覆盖都会丢真实业务逻辑 (实盘的风控 / 研究的 FeatureReader), 需要文件级 merge 决策
4. **147 内网隔离**, ops 代码不在那, 没有直接推 / 拉的通道, 只能 rclone 走 external-sync 中转
5. **缺 single source of truth**: 不知道哪个版本"应该是对的", 没有 git 仓库追溯历史

---

## 10. 需要上级决策的点

### Q1. canonical source 是谁?

- 选项 A: **147 = canonical**, 160 / 144 跟随 (实盘优先)
- 选项 B: **160 = canonical**, 147 跟随 (研究优先)
- 选项 C: 建立 **独立 git repo 作为 master**, 三地都拉
- 选项 D: 维持现状, 接受两边都改, 各自负责

### Q2. 历史 fork 怎么 reconcile?

- 147 的实盘改动 (production gating, 风控算子, 数值保护) 是否应该回流到 160?
- 160 的研究改动 (FeatureReader, int cast, interval_5m) 是否应该推到 147?
- `alpha_node.so` 两版各保留什么? 谁有源码能合?

### Q3. 防再次漂移的机制?

- git + CI 强制要求 commit, 任何 push 都通知 (强但需要框架团队认可)
- 定期 cron 跑 sha256 比对, drift 告警 (轻量但仍有窗口)
- 共享文件系统覆盖 gsim 代码 (跟 alphalib 走 JFS 类似, 但对实盘有性能 / 隔离风险)
- 不做防护, 接受漂移 (现状)

### Q4. .so 编译权限和源码归属?

- gsim 框架的 cython 源码在哪? 谁维护? 编译产物分发流程是什么?
- 实盘 (147) 专用的 RiskOpt / StatsOpt 系列源码归属?
- 解决前一步是先搞清楚 build system

### Q5. 短期止血措施?

在 Q1-Q4 决策出来之前, 是否需要立刻:
- 冻结所有 gsim 代码改动 (任何方向)?
- 在 ops 这边对 `correlation` / `combo` 输出加跨机器一致性 check (即使代码不一致, 至少 detect 出 PNL 偏差)?

---

## 11. 附录: 复现命令

```bash
# 从 147 镜像拉取
mkdir -p /tmp/147/{gsim,source_ref,dm_src}
rclone copy 39000:external-sync/147/gsim/        /tmp/147/gsim/
rclone copy 39000:external-sync/147/source_ref/  /tmp/147/source_ref/
rclone copy 39000:external-sync/147/dm_src/      /tmp/147/dm_src/

# 对每个目录生成 sha256 manifest
for dir in source_ref dm_src; do
    (cd /tmp/147/$dir          && sha256sum *.py 2>/dev/null | sort) > /tmp/${dir}_147.txt
    (cd /usr/local/gsim/$dir   && sha256sum *.py 2>/dev/null | sort) > /tmp/${dir}_160.txt
done

# gsim/ 递归 (排除 __pycache__/build)
for site in 147 160; do
    src=$([ "$site" = "147" ] && echo "/tmp/147/gsim" || echo "/usr/local/gsim/gsim")
    (cd $src && find . -type f \( -name '*.py' -o -name '*.so' -o -name '*.xsd' \) \
        ! -path '*/__pycache__/*' ! -path './build/*' | sort | xargs sha256sum) \
        > /tmp/gsim_${site}.txt
done

# 文件集合 diff
comm -23 <(awk '{print $2}' /tmp/gsim_160.txt|sort) <(awk '{print $2}' /tmp/gsim_147.txt|sort)  # 160 ONLY
comm -13 <(awk '{print $2}' /tmp/gsim_160.txt|sort) <(awk '{print $2}' /tmp/gsim_147.txt|sort)  # 147 ONLY

# silent drift (共名内容不同)
join -j 2 -o '1.1,2.1,0' <(sort -k2 /tmp/gsim_160.txt) <(sort -k2 /tmp/gsim_147.txt) \
    | awk '$1 != $2 {print $3}'
```
