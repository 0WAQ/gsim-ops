# Compliance 摸底(`scripts/compliance_survey.py`)

**目的**:compliance 判定重做前,先产出**阈值无关的逐日持仓分布**,用数据定策
(先测量后定策)。纯只读,零 sudo,只往 `--out` 目录落缓存,不碰任何生产写路径。

**状态**(2026-07-15):脚本已沙盘 + 五问对抗验证过关;数据源等价性已确认。分支
`claude/compliance-survey`。摸底方案与缺陷全清单见 `.claude/plans.md` "Compliance
判定重做"节。

---

## 它测什么

对每个因子、每个交易日,存 **compliance checker 判定所需的四个原始量**
(`ComplianceChecker._check_position` 同款,`ops/services/check/checker/compliance_checker.py`):

| 列 | 定义 | 对应阈值 |
|---|---|---|
| `total_abs` | 当日总敞口 Σ\|w\| | max 占比的分母 |
| `max_pos_pct` | 单股最大占比 max\|w\|/Σ\|w\| | `max_position_pct`(现 5%) |
| `long_count` | 多头持股数 #(w>0) | `min_long_stocks`(现 50) |
| `short_count` | 空头持股数 #(w<0) | `min_short_stocks`(现 50) |

**不存**"当前阈值下的违规计数"—— 那只能回答一个问题。存原始分布后,任何候选政策
("5% 改 4.5% 多拒多少"、"容忍 0.5% 天数"、"纯多头豁免")都是对缓存的秒级查询。

产出(`--out` 目录):

| 文件 | 内容 |
|---|---|
| `<name>.npz` | 逐日四列(`total_abs`/`max_pos_pct`/`long_count`/`short_count`,长 PACK_L=3900) |
| `summary.csv` | 每因子一行:有效起末日 / valid_days / gap_days / maxpos 分位数(p50/95/99/max)/ 多空/总持股 min·p05 |
| `universe_dates.npy` | 行号 → 交易日 标尺(一次) |
| `coverage-missing.txt` | 该源无数据的因子名单 |

**四条 compliance 阈值全能从 `summary.csv` 复算**:`max_position_pct`←`maxpos_max`、
`min_long_stocks`←`long_min`、`min_short_stocks`←`short_min`、`min_total_stocks`←
`total_min`(单列,因 `min(多+空) ≠ min(多)+min(空)`)。

---

## 两个数据源与等价性(已确认)

- **feature**:`alpha_feature/<name>.v2.npy`,裸 memmap `(3900, H)`,pack 直写无 npy 头。
  快、JFS 共享,但只在 **packed(≈ACTIVE)** 因子上有。
- **dump**:`alpha_dump/<name>/YYYY/MM/<yyyymmdd>*.v2.npy`,逐日 `(H,)` 向量。
  **被拒因子(compliance/correlation)无 feature,只有 dump**,是完整判定域的另一半。
  dump 是**本机 sidecar**,只能在持有该因子 dump 的机器上跑。

**feature 是 dump 的忠实代理**(2026-07-15 五问对抗验证,`compliance_checker` /
`pack.py` 逐行核实 + 数值对拍):

- pack 转换是**纯字节搬运**,无缩放变换 —— pack 自带的 `verify_sample` 逐位比对
  `feature[行] == dump[日]`(ATOL=1e-6,NaN-aware),本身即证明数值恒等;
- survey 的四列 = checker 同款 numpy 表达式:**多空计数 / max 占比 / 跳过规则逐位
  精确一致**;`total_abs` 仅差最后一个 ULP(feature 的 `nansum`-over-padded vs
  checker 的 `sum`-over-valid,最坏 ~5e-16,阈值 0.05 —— 差 13 个数量级,不翻任何判定);
- **逐日分布统计与数据源无关**:百分位 / 计数 / valid_days / gap 全部源不变。

**唯一源相关处(已知、无害、留后续)**:feature 行号带 delay 偏移(delay=1 时行 i
存的是交易日 i+1 的 dump),故 `first/last_valid_date` 在 delay=1 feature-读因子上比
真实 dump 日**早 ≤1 交易日** —— 只动日期标签、不碰阈值分布。dump 路径无偏移,标签
与 checker 的文件名日期一致。跨源精确对齐等把 rejected 因子的 dump 折进来时再做。

---

## 怎么跑

**机器**:JFS 可达节点(160/150/170 皆可 —— feature 在 JFS 共享)。dump 源须在持有
该因子 dump 的机器上跑。

### 0 · 切分支 + 同步

```bash
cd ~/gsim-ops
git fetch origin claude/compliance-survey
git checkout claude/compliance-survey && git reset --hard origin/claude/compliance-survey
uv sync --quiet
df -h ~ | tail -1        # 抽检产出很小;全量缓存约 1G
```

### 1 · 随机抽检(推荐先跑这个)

`--sample N` 只从**该源确有数据**的因子里抽,保证抽到的 N 个都出统计;`--seed` 固定
可复现。

```bash
uv run python scripts/compliance_survey.py --source feature --sample 8 --out ~/csurvey-sample
```

跑完贴回:

```bash
head -1 ~/csurvey-sample/summary.csv                 # 表头
column -t -s, ~/csurvey-sample/summary.csv | less -S  # 或整表贴回(纯统计,无敏感信息)
# 抽一个 npz 看结构
uv run python -c "import numpy as np,glob; f=sorted(glob.glob('$HOME/csurvey-sample/*.npz'))[0]; d=np.load(f); print(f.split('/')[-1]); [print(k,d[k].shape,d[k].dtype) for k in d.files]"
```

**自检**(不过就停下贴报错):`source` 列全 `feature`;`maxpos_max`/`long_min`/
`short_min`/`total_min` 有值;无 `Traceback`;npz 四数组长 3900。

### 2 · 全量(可选,抽检判读后按需)

```bash
nohup uv run python scripts/compliance_survey.py --source feature \
    --out ~/compliance-survey > ~/compliance-survey.log 2>&1 &
echo "PID=$!"
```

断点续跑安全(已有 `<name>.npz` 跳过);顺序读大(每因子 ~171MB feature memmap),
JFS 上小时级,峰值内存 ~22MB。监控 `tail -3 ~/compliance-survey.log` /
`ls ~/compliance-survey/*.npz | wc -l`。

### 3 · 回收判读

把 `summary.csv` 贴回 / 压缩发回 → 出全库(或样本)分布 → 按数据定阈值。

---

## 已修(对抗验证收口,commit `de53235`)

- **dump 路径越界崩溃**:`survey_one_dump` 原无上界守卫,而 universe 已含 20251231
  后的日增日(di 逼近/超 PACK_L=3900),dump 路径任一 2026 日期即 `IndexError` 崩
  整轮。补 pack 同款 `di >= PACK_L` 守卫(与 pack/feature 同域丢弃日增段)。
- **`total_min`/`total_p05` 列**:补齐 `min_total_stocks` 判据(边际 min 合不出总 min)。
- **nanmax 哨兵收窄**成 `== -inf`:真 +inf 坏权重不再静默吞成 NaN。

## 后续

抽检/全量分布判读 → 用户按数据定策(容忍度 / 有效天数下限 / gap / 纯多头豁免 /
5% 口径)→ checker 重写 + 对 22 条已被拒 compliance 因子做新旧影子对比(回归材料)
→ 顺手修 long_backtest 的 `prepare` 显式声明 `dump_alpha=True`(缺陷 6)。
