# ops 测试

因子检测 pipeline 的路径/循环处理 + state 存储层单测。重点覆盖 `ops check`
的控制流(6 个结局分支、on_reject 分产物、扫描/自愈/锁),不测 gsim 回测算法本身。

## 快速开始

```bash
uv sync --group dev                 # 装 pytest (dev group)
uv run pytest -m "not slow"         # 跑全部自动化单测 (纯函数 + PG)
```

PG 测试需要一个独立测试库 `ops_test`(同生产 160 docker 实例,host 10.9.100.160:15432)。
库不可达时,pg 组自动 skip(不红),纯函数测试仍跑。

### 建 ops_test 库(首次)

```bash
uv run python - <<'PY'
import psycopg
pw = next(l.split('=',1)[1].strip() for l in open('scripts/postgres/.env')
          if l.startswith('OPS_PG_PASSWORD='))
c = psycopg.connect(f'host=10.9.100.160 port=15432 dbname=postgres user=ops password={pw}',
                    autocommit=True)
c.execute('CREATE DATABASE ops_test OWNER ops'); c.close()
PY
```

三表(factor_info / factor_state / factor_snapshot)由 store 首次连接时的幂等
`_init_schema` 自建,无需手工灌 schema。

## 隔离模型

- **PG**:独立库 `ops_test`,绝不碰生产 `ops` 库。**隔离模型待重建**(full-review I2):
  原"每测试唯一 library_id 分区 + 测后按 library_id 删行"已随三表去 library_id 失效
  (teardown 的 DELETE 实为 no-op),direct-store fixture(`state_store`)显式 skip;
  待改 per-test schema 隔离(CREATE SCHEMA + search_path,put 前需 factor_info 父行)。
- **文件**:每个测试的数据路径(alpha_src/dump/pnl/feature/staging + pnl/checkpoint)
  全部相对 pytest `tmp_path`,测完随 tmp 自动清。
- 连接/密码可用环境变量覆盖:`OPS_TEST_PG_HOST/PORT/USER/PASSWORD`;缺省从
  `scripts/postgres/.env` 的 `OPS_PG_PASSWORD` 读。

## 测试组织

| 文件 | 内容 | 需 PG |
|---|---|---|
| `test_pure.py` | JsonStateStore CRUD | 否 |
| `test_batch.py` | `_batch.py` 批量骨架(apply_locked 四种结局路由、失败不阻断)+ `transition(expect=)` CAS | 否 |
| `test_check_routing_json.py` | pipeline 5 个非 pass 结局(retry/reject-late/reject-early/skip/crash)+ stage 归因盖章 + prepare 失败响亮化 + short-circuit(json 后端,CI 常跑)| 否 |
| `test_state_store_pg.py` | PostgresStateStore:put/get round-trip、时间戳 tz 不偏 8h、transition、append_check、delete、list(**当前整组 skip**:state_store fixture 待重建,I2) | 是 |
| `test_check_routing.py` | pipeline 6 结局含 **pass→archive**(snapshot 落库、pnl 分流)+ 派生局部失败不阻断 | 是 |
| `test_check_scan.py` | `_scan_factors` 过滤、`_ensure_record` 补建/不覆盖、CHECKING 自愈、FactorLocked → locked | 是 |
| `test_submit.py` | submit:新因子 version=1、已入库跳过、`--overwrite` version+1、文件数/syntax/discovery_method 校验失败回滚 staging | 是 |
| `test_restage.py` | restage:ACTIVE/REJECTED 召回 → SUBMITTED、REJECTED 清 pnl、`--purge` 清 dump/feature、不支持状态/源缺失跳过 | 是 |
| `test_lifecycle_cmds.py` | cancel(SUBMITTED/`--force` CHECKING、产物守卫拒绝/批量 skip)、approve(仅 correlation-rejected)、clear(仅孤儿)、rm(硬删全落点含 staging + factor_info 级联 state/snapshot) | 是 |

### 可测性接缝(依赖注入)

`CheckerPipeline.__init__` 接受 `checkers: dict[str, Checker] | None`;测试注入一组
**fake checker**(`conftest.py:fake_checkers`),在指定 stage 抛
`CheckFail`/`CheckSkip`/`Exception` 或全 pass,再同进程直接调 `run_one`/`_run_one_locked`
(绕开 ProcessPoolExecutor),断言 state 转移 + 文件落点 + check_history。生产不传
`checkers` → 照旧 new 真的 gsim-backed checker,行为不变。

pass 路径 archive 会调 `Runner.run_simsummary`(真 gsim)—— 由 `fake_metrics` fixture
monkeypatch 成返回假 Metrics。

## 常用命令

```bash
uv run pytest -m "not slow"          # 全自动 suite (默认)
uv run pytest -m pg                   # 只跑需 PG 的
uv run pytest tests/test_pure.py      # 只跑纯函数 (无需 PG)
uv run pytest -k routing -v           # 只跑路由测试
```

## 端到端测试(`tests/e2e/`,标 `slow` + `e2e`,默认不跑)

真实 gsim + 真实 cc_2025 数据,**构造假因子确定性触发每条 pipeline 路径**,验证从
`submit` → `check`(真回测)→ 最终 state + 文件落点 的完整生产流程。

```bash
uv run pytest -m e2e -v                # 跑全部 6 条路径 (~7min, 160 实测)
uv run pytest tests/e2e/ -m e2e -k pass   # 只跑 pass 路径
```

覆盖的 6 条路径(`tests/e2e/test_e2e_pipeline.py`):

| 假因子 | 触发点 | 期望结局 |
|---|---|---|
| 正常 reversal | 全过(e2e config 放宽业绩门槛)| → ACTIVE + rm 清干净 |
| `generate` 抛异常 | validate gsim 崩 | → SUBMITTED (retry) |
| delay=1 访问当日数据 | checkbias firewall 拦前视 | → REJECTED |
| `np.random` 非确定输出 | checkpoint 断点 md5 不一致 | → REJECTED |
| 只选 10 只股票 | compliance 持股数不足 | → REJECTED |
| 噪声输出 | correlation 业绩不达标 | → REJECTED |

**隔离**(`tests/e2e/conftest.py`):真实 gsim/cc + 隔离可写落点(dropbox/alpha_src/pnl/staging
→ tmp)+ PG(ops_test;其按 library_id 删行的 teardown 同样已随三表去 library_id 失效,
测试行会残留,归 I2 一并重建)+ check 报告目录重定向到 tmp(否则污染
`docs/reports/check/`)。gsim/cc/PG 任一不可达 → skip 整组。假因子模板见 conftest `_TEMPLATES`。

**为什么放宽 pass 路径的门槛**:真实 reversal 因子实测 shrp~1.2/tvr~78 达不到生产门槛
(ret≥10/shrp>2/tvr≤60),会 correlation-reject。E2E pass 路径要验的是 **pipeline 路由到
ACTIVE**,不是生产门槛本身(门槛校验由 correlation_checker 逻辑 + 单测覆盖),故 `relax_thresholds`
fixture 放宽 + `corr_threshold=1.01` 恒走低相关直接通过分支。

## teardown 测试库(需要时)

```bash
uv run python -c "import psycopg; pw=next(l.split('=',1)[1].strip() for l in open('scripts/postgres/.env') if l.startswith('OPS_PG_PASSWORD=')); c=psycopg.connect(f'host=10.9.100.160 port=15432 dbname=postgres user=ops password={pw}', autocommit=True); c.execute('DROP DATABASE ops_test'); c.close()"
```

平时不用 drop;但隔离重建(I2)前测试行会残留在 ops_test,脏了就 drop 重建
(三表由 store 首次连接自建)。
