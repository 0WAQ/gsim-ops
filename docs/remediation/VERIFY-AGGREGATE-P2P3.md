# 增量验证 · Factor 聚合阶段 2/3 执行手册(160 单机)

**目标**:在 160 上对 `claude/factor-aggregate-phase3`(**已包含**
`claude/factor-aggregate-phase2`,验它即覆盖两分支)做 PG 组 + e2e + 金丝雀
行为环路验证。全绿后两个前置同时解除:①阶段 3 第二批(archive/recall 收编)
开工;②两分支合 main 前置齐备。

**增量面**(main → phase3,三批):
- **FactorPaths + 身份守卫**:盘面布局收编 `ops/core/paths.py`;check 对
  "staging 目录名 ≠ XML @id"在任何状态写入前整单拒绝,to_lib 兜底断言。
- **阶段 2**:`Factor` 聚合(core/factor.py)+ `FactorRepository`
  (find 单条三表 JOIN 取代 query_factors;register 原子双表写;
  attach_snapshot 强制 snapshot_at=entered_at;purge_artifacts 两面模型;
  DDL 滚出 store 构造,归 `infra/schema.py::ensure_schemas`)。
- **阶段 3 第一批**:cli 接缝 `ops/cli/common.py`,**7/7 import 契约
  enforcing**(ratchet 基建删除);status/cancel/pack 塌缩到 repo;cancel 删除
  改 info 级联一步;find 的 state 边 LEFT JOIN(info 孤儿在"任何记录"语义下
  现形)。

**兼容性判定(无需停写窗口)**:PG 三表结构、锁键命名空间、状态转移语义
main ↔ phase3 零变化(纯代码组织重构)。150/144 留在 main 期间三机互斥照常
成立。纪律照旧:
1. 共享 `ops_test` ⇒ 跑测试期间确认其它机器没在跑测试(串行);
2. 金丝雀验证期间其它机器不对金丝雀名字做写操作。

**红线**(沿用既有):写操作只允许针对金丝雀 `AlphaWbaiCanary001` /
`AlphaWbaiCanary002`;实际输出与预期不符**立即停止报告**,不自行修复;
不动 redis/sentinel;不直接 SQL 写生产 `ops` 库(只读 SELECT 核对允许)。

---

## 阶段 0 · 部署 + 静态门禁(160)

```bash
cd ~/gsim-ops && git status -sb        # 干净才继续
git fetch origin claude/factor-aggregate-phase3
git checkout claude/factor-aggregate-phase3 && git pull origin claude/factor-aggregate-phase3
git log --oneline -3                   # 记录 rev(报告用;tip 应为 34e7aee 或其后)
uv sync --group dev
uv run ruff check ops tests            # 预期 All checks passed
uv run pyright ops                     # 预期 0 errors(首跑联网拉 node,失败记录不阻塞)
uv run lint-imports                    # 预期 Contracts: 7 kept, 0 broken ← 本次新验收
ls scripts/ci/ contracts-baseline.toml 2>&1   # 预期都不存在(ratchet 已删)
```

## 阶段 1 · fast suite 含 PG 组(160,ops_test 可达 ⇒ PG 组真跑)

```bash
uv run pytest -m "not slow" -q         # 预期 0 failed;记录 passed/skipped 数
```

关注新用例(报告点名其结果):
- `tests/test_repository.py` —— PG 组 7 个:register 原子双表 / find 因子集与
  过滤 / include_submitted / **info 孤儿现形**(test_pg_find_surfaces_info_orphans)/
  attach_snapshot 强制 entered_at + stale 自愈 / attach 无 entered_at 拒绝 /
  delete 级联;
- `tests/test_check_routing_json.py` —— `test_identity_divergence_refused_before_state`
  (身份守卫)、`test_ensure_record_works_without_seed`、
  `test_preamble_crash_emits_done`、`test_watch_futures_unblocks_on_all_pending`;
- `tests/test_lifecycle_cmds.py` PG 组(cancel 守卫 / rm staging,上一轮挂账的
  160 复跑在此一并完成);
- `tests/test_factor_paths.py`(布局契约,无 I/O)。

任何 fail:停止报告(附完整输出)。

## 阶段 2 · e2e(真 gsim + cc)

```bash
uv run pytest -m e2e -q                # ~85s;预期全 passed
```

本次增量动了 archive 前后的编排(_ensure_record→register、
_persist_derived→attach_snapshot、to_lib 身份断言),e2e 的逐 stage 确定性
失败因子正是对这些的行为级回归。任何 fail:停止报告。

## 阶段 3 · 只读冒烟(生产 config;list/status 是本次重写的读路径)

```bash
uv run ops list 2>/dev/null | tail -1        # Total 与基线一致(repo.find 取代 query_factors 的生产实证)
uv run ops list -u wbai | head
uv run ops list --filter-by "ret>30,shrp>2" 2>/dev/null | tail -1   # 下推路径
uv run ops list --filter-by "ret=>30" 2>&1 | head -2   # 预期报错含 Unknown operator 与 did you mean '>='
uv run ops status | tail -3                  # 列表模式(repo.find include_submitted)
uv run ops status <任选真因子>               # 单因子(repo.get,含 check_history)
uv run ops info <同一因子>                   # snapshot/物理状态显示正常
```

Total 或任一因子行与升级前不一致:停止报告并附对照。

## 阶段 4 · 金丝雀行为环路(160,生产库)

psql 连接按本机习惯(`psql -h localhost -p 15432 -U ops -d ops`,或 docker exec 进容器)。
夹具与 VERIFY-WAVE3 阶段 3 完全一致:双 config + dropbox 金丝雀重建 snippet
**照抄 VERIFY-PV7.md 阶段 0**(勿手抄模板;重建前 `rm -rf` 旧目录)。

```bash
export CANARY=AlphaWbaiCanary001
export CDATE=$(date +%Y%m%d)
```

前提检查:`ls /tank/vault/alphalib/pnl_manual/AlphaWbaiReversal`(孪生真因子
在池,4c 靠它触发 REJECTED;不在则 4c 预期改直接 ACTIVE 并在报告注明)。

### 4a · 入库(register 原子 + attach 强制 + 身份守卫不误伤)

```bash
uv run ops submit -u wbai -s $CDATE -f $CANARY
uv run ops check -f $CANARY -c config.verify.yaml
```

预期:7 stage 全过 → ACTIVE(正常因子经 normalize 后 目录名==@id,身份守卫
**不**触发 —— 这是守卫无误伤的验证点)。核对(只读 SELECT):

```bash
psql -h localhost -p 15432 -U ops -d ops -c \
  "SELECT i.name, i.author, s.status, s.entered_at, n.snapshot_at, (s.entered_at = n.snapshot_at) AS stamped
   FROM factor_info i JOIN factor_state s ON s.name=i.name LEFT JOIN factor_snapshot n ON n.name=i.name
   WHERE i.name = '$CANARY';"
# 预期:三表各一行,status=active,stamped=t(attach_snapshot 强制生效)
ls /tank/vault/alphalib/pnl_manual/$CANARY     # 池副本存在
```

### 4b · check 期间连接占用(顺带,P0 后遗症复核)

4a 的 check 运行中另开终端:

```bash
psql -h localhost -p 15432 -U ops -d ops -c \
  "SELECT count(*) FROM pg_stat_activity WHERE datname='ops';"
# 预期个位数~十几(get_pool 去重后),远低于 100
```

### 4c · 生产阈值 re-check → REJECTED(归因 + 产物策略,回归 WAVE3 3c)

```bash
uv run ops restage $CANARY -y                            # ACTIVE 召回(pnl+池回收)
uv run ops check -f $CANARY -c config.verify-pv7.yaml    # corr=0.7 → correlation 拒
uv run ops status $CANARY                                # status=rejected, last_fail=correlation
ls /tank/vault/alphalib/alpha_pnl/$CANARY && ls -d /tank/vault/alphalib/alpha_dump/$CANARY  # late-stage 保留
ls /tank/vault/alphalib/pnl_manual/$CANARY 2>/dev/null   # 应无输出(REJECTED 不拷池)
```

### 4d · approve 语义 API(阶段 2 迁移,新验证点)

```bash
uv run ops approve $CANARY          # 不带 -y:确认交互应显示 author=wbai(来自 repo.find/get)
# 答 y
uv run ops status $CANARY           # active;check_history 末条 fail_reason=approved
```

预期:correlation-rejected 被放行(`correlation_rejected()` 谓词);approve
不写快照 —— `ops info $CANARY` 的 Metrics 应显示"未入库或入库时未生成"一类
占位(**合法无快照的 ACTIVE**,不是数据异常)。

### 4e · REJECTED 闭环 + rm 全落点(回归 WAVE3 3d/3e)

```bash
uv run ops restage $CANARY -y                          # ACTIVE 召回
uv run ops check -f $CANARY -c config.verify.yaml      # corr=1.01 → 重新 ACTIVE(stale snapshot 自愈路径)
uv run ops rm $CANARY -y
# 零残留(全部无输出):
ls -d /tank/vault/alphalib/{alpha_src,alpha_dump,staging}/$CANARY 2>/dev/null
ls /tank/vault/alphalib/{alpha_pnl,pnl_manual,pnl_automated}/$CANARY 2>/dev/null
psql -h localhost -p 15432 -U ops -d ops -c \
  "SELECT 'info',count(*) FROM factor_info WHERE name='$CANARY'
   UNION ALL SELECT 'state',count(*) FROM factor_state WHERE name='$CANARY'
   UNION ALL SELECT 'snap',count(*) FROM factor_snapshot WHERE name='$CANARY';"
# 预期三行都是 0(repo.delete 级联)
```

### 4f · cancel 级联一步(阶段 3 新行为,用二号金丝雀,不跑 check)

```bash
export CANARY2=AlphaWbaiCanary002
# dropbox 重建 snippet 同 4 前置,把名字换成 $CANARY2
uv run ops submit -u wbai -s $CDATE -f $CANARY2
uv run ops status $CANARY2                    # submitted
uv run ops cancel $CANARY2                    # 不带 -y:确认交互应显示 author=wbai
# 答 y;预期输出:"已删除 staging/..." + "已删除 factor_info + 级联 state record ..."
psql -h localhost -p 15432 -U ops -d ops -c \
  "SELECT 'info',count(*) FROM factor_info WHERE name='$CANARY2'
   UNION ALL SELECT 'state',count(*) FROM factor_state WHERE name='$CANARY2';"
# 预期两行都是 0(⚠ 本条是级联新语义的核心断言:info 不得剩孤儿行)
ls -d /tank/vault/alphalib/staging/$CANARY2 2>/dev/null   # 无输出
```

### 4g · 清理

```bash
rm -f config.verify.yaml config.verify-pv7.yaml
rm -rf /mnt/storage/dropbox/wbai/$CDATE/{$CANARY,$CANARY2}
rm -f docs/reports/check/check-$CANARY*-*.json
uv run ops list 2>/dev/null | tail -1          # Total 回到基线
```

## 阶段 5 · 150/144

**本次跳过**:phase2/3 未合 main,不滚三机;混版本兼容性已在头部判定
(表结构/锁键/语义零变化)。合 main 后随下一窗口滚存。

## 阶段 6 · 报告

写入 `docs/remediation/VERIFY-AGGREGATE-P2P3-RESULT.md`,commit + push 到
`claude/factor-aggregate-phase3` 分支。逐步一行 + 重点**原文**(教训:报告必须
贴命令原始输出,不贴结论):阶段 1 的 passed/skipped 汇总行与点名用例结果、
e2e 汇总行、阶段 3 的 Total 对照与 `=>` 报错原文、4a 的 stamped=t 行、4d 的
approve 交互原文、4e/4f 的 PG 零行核对原文、160 rev。任何一步不符:停在那一步,
报告写到哪算哪。
