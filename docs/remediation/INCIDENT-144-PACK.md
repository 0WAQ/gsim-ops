# 事件评估 · 144 孤儿 pack 与 5757 个 alpha_feature(只读)

> **已结案(2026-07-08,wbai 拍板)**:7/1 的 pack 是 wbai 本人执行;
> **alpha_dump 任意时刻/任意机器等价**(dump 是 check 回测的确定性产物,
> 从哪份副本打包结果相同)→ 5757 个 feature 内容无污染;feature 可随时
> `ops pack` 重打,无数据丢失风险。事件降级为"正常操作未善后 + 孤儿残留"。
> A1/A3 无需执行。**剩余动作**:① wbai 清 3 个 tmp 残渣(A4 的一条 sudo rm,
> 随时);② 升级窗口收官后在 IDC 机器用新代码跑一次裸 `ops pack` 补齐缺失与
> 半对(A2 可先跑一次拿基数,非阻塞);③ 升级窗口从阶段 0a 重新开启。
> 整改记账(pack 孤儿 residue / 数据源守卫)保留,随窗口收官进 JOURNAL。

对应事件记录:`VERIFY-UPGRADE-150-144-RESULT.md` E1-E6。本手册全程**只读**
(除最后清 3 个 tmp 残渣需 wbai root)。

## 定性修正(代码侧已核实,rev 6225b7b)

裸 `ops pack`(取证确认无 `--force` 无 `-c`)的候选过滤逻辑是
`if not force: candidates = [n for n in candidates if not _is_packed(n)]`,
`_is_packed` = **v1 与 v2 都存在**才跳过。因此:

- **这 5757 个 feature 在 7/1 之前并不存在(或只存在半对)—— 这是一次
  backfill(补齐缺失),不是覆盖既有生产 feature**。
- 唯一的"覆盖"边界情形:此前恰好只有 v1/v2 之一的因子,pack 会重写整对
  (旧的那只被覆盖)。规模见 A2。
- 风险问题因此从"好数据被坏数据覆盖"变为两个:
  1. **新造出来的 feature 可信吗?**(取决于 144 的 alpha_dump 数据源新旧)
  2. **backfill 完整吗?**(父进程 7/2 中途死亡,队列里没跑完的因子仍缺 feature,
     另有 2 个死在写入中途)

## 待 wbai 回答的两个问题(比任何命令都重要)

1. **7/1 那次 `ops pack` 是谁、为什么跑的?** 是不是有意的 feature backfill?
   为什么在 144?(它以 root 跑起来的,是有人敲的命令,不是 cron。)
2. **生产 combo 当前是否消费这 5757 个 feature**(97% 是 Fguo/Hwang 因子)?
   —— 决定后续修复的紧迫度。

## A1 · dump 来源判定(最高信息量,先跑这个)

pack 读的是 `<alphalib>/alpha_dump`。它到底是共享 JFS 目录还是 per-host 本地
sidecar,直接决定事件严重性:

```bash
# 144 上:
readlink -f /storage/vault/alphalib/alpha_dump
df -T $(readlink -f /storage/vault/alphalib/alpha_dump) | tail -1   # fuse.juicefs 还是本地 fs?
# 160 上:
readlink -f /tank/vault/alphalib/alpha_dump
df -T $(readlink -f /tank/vault/alphalib/alpha_dump) | tail -1
# 双机抽查同 3 个受影响因子的 dump 目录(从 E5 名单选,Fguo/Hwang/Zxu 各一):
ls <resolved>/Alpha<X> | head -3 ; find <resolved>/Alpha<X> -name "*.npy" | wc -l
```

- **分支一:双机 resolve 进同一 JFS 卷(fuse.juicefs,文件数一致)** →
  pack 读的就是 160 也会读的同一份数据,**feature 内容无污染问题**;事件降级为
  "未完成的 backfill + 孤儿善后",只剩 A2/A4,A3 跳过。
- **分支二:resolve 到 `.local` 本地 sidecar(或双机不一致)** → 144 用本地
  副本打包,须做 A3 指纹比对。

## A2 · backfill 完整性(160 上跑,只读)

```bash
cd ~/gsim-ops && uv run python - <<'EOF'
from pathlib import Path
from ops.core.state import FactorStatus
from ops.infra.config import Config
from ops.infra.store import default_store
c = Config.load(Path("config.yaml"))
names = [r.name for r in default_store(c).list(status=FactorStatus.ACTIVE)]
pair = lambda n: ((c.alpha_feature / f"{n}.v1.npy").exists(),
                  (c.alpha_feature / f"{n}.v2.npy").exists())
missing = [n for n in names if pair(n) == (False, False)]
half    = [n for n in names if pair(n)[0] != pair(n)[1]]
print("ACTIVE 因子        :", len(names))
print("整对 feature 缺失  :", len(missing), "(队列没跑完的部分)")
print("只有半对(疑似死在中途/曾被单边重写):", len(half))
print("半对名单:", half[:20])
EOF
```

同时导出受影响名单备用:

```bash
find /tank/vault/alphalib/alpha_feature -name "*.npy" -newermt "2026-07-01" ! -newermt "2026-07-03" \
  | sed -E 's|.*/||; s|\.v[12]\.npy$||' | sort -u > /tmp/affected-5757.txt
wc -l /tmp/affected-5757.txt
```

## A3 · dump 指纹比对(仅分支二)

双机各跑一份指纹,拉到一处 diff:

```bash
# 160: ALPHALIB=/tank/vault/alphalib;144: ALPHALIB=/storage/vault/alphalib
ALPHALIB=... python3 - <<'EOF' > /tmp/dump-fp-$(hostname).txt
import os
from pathlib import Path
root = Path(os.environ["ALPHALIB"]) / "alpha_dump"
for d in sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("Alpha")):
    files = list(d.rglob("*.npy"))
    latest = max((f.name[:8] for f in files), default="-")
    print(d.name, len(files), sum(f.stat().st_size for f in files), latest)
EOF
```

对 `/tmp/affected-5757.txt` 里的每个名字分三桶:
- **一致**(双机 n/bytes/latest 全等)→ feature 可信,不动;
- **144-only**(160 无该因子 dump)→ 144 是唯一数据源,feature 至少是"仅有的
  数据",记录即可;
- **不一致** → 疑似 stale,进重打名单(修复阶段在权威机器
  `ops pack --factor X --force` 重打;写操作,**留到升级窗口收官后用新代码跑**)。

## A4 · tmp 残渣与半成品定性

3 个 `.tmp`(E6)逐个查目标文件状态:

```bash
for t in ".AlphaZxu_260621_PxRangePos_delay1.v1.npy.tmp" \
         ".AlphaYbai0615TaxTruth.v1.npy.tmp" \
         ".AlphaYbai0615ValueRankEPTTMQ4TaxResid.v1.npy.tmp"; do
  n=${t#.}; n=${n%.tmp}
  ls -l /tank/vault/alphalib/alpha_feature/$n /tank/vault/alphalib/alpha_feature/${n/v1/v2} 2>&1
done
```

目标缺失/只有半对的因子并入 A2 的 half 名单。确认后由 wbai 清理残渣
(`sudo rm /tank/vault/alphalib/alpha_feature/.Alpha*.tmp`,只删点开头 tmp)。

## 决策矩阵(评估完成后)

| 评估结果 | 处置 |
|---|---|
| 分支一(dump 共享)| 事件降级记录;窗口重新开启;缺失/半对 feature 的补打排到升级后 |
| 分支二 + 不一致桶为空 | 同上 |
| 分支二 + 不一致桶非空 | 窗口照开(代码升级与数据修复独立);收官后按名单重打;若 wbai 确认生产 combo 正在消费不一致因子 → 提级,wbai 决定是否紧急重打 |

**升级窗口不被本事件阻塞**:进程已清、写已停止,评估是只读的;而修复(重打)
本来就该用升级后的新代码跑。评估与开窗可并行,唯一顺序约束是**重打在窗口收官后**。

## 整改记账(随窗口收官进 JOURNAL)

- pack 孤儿 worker 是无人兜底的 crash residue 类别(staging 有 `ops clear`,
  pack 没有);现版本同为 ProcessPoolExecutor,风险仍在 → `ops doctor` 需求。
- pack 无数据源守卫:任何机器(含冷副本)的裸 `ops pack` 都能写共享
  alpha_feature → 候选整改:pack 增加 dump 来源/机器角色确认,或至少批量
  写入前 apt 风格确认(与其它批量命令对齐)。
