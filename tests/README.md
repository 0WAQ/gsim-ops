# ops 测试

因子检测 pipeline 的路径/循环处理 + state 存储层单测。重点覆盖 `ops check`
的控制流(6 个结局分支、on_reject 分产物、扫描/自愈/锁),不测 gsim 回测算法本身。

## 快速开始

```bash
uv sync --group dev                 # 装 pytest (dev group)
uv run pytest -m "not slow"         # 跑全部自动化单测 (纯函数 + PG)
```

PG 测试需要一个测试库 `ops_test`。三种起法,任选:

- **执行机(160/150/144/170)**:生产 PG 实例上的独立库
  `10.9.100.160:15432/ops_test`(缺省,零配置);
- **本地 docker**(I2,2026-07-11):`docker compose -f docker-compose.test.yml
  up -d`,然后 `export OPS_TEST_PG_HOST=127.0.0.1 OPS_TEST_PG_PORT=15433
  OPS_TEST_PG_PASSWORD=ops_test`(端口刻意 15433,避免与生产 15432 混淆);
- **CI**:`.github/workflows/ci.yml` 起 postgres service,pg 组常跑;
  service 可达性有独立断言步 —— 挂了是红,不再 skip 假绿。

库不可达时,pg 组自动 skip(不红),纯函数测试仍跑。

### 建 ops_test 库(160 实例首次;docker/CI 自带无需此步)

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

三表(factor_info / factor_state / factor_snapshot)由
`ops/infra/schema.py::ensure_schemas` 按 FK 依赖序幂等自建(`pg_conninfo` fixture
显式调用;DDL 已滚出 store `__init__`,2026-07-09),无需手工灌 schema。

## 隔离模型(I2,2026-07-11 重建)

- **PG:per-session 随机 schema**。每个 pytest session 在 ops_test 里
  `CREATE SCHEMA t_<hex>`,conninfo 带 `options=-csearch_path=<schema>`
  (search_path 只列 schema 本身,不含 public —— 漏建的表响亮失败),三表建在
  schema 内,session 结束 `DROP SCHEMA CASCADE`。**并行的 pytest 进程各有各的
  schema,互不干扰** —— 原"多机/多进程跑测试须人肉串行"的纪律作废。测试之间
  沿用 wipe(schema 内删 factor_info 级联;仍有 current_database()=='ops_test'
  双保险)。绝不碰生产 `ops` 库。
- **advisory lock:命名空间注入**。锁是库级作用域,schema 隔离挡不住 ——
  测试 config 注入 `state.lock_namespace = <schema 名>`(`lock.py` 的**仅测试**
  注入口;生产锁键固定 `ops:factor_lock`,S18 教训:锁键随 config 漂移 =
  跨机互斥无声失效)。
- **FK 前置**:`factor_state.name` 外键引 `factor_info` —— direct-store 测试
  put 前用 `seed_info` fixture 种父行(镜像生产:register 是 info+state 原子
  双表写,不存在无父行的 state);service 级测试用 `seed_factor`(走 Config)。
- **文件**:每个测试的数据路径(alpha_src/dump/pnl/feature/staging + pnl/checkpoint)
  全部相对 pytest `tmp_path`,测完随 tmp 自动清。
- 连接/密码可用环境变量覆盖:`OPS_TEST_PG_HOST/PORT/USER/PASSWORD`;缺省从
  `scripts/postgres/.env` 的 `OPS_PG_PASSWORD` 读。

## 测试组织

| 文件 | 内容 | 需 PG |
|---|---|---|
| `test_pure.py` | JsonStateStore CRUD + list 过滤解析/glob→LIKE 下推 + S16 写命令声明集(`mark_write` 注册派生,`test_write_command_declarations_match_registry`) | 否 |
| `test_batch.py` | `_batch.py` 批量骨架(apply_locked 四种结局路由、失败不阻断)+ `transition(expect=)` CAS | 否 |
| `test_check_routing_json.py` | pipeline 5 个非 pass 结局(retry/reject-late/reject-early/skip/crash)+ stage 归因盖章 + prepare 失败响亮化 + short-circuit + `_ensure_record` 无 seed 补建(json 后端,CI 常跑)| 否 |
| `test_repository.py` | `FactorRepository`:产物面 `ArtifactScope` 两面语义 + 搬运三件套 archive/recall/unstage(归档搬运与分流 / 身份发散拒绝 / 召回往返与守卫 / unstage 幂等,2026-07-10)+ json 降级(register 只写 state / find 拒绝 / discard no-op,无 PG 组);register 原子双表写、find 单条 JOIN 因子集(含 `include_submitted` "任何记录"语义)/过滤/快照拼装、attach_snapshot 强制 entered_at + stale 自愈、delete 级联(PG 组) | json 组否 / PG 组是 |
| `test_state_store_pg.py` | PostgresStateStore:put/get round-trip、时间戳 tz 不偏 8h、transition、append_check、delete、list(I2 重建后常跑;put 前 `seed_info` 种 FK 父行) | 是 |
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
→ tmp)+ PG 复用上层 conftest 的 per-session schema fixture(pytest fixture 按目录
级联;I2 起 e2e 不再往 ops_test 残留测试行)+ check 报告目录重定向到 tmp(否则污染
`docs/reports/check/`)。gsim/cc/PG 任一不可达 → skip 整组。假因子模板见 conftest `_TEMPLATES`。

**为什么放宽 pass 路径的门槛**:真实 reversal 因子实测 shrp~1.2/tvr~78 达不到生产门槛
(ret≥10/shrp>2/tvr≤60),会 correlation-reject。E2E pass 路径要验的是 **pipeline 路由到
ACTIVE**,不是生产门槛本身(门槛校验由 correlation_checker 逻辑 + 单测覆盖),故 `relax_thresholds`
fixture 放宽 + `corr_threshold=1.01` 恒走低相关直接通过分支。

## teardown 测试库(需要时)

```bash
uv run python -c "import psycopg; pw=next(l.split('=',1)[1].strip() for l in open('scripts/postgres/.env') if l.startswith('OPS_PG_PASSWORD=')); c=psycopg.connect(f'host=10.9.100.160 port=15432 dbname=postgres user=ops password={pw}', autocommit=True); c.execute('DROP DATABASE ops_test'); c.close()"
```

平时不用 drop:per-session schema 自建自清,ops_test 常态是空库。pytest 进程
被 SIGKILL 时 teardown 不跑,可能残留孤儿 schema(无害)——
`SELECT nspname FROM pg_namespace WHERE nspname LIKE 't_%'` 查出后逐个
`DROP SCHEMA ... CASCADE` 即可。
