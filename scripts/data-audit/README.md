# cc 数据审计工具集

三个工具, 解决三个问题:

| 问题 | 工具 | 输入 | 输出 |
|---|---|---|---|
| **1. 单数据集异常检测** | `cc_validate.py` | 一个 cc root | 每文件质量报告 (NaN/inf/全 0/异常负值/范围) |
| **2. 同机跨 cc 目录是否一致** | `cc_fingerprint.py` + `cc_fingerprint_diff.py` | 多个本地 cc root | 两两 diff 报告 |
| **3. 跨服务器是否一致** | 同上 | 各机器各跑指纹 + rclone 中转 | 两两 diff 报告 |

工具仅依赖 `numpy`, 自包含, 可拷到任何机器跑 (147 内网隔离也可以)。

---

## 通用约定

- **T 切片**: 默认只看 `dates <= 20241231` 范围 (avoid yifei L2 缺 25 前数据 + 异步 build forecast 等已知不可比因素)
- **trim_last**: 默认砍尾部 1 天, 排除"build_cc enddate 那天 NaN 占位"造成的固有差异 (见 [[reference-cc-all-data-layout]])
- **跳过目录**: L2 系列 / 3D / universe mask / delta (脚本里 hardcode 了 `SKIP_DIRS`)
- **dtype**: 只处理 2D float64 / int8, 1D 序列和 3D 数据另算

---

## 用法 1: 单数据集异常检测 — `cc_validate.py`

扫一个 cc root, 给每个 `.npy` 出质量摘要, flag 出严重问题。

```bash
# 全量扫
python3 cc_validate.py --root /datasvc/data/cc_all --out validate_cc_all.json

# 限定某 module
python3 cc_validate.py --root /datasvc/data/cc_all --filter 'AShareMoneyFlow*' --out report.json
python3 cc_validate.py --root /datasvc/data/cc_all --filter 'Basedata*'        --out report.json

# 看更细 (前 20 个文件)
python3 cc_validate.py --root /datasvc/data/cc_all --limit 20 --out report.json
```

**严重度自动归档**:

| 严重度 | 条件 |
|---|---|
| `critical` | 全 0 文件 / inf 出现 / 应非负字段含负值 |
| `warn`     | 全 NaN 文件 (可能源真没数据, 但需关注) |
| `ok`       | 通过所有检查 |

退出码: 0 = 没有 critical, 1 = 有 critical。可直接接 cron 告警。

**输出 JSON 结构**:
```json
{
  "root": "/datasvc/data/cc_all",
  "cutoff": 20241231,
  "severity_counts": {"ok": 93, "warn": 2, "critical": 0},
  "results": {
    "AShareMoneyFlow/...": {
      "shape": [3997, 5484],
      "dtype": "float64",
      "n_nan": 8588630,
      "n_finite": 11460874,
      "n_posinf": 0, "n_neginf": 0,
      "stats": {"min": 0, "max": 5e6, "mean": 2331,
                "n_neg": 0, "n_zero": 0, "n_pos": 11460874},
      "flags": [],
      "severity": "ok"
    }
  }
}
```

**典型用法**:

```bash
# 跑完后看 critical 文件
python3 -c "
import json
r = json.load(open('validate_cc_all.json'))
for k, v in r['results'].items():
    if v['severity'] == 'critical':
        print(f\"{k}: {v['flags']}\")
"

# 看 warn (全 NaN, 可能源真没数据)
python3 -c "
import json
r = json.load(open('validate_cc_all.json'))
for k, v in r['results'].items():
    if v['severity'] == 'warn':
        print(k)
"
```

**heuristic 局限** (要校准时改 `cc_validate.py` 顶部常量):
- `NONNEG_KEYWORDS`: 字段名匹配 → 应非负 (value/volume/trades/price/...)
- `NONNEG_EXCEPTIONS`: 字段名匹配 → 跳过非负检查 (ret/diff/inflow/pct/rate/...)
- 误报 (该 ok 报 critical): 加进 `NONNEG_EXCEPTIONS`
- 漏报 (该 critical 没报): 业务上加进 `NONNEG_KEYWORDS` 或单独 hardcode

---

## 用法 2: 同机跨 cc 目录比对

两步: 各 cc root 各跑一次指纹 → diff 工具比对。

```bash
# 在同一台机器上跑 3 个 root 的指纹 (并行, IO 敏感场景跑慢一点)
python3 cc_fingerprint.py --root /datasvc/data/cc_all                --out fp_cc_all.npz &
python3 cc_fingerprint.py --root /datasvc/data/cc_2024               --out fp_cc_2024.npz &
python3 cc_fingerprint.py --root /tank/vault/datasvc/data/cc_2025    --out fp_cc_2025.npz &
wait

# 两两 diff (任意配对)
python3 cc_fingerprint_diff.py fp_cc_all.npz  fp_cc_2025.npz --out diff_all_vs_2025.json
python3 cc_fingerprint_diff.py fp_cc_2024.npz fp_cc_2025.npz --out diff_2024_vs_2025.json

# 查看汇总
python3 -c "
import json
r = json.load(open('diff_all_vs_2025.json'))
print(f\"match={r['summary']['match']} sum_diff={r['summary']['sum_diff']} \"
      f\"nan_diff={r['summary']['nan_diff']} only_a={r['only_a_count']} only_b={r['only_b_count']}\")
"
```

**指纹大小**: 每地 ~30-50 MB, 全 cc_all 跑一次 ~15-30 min (IO 敏感)。

**指纹定义**: 对每个 (T, N) 2D `.npy`, 沿 N 轴归约得 (T,) 向量
- `sum`: `np.nansum(arr, axis=1)` — 当日数据量级
- `nan`: `np.isnan(arr).sum(axis=1)` — 当日 NaN 数
- `shape` + `dtype`

**比对判定** (`cc_fingerprint_diff.py`):
1. dtype 不同 → `dtype_diff` (schema 漂移)
2. shape[1] (N) 不同 → `shape_diff`
3. nan_count 不严格相等 → `nan_diff`
4. sum 不 `np.allclose(rtol=1e-5)` → `sum_diff`
5. 否则 `match`

**控制参数**:
- `--rtol 1e-5`: sum 相对误差容差 (浮点累积差异)
- `--trim-last 1`: 砍尾部 N 天, 默认 1 天 (排除 enddate NaN 占位)。比对 cc_X (enddate=YYYY1231) vs cc_Y (enddate=ZZZZ1231) 时这条**很重要**, 不然每个文件都报 nan_diff

---

## 用法 3: 跨服务器一致性

跟用法 2 同样的脚本, 区别只在指纹生成的地点。

### 路径 A: 各机器都能 SSH (内网互通)

```bash
# 在机器 A (e.g. 160) 上:
python3 cc_fingerprint.py --root /datasvc/data/cc_all --out fp_160.npz

# 在机器 B (e.g. 144 本地) 上, 把脚本 + 跑:
scp cc_fingerprint.py wbai@10.6.100.144:/tmp/
ssh wbai@10.6.100.144 'python3 /tmp/cc_fingerprint.py --root /datasvc/data/cc_all --out /tmp/fp_144.npz'
scp wbai@10.6.100.144:/tmp/fp_144.npz .

# diff
python3 cc_fingerprint_diff.py fp_160.npz fp_144.npz --out diff.json
```

### 路径 B: 内网隔离 (147 不通 SSH, 走 rclone 中转)

```bash
# 在 160 上:
python3 cc_fingerprint.py --root /datasvc/data/cc_all --out fp_160.npz
rclone copy cc_fingerprint.py 39000:external-sync/scripts/

# 让 147 上的人 (或者你登 147) 拉脚本 + 跑 + 推回:
rclone copy 39000:external-sync/scripts/cc_fingerprint.py /tmp/
python3 /tmp/cc_fingerprint.py --root /datasvc/data/cc_all --out /tmp/fp_147.npz
rclone copy /tmp/fp_147.npz /tmp/fp_147.skip.json /tmp/fp_147.summary.json \
    39000:external-sync/fingerprints/

# 回到 160, 拉指纹比对:
rclone copy 39000:external-sync/fingerprints/ /tmp/147-fp/
python3 cc_fingerprint_diff.py fp_160.npz /tmp/147-fp/fp_147.npz --out diff_160_vs_147.json
```

**为什么不传数据**: 1.6T cc_all → 几十 MB 指纹, 跨地 / 跨网带宽友好。

---

## 已知局限

1. **3D 数据不处理** (`Interval5m` shape `(T, 49, N)`, `ashareconsensusrollingdata_*` shape `(T, K, N)`) — 写 3D 版本做扩展或单独 ad-hoc 脚本
2. **1D 序列不处理** (`aindexeodprices/*.npy` shape `(T,)`) — 81 个文件被 skip, 见 fp_*.skip.json
3. **沿 N 归约可能掩盖逐股偏差** (单股偏差大但全局抵消) — v2 加抽样 + min/max/std 多指标
4. **universe mask 不比** (用户 explicit 不要比, 只看数据本身)
5. **cc_validate 的非负 heuristic 是粗略的** — 可能误报 / 漏报, 见 §1 末尾

---

## 快速 troubleshooting

**Q: cc_validate 大量误报 critical neg_in_nonneg**
A: 该字段语义实际允许负 (e.g. 净值 / pct change / inflow), 把字段名关键词加进 `NONNEG_EXCEPTIONS`。

**Q: cc_fingerprint_diff 大量 nan_diff 都集中在最后一天**
A: 两个 cc 集 enddate 不同, 默认 `--trim-last 1` 没起效或者关了, 检查参数。

**Q: 指纹文件大小差很多**
A: 不同 root .npy 文件数差很多 (比如 cc_2024 缺 cc_2025_new 的派生 feature), 用 `cc_fingerprint_diff.py` 看 only_A / only_B 清单。

**Q: 想看每个 diff 文件的详细信息**
A: `cc_fingerprint_diff.py` 输出的 JSON 里 `diff_details[<filename>]` 有具体 `first_bad_day_idx`, `max_rel_err` 等。

---

## 历史 incident 报告 (使用本工具发现的问题)

- `docs/incidents/2026-06-06-cc-data-drift-160-vs-147.md` — 跨服务器审计 (用法 3)
- `docs/incidents/2026-06-07-interval5m-bugs.md` — 单数据 + 3D 异常发现 (用法 1 衍生)
