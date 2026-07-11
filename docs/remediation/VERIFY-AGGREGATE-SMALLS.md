# 增量验证 · 小件收官批执行手册(160 单机)

**目标**:在 160 上对 `claude/factor-aggregate-smalls`(单 commit `3c275f7`,
基于 main `2507f40`)做 PG 组 + e2e + 轻量金丝雀验证。全绿后合 main 前置齐备。

**增量面**(main → smalls,四个小件):
- **S8 metric 注册表**:`core/metrics.py::SNAPSHOT_METRICS` 成为 metric 键集 +
  取值语义(bcorr=abs)唯一定义;snapshot pg_store 的 SQL 下推表达式、list 的
  内存兜底、CLI `--sort-by` choices 三方派生(原 `_METRIC_EXPR`/`_metric_get`/
  手抄 choices 三份拷贝删除)。**list 的过滤/排序读路径是本批重写面。**
- **AlphaMetadata 去 I/O**:alpha_dump 工作区扫描迁
  `services/check/checker/dumpscan.py`(v2npy_files/last_v2npy_file);
  **compliance / checkpoint 两个 checker 改走它** —— e2e + 金丝雀是行为证据。
  判定语义与原实现一致(last_v2npy_file 只看最新月份);唯一有意变更:扫描
  I/O 错误不再静默吞(冒泡 unexpected 臂 → revert SUBMITTED,终态等价)。
- **results 空壳清理**:checkpoint.py 删除(CheckpointChecker.check 返回 None,
  流水线本就丢弃)、Status/Results 空壳类删除。纯删除,无行为面。
- **created_at 两径收敛**:info store 读走 ts_out(与 repo.find 对齐),写缺省
  `ts_in(created_at or now_iso())`。**金丝雀入库后 SELECT 核对时区正确性。**

**兼容性判定(无需停写窗口)**:PG 三表结构、锁键、状态转移语义 main ↔ smalls
零变化。created_at 变更只在写路径的 Python 侧(列仍 TIMESTAMPTZ),混版本安全。
150/144 留在 main 期间三机互斥照常成立。纪律照旧:
1. 共享 `ops_test` ⇒ 跑测试期间确认其它机器没在跑测试(串行);
2. 金丝雀验证期间其它机器不对金丝雀名字做写操作。

**红线**(沿用既有):写操作只允许针对金丝雀 `AlphaWbaiCanary001`;实际输出与
预期不符**立即停止报告**,不自行修复;不动 redis/sentinel;不直接 SQL 写生产
`ops` 库(只读 SELECT 核对允许)。

---

## 阶段 0 · 部署 + 静态门禁(160)

```bash
cd ~/gsim-ops && git status -sb        # 干净才继续
git fetch origin claude/factor-aggregate-smalls
git checkout claude/factor-aggregate-smalls && git pull origin claude/factor-aggregate-smalls
git log --oneline -2                   # 记录 rev(tip 应为 3c275f7)
uv sync --group dev
uv run ruff check ops tests            # 预期 All checks passed
uv run pyright                         # 预期 0 errors
uv run lint-imports                    # 预期 Contracts: 7 kept, 0 broken
uv run python -c "from ops.core.alpha.results import checkpoint" 2>&1 | tail -1   # 预期 ModuleNotFoundError(空壳已删)
```

## 阶段 1 · fast suite 含 PG 组(160,ops_test 可达 ⇒ PG 组真跑)

```bash
uv run pytest -m "not slow" -q         # 预期 108 passed / 0 failed(基线 106 + 本批新增 2)
```

关注新用例(报告点名其结果):
- `tests/test_pure.py::test_metric_registry_is_single_source`(S8:键集决策 +
  bcorr abs 语义 SQL/内存逐位一致 + CLI choices 同源);
- `tests/test_pure.py::test_dumpscan_layout_and_order`(dumpscan:时序/非日期
  目录忽略/最新月份无 v2 → None **不回退**)。

passed 数与 108 不符:先核对 collected 总数(可能因环境 skip 波动),0 failed
是硬线。任何 fail:停止报告(附完整输出)。

## 阶段 2 · e2e(真 gsim + cc)

```bash
uv run pytest -m e2e -q                # ~85s;预期 6 passed
```

本批把 compliance(v2npy_files)/ checkpoint(last_v2npy_file)的文件发现换成
dumpscan,e2e 的逐 stage 确定性失败因子会真跑到这两个 stage —— 是 dumpscan 在
真 gsim dump 布局上的行为级回归。任何 fail:停止报告。

## 阶段 3 · 只读冒烟(生产 config;list 的 metric 过滤/排序是本批重写面)

```bash
uv run ops list 2>/dev/null | tail -1                      # Total 与基线一致(上轮 8252,若期间有正常入库/删除按当前基线)
uv run ops list --sort-by bcorr | head -5                  # 正常出表,bcorr 列降序(按绝对值)
uv run ops list --filter-by "ret>30,shrp>2" 2>/dev/null | tail -1   # 记录 Total
uv run ops list --filter-by "bcorr>0.3" --format json 2>/dev/null | python3 -c "import json,sys; print(len(json.load(sys.stdin)))"
uv run ops list --filter-by "ret=>30" 2>&1 | head -2       # 预期报错含 Unknown operator 与 did you mean '>='
uv run ops list --sort-by delay 2>&1 | tail -2             # 预期 argparse 报错 invalid choice(choices 从注册表派生,delay 不在)
```

**bcorr 过滤交叉核对**(注册表 SQL/内存两半与 PG 真值三方对齐,只读):

```bash
psql -h localhost -p 15432 -U ops -d ops -c \
  "SELECT count(*) FROM factor_snapshot n JOIN factor_state s ON s.name=n.name
   WHERE s.status != 'submitted' AND abs(n.max_bcorr) > 0.3;"
# 预期:与上面 bcorr>0.3 的 json 长度一致
```

不一致:停止报告并附两边原文。

## 阶段 4 · 金丝雀行为环路(160,生产库;本批轻量版:一次全通 + created_at 核对)

psql 连接按本机习惯(`psql -h localhost -p 15432 -U ops -d ops`,或 docker exec
进容器)。夹具与 P2P3 手册相同:双 config + dropbox 金丝雀重建 snippet **照抄
VERIFY-PV7.md 阶段 0**(勿手抄模板;重建前 `rm -rf` 旧目录)。

```bash
export CANARY=AlphaWbaiCanary001
export CDATE=$(date +%Y%m%d)
```

### 4a · 入库全通(compliance/checkpoint 走 dumpscan 的生产实证)

```bash
uv run ops submit -u wbai -s $CDATE -f $CANARY
uv run ops check -f $CANARY -c config.verify.yaml
uv run ops status $CANARY              # 预期 active
```

预期:7 stage 全过 → ACTIVE。**checkpoint stage 通过本身就是
last_v2npy_file 的行为证据**(断点续跑 md5 比对要求它找对同一份 dump);
compliance 通过是 v2npy_files 时序窗口的证据。若 checkpoint 意外
SKIP/REJECTED:停止报告,附 check 报告原文。

### 4b · created_at 时区/格式核对(本批写路径变更;只读 SELECT)

```bash
psql -h localhost -p 15432 -U ops -d ops -c \
  "SELECT created_at, now(), (now() - created_at) < interval '1 hour' AS fresh
   FROM factor_info WHERE name = '$CANARY';"
# 预期:fresh = t(created_at 是刚才的本地时刻,不是偏 8h 的 UTC 误写)
uv run ops info $CANARY | head -15     # 显示正常(identity/metrics/snapshot_at)
```

fresh = f:停止报告(created_at 写路径回归,附该行原文)。

### 4c · 清理

```bash
uv run ops rm $CANARY -y
psql -h localhost -p 15432 -U ops -d ops -c \
  "SELECT 'info',count(*) FROM factor_info WHERE name='$CANARY'
   UNION ALL SELECT 'state',count(*) FROM factor_state WHERE name='$CANARY'
   UNION ALL SELECT 'snap',count(*) FROM factor_snapshot WHERE name='$CANARY';"
# 预期三行都是 0
rm -f config.verify.yaml config.verify-pv7.yaml
rm -rf /mnt/storage/dropbox/wbai/$CDATE/$CANARY
rm -f docs/reports/check/check-$CANARY-*.json
uv run ops list 2>/dev/null | tail -1          # Total 回到基线
```

## 阶段 5 · 150/144

**本次跳过**:smalls 未合 main,不滚三机;混版本兼容性已在头部判定。
合 main 后随下一窗口滚存。

## 阶段 6 · 报告

写入 `docs/remediation/VERIFY-AGGREGATE-SMALLS-RESULT.md`,commit + push 到
`claude/factor-aggregate-smalls` 分支。逐步一行 + 重点**原文**(纪律:报告必须
贴命令原始输出,不贴结论):阶段 1 的 passed/skipped 汇总行与两个点名新用例
结果、e2e 汇总行、阶段 3 的 Total / `=>` 报错原文 / `--sort-by delay` 报错原文 /
bcorr 交叉核对两边数字、4a 的 status 输出与 check 汇总行、4b 的 SELECT 原文
(含 fresh 列)、4c 的三表零行原文、160 rev。任何一步不符:停在那一步,
报告写到哪算哪。
