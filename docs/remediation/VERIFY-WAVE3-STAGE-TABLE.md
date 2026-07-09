# 增量验证 · wave3 + stage-table 滚存三机执行手册

**目标**:把三机从 `claude/remediation-wave0` 滚存到堆叠顶端
`claude/remediation-stage-table`,并对 wave0 之后的增量做行为级验证。全绿后
"分支合 main"的前置即告齐备。

**增量面**(wave0 → stage-table,共 86 文件):
- **wave3(A/C 系列)**:批量骨架 `_batch.py`(confirm/锁循环/汇总/失败双通道)
  + TOCTOU 锁内复验 + CAS `transition(expect=...)`(StateConflict);CI 工作流
  (ruff + pyright + fast suite)。
- **stage-table(P/D 系列)**:Stage 表 `stages.py`(PIPELINE 单一事实源,
  `_run_one_locked` 变 for-loop);**异常归因盖章**(CheckFail/CheckSkip 不携带
  stage,流水线按 current_stage 归因,12 个异常子类删除);`xml_prepare.py`
  声明式窗口改写(prepare 失败直接抛不再吞);`xmlio.py`/`factor_dir.py` 收敛;
  `Checker.clean()` 钩子;文档清扫。

**兼容性判定(为什么这次不需要停写窗口)**:锁键命名空间
(`hashtext('ops:factor_lock')`)、PG 三表结构、状态转移语义在 wave0 ↔
stage-table 之间零变化(diff 已核:lock.py 仅防御性微调,state.py 仅类型注解)。
混版本期间三机互斥照常成立,**滚动升级安全**。仅有的纪律:
1. 共享 `ops_test` ⇒ **多机测试严格串行**(U1 实测教训);
2. 金丝雀验证期间(阶段 3)其它机器照旧不要对**金丝雀名字**做写操作(常规
   advisory 锁即可保障,无需全网停写)。

**红线**(沿用既有):写操作只允许针对金丝雀 `AlphaWbaiCanary001`;实际输出与
预期不符**立即停止报告**,不自行修复;不动 redis/sentinel;不直接 SQL 写生产库。

---

## 阶段 0 · 160 部署 + 门禁

```bash
cd ~/gsim-ops && git status -sb        # 干净才继续
git fetch origin claude/remediation-stage-table
git checkout claude/remediation-stage-table && git pull origin claude/remediation-stage-table
git log --oneline -1                   # 记录 rev(报告用)
uv sync --group dev
uv run ruff check ops tests            # 预期 All checks passed
uv run pytest -m "not slow" -q         # 预期 51+ passed / 0 failed(确认无他机在跑测试)
uv run pyright ops                     # 预期 0 errors(首跑需联网拉 node,失败记录不阻塞)
```

新增测试文件应出现在收集里:`tests/test_batch.py`(批量骨架/TOCTOU/CAS,json
后端)、`tests/test_check_routing_json.py`(路由归因,json 后端)。

## 阶段 1 · e2e 重跑(本次增量的核心验证)

Stage 表重构动了流水线主干(运行块 for-loop 化 + 归因盖章 + prepare 抛错),
e2e 用真 gsim + 真 cc 数据对每个 stage 的确定性失败因子验路由 —— 这正是增量
风险所在:

```bash
uv run pytest -m e2e -q       # ~85s;预期全 passed
```

任何 fail:停止报告(附完整输出),不要继续阶段 2+。

## 阶段 2 · 只读冒烟(160)

```bash
uv run ops list 2>/dev/null | tail -1     # Total 与升级前一致(基线 7488 上下)
uv run ops list -u wbai | head
uv run ops status | head
uv run ops info <任选真因子>
uv run ops list --format json 2>/dev/null | head -5   # exit 141 正常
```

## 阶段 3 · 金丝雀行为环路(160)

一个环路串起 wave3 的批量确认/CAS 与 stage-table 的流水线/归因/产物策略。
准备(同 PV7 手册:双 config + dropbox 金丝雀重建):

```bash
export CANARY=AlphaWbaiCanary001
export CDATE=$(date +%Y%m%d)
# config.verify.yaml (corr=1.01) + config.verify-pv7.yaml (corr=0.7) 生成 snippet
# 与 dropbox 金丝雀重建 snippet 照抄 VERIFY-PV7.md 阶段 0(勿手抄模板)。
```

前提检查:`ls /tank/vault/alphalib/pnl_manual/AlphaWbaiReversal` —— 金丝雀的
孪生真因子须在池里(3c 靠它触发 REJECTED)。不在则 3c 预期改为直接 ACTIVE,
归因检查跳过并在报告注明。

### 3a · 入库(新流水线 happy path)

```bash
uv run ops submit -u wbai -s $CDATE -f $CANARY
uv run ops check -f $CANARY -c config.verify.yaml
```

预期:7 stage 全过 → ACTIVE;【速查】snap 有值(snapshot_at=entered_at),
`pnl_manual/$CANARY` 池副本存在。验证点:PIPELINE 表驱动的主路径 + archive
快照/池副本在新代码下不回归。

### 3b · 批量确认交互(wave3 `_batch`,此前无人工验证)

```bash
uv run ops restage $CANARY          # 不带 -y!
```

预期:打印计划(含 "dump/feature 保留为服务面…" 行)后出现 y/N 确认。
**先答 `n`** → 中止;【速查】state 仍 active,`alpha_pnl/$CANARY` 仍在
(零副作用)。再次运行**答 `y`** → 走 `apply_locked`(锁内复验 + CAS):
输出含两行"已回收"(pnl + pnl_manual),state → submitted,snap=None。

### 3c · 生产阈值 re-check(异常归因盖章 + on_reject 产物策略)

```bash
uv run ops check -f $CANARY -c config.verify-pv7.yaml     # corr=0.7
```

预期:correlation 撞孪生 `AlphaWbaiReversal`(bcorr≈1.0,业绩几乎相同打不过)
→ **REJECTED**。核对三点:

```bash
uv run python - <<'EOF'
import os
from pathlib import Path
from ops.infra.config import Config
from ops.infra.store import default_store
from ops.infra.snapshot import default_snapshot_store
c = Config.load(Path("config.yaml")); n = os.environ["CANARY"]
r = default_store(c).get(n)
print("status:", r.status.value)                 # rejected
print("fail_stage:", r.last_fail_stage)          # correlation ← 归因盖章(流水线,非异常子类)
print("fail_reason 片段:", (r.last_fail_reason or "")[:120])   # 应含竞品信息
print("snap:", default_snapshot_store(c).get(n)) # None(REJECTED 不写快照)
EOF
ls /tank/vault/alphalib/alpha_pnl/$CANARY && ls -d /tank/vault/alphalib/alpha_dump/$CANARY
# ↑ late-stage 失败保留 pnl+dump(KEEP_ARTIFACTS 由 PIPELINE 派生)——两个都应存在
ls /tank/vault/alphalib/pnl_manual/$CANARY 2>/dev/null   # 应无输出(REJECTED 不拷池)
```

### 3d · REJECTED 召回 → 再入库(闭环)

```bash
uv run ops restage $CANARY -y     # REJECTED:自动清 dump+feature + 回收 pnl
uv run ops check -f $CANARY -c config.verify.yaml    # corr=1.01 → ACTIVE
```

预期:restage 输出含"REJECTED 因子将自动清除…"与"已回收/已删除"行;
check 后重新 ACTIVE(顺带覆盖 PV5 checkpoint wipe 与 stale snapshot 自愈在
新代码下的行为)。

### 3e · 清理

```bash
uv run ops rm $CANARY -y
rm -f config.verify.yaml config.verify-pv7.yaml
rm -rf /mnt/storage/dropbox/wbai/$CDATE/$CANARY
rm -f docs/reports/check/check-$CANARY-*.json
# 零残留(全部无输出)+ Total 与基线一致:
ls -d /tank/vault/alphalib/{alpha_src,alpha_dump,staging}/$CANARY 2>/dev/null
ls /tank/vault/alphalib/{alpha_pnl,pnl_manual,pnl_automated}/$CANARY 2>/dev/null
uv run ops list 2>/dev/null | tail -1
```

## 阶段 4 · 150 / 144 滚动升级(轻量)

无窗口要求,逐台做即可(sudoers / `.env` / 144 的 `OPS_ALPHALIB_ROOT` 均已
就位,无需重配):

```bash
cd ~/gsim-ops && git fetch origin claude/remediation-stage-table
git checkout claude/remediation-stage-table && git pull origin claude/remediation-stage-table
git log --oneline -1              # 与 160 一致
uv sync --group dev
uv tool install --editable . --force     # 刷新 ~/.local/bin/ops
uv run pytest -m "not slow" -q    # ⚠ 串行:确认 160 与另一台没在跑测试
ops list 2>/dev/null | tail -1    # Total 一致;150/144 入口是 ops(非 uv run ops)
ops info <任选真因子>
```

144 照旧记录 WAN 耗时(uv sync 记得 `UV_HTTP_TIMEOUT=180`)。

## 阶段 5 · 事件遗留收口(160)

1. **wbai** 清 3 个 tmp 残渣(如尚未清):
   `sudo rm /tank/vault/alphalib/alpha_feature/.Alpha*.npy.tmp`
2. 补 pack(`INCIDENT-144-PACK.md` 遗留;IDC 机器、新代码):

```bash
uv run ops pack --dry-run 2>/dev/null | tail -3    # 先看待补数量(报告记录)
uv run ops pack                                    # 非 force,只补缺失/半对
uv run ops pack --dry-run 2>/dev/null | tail -3    # 复查应为 0 待处理
```

## 阶段 6 · 报告

写入 `docs/remediation/VERIFY-WAVE3-STAGE-TABLE-RESULT.md`(不自行 commit)。
逐步一行 + 重点原文:3b 两次交互的完整输出、3c 的 fail_stage/fail_reason 与
产物三点核对、e2e 汇总行、三机 rev/Total 对照、补 pack 前后数量。全部 ✅ 后:
三机在堆叠顶端,合 main 前置齐备(合并动作另行安排,不在本手册)。
