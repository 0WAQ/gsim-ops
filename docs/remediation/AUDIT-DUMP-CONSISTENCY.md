# AUDIT:产线 dataset vs 入库 check 产物的 dump 一致性抽样(执行者;只读)

背景:因子日增产线落点意向 = 沿用 `/nvme125/alpha_dump` dataset(cchang 近期新产,
20110101 起 + backdays 强制 256),但须先与 alphalib 的入库产物(check 时算的,
20150101-20251231,原 backdays)核对一致性。**四个合法差异源要与真 bug 分开**:
① backdays>256 因子被强制 256(预期漂,分组统计);② 窗口起点不同(跨日状态因子漂);
③ cc 跨机漂移(入库 dump 多在 160 用 160 的 cc_2025 算,dataset 在 170 用 170 的
cc_all 算);④ 其余 = 真差异。**核心信号:backdays≤256 组在交集窗口应逐位一致。**

全程只读(ls/cat/python 只读脚本),样本拷贝只进 scratch(如 /tmp/audit-dump/)。
结果贴回 `AUDIT-DUMP-CONSISTENCY-RESULT.md`。

## 1. 盘点:cchang per-factor prod.xml 存放处(gen diff 物料)

```bash
# 170 上:
find /nvme125 -maxdepth 3 -name 'prod.xml' 2>/dev/null | head
find /nvme125 -maxdepth 2 -type d -name '*xml*' 2>/dev/null
# 找到后:数量 + 任贴一份全文(与 alpha_src 模板对照的实证)
```

## 2. 盘点:dataset 因子集 vs ops ACTIVE 差集(治理底数)

```bash
# 160 上(或任意可连 PG 的机器):
psql "host=10.9.100.160 port=15432 dbname=ops user=ops" -tAc \
  "SELECT name FROM factor_state WHERE status='active' ORDER BY name" > /tmp/audit-dump/active.txt
# 170 上:
ls /nvme125/alpha_dump | sort > /tmp/audit-dump/dataset.txt
comm -23 /tmp/audit-dump/active.txt /tmp/audit-dump/dataset.txt | wc -l   # 在库未投产
comm -13 /tmp/audit-dump/active.txt /tmp/audit-dump/dataset.txt | wc -l   # 在产不在库
comm -13 ... | head -20                                                    # 样例(如 AlphaCchang*)
```

## 3. 抽样对账(核心)

**抽样框**:从两集交集中,按 alpha_src meta.json 的 backdays 分两组
(≤256 / >256)各取 10 个因子(作者/于 delay 混搭);日期取交集窗口四点:
`20150105 / 20180702 / 20211230 / 20251230`,版本 v2。

**取样**(check 侧 dump 在 160 sidecar):
```bash
# 160 上:
SIDECAR=/tank/vault/alphalib.local/alpha_dump     # ls 确认实路径
for f in <20 因子>; do for d in 2015/01/20150105 2018/07/20180702 2021/12/20211230 2025/12/20251230; do
  scp $SIDECAR/$f/${d}v2.npy 170:/tmp/audit-dump/check/$f-$(basename $d)v2.npy 2>/dev/null
done; done
```

**比对**(170 上,python 只读):
```python
import numpy as np, glob, os
for p in sorted(glob.glob('/tmp/audit-dump/check/*.npy')):
    name, date = os.path.basename(p)[:-6].rsplit('-', 1)   # Xxx-20150105 v2
    q = f"/nvme125/alpha_dump/{name}/{date[:4]}/{date[4:6]}/{date}v2.npy"
    if not os.path.exists(q): print(f"{name} {date} MISSING-in-dataset"); continue
    a, b = np.load(p), np.load(q)
    if a.shape != b.shape: print(f"{name} {date} SHAPE {a.shape}!={b.shape}"); continue
    same = np.nanmax(np.abs(np.where(np.isnan(a)&np.isnan(b), 0, a-b))) if a.size else 0
    byte = open(p,'rb').read()==open(q,'rb').read()
    print(f"{name} {date} byte={byte} maxdiff={same:.3e}")
```

**报告格式**:按 backdays 组 × 日期,统计 byte-equal / ATOL(1e-6)-equal / drift /
MISSING 四类计数 + drift 明细前 20 条。**不下结论**(判读方按四差异源分类)。

## 4. 顺带一测(若 ≤256 组有 drift):cc 漂移隔离

任选一个 drift 因子 + 一个字段(如 close),比对 160 `/datasvc/data/cc_2025` 与
170 `/nvme125/datasvc/data/cc_all` 同日行 md5 —— 区分"数据不同"与"计算不同"。
(方法参考 scripts/data-audit/cc_fingerprint.py,存量工具。)
