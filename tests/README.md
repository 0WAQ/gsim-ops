# ops 测试

因子检测 pipeline 的路径/循环处理 + state/derived 存储层单测。重点覆盖 `ops check`
的控制流(5 个结局分支、on_reject 分产物、扫描/自愈/锁),不测 gsim 回测算法本身。

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

表(factor_state / factor_derived / derived_meta)由 store 首次连接时的幂等
`_init_schema` 自建,无需手工灌 schema。

## 隔离模型

- **PG**:独立库 `ops_test`,每个测试用唯一 `library_id`(`test_<uuid>`)分区,测后按
  library_id 删三表行(用例并行安全)。绝不碰生产 `ops` 库。
- **文件**:每个测试的数据路径(alpha_src/dump/pnl/feature/staging + pnl/checkpoint)
  全部相对 pytest `tmp_path`,测完随 tmp 自动清。
- 连接/密码可用环境变量覆盖:`OPS_TEST_PG_HOST/PORT/USER/PASSWORD`;缺省从
  `scripts/postgres/.env` 的 `OPS_PG_PASSWORD` 读。

## 测试组织

| 文件 | 内容 | 需 PG |
|---|---|---|
| `test_pure.py` | `metric_get`/`sort_key` 逐键语义;Json{State,Derived}Store CRUD + get_all 下推 | 否 |
| `test_state_store_pg.py` | PostgresStateStore:put/get round-trip、时间戳 tz 不偏 8h、transition、append_check、delete、list、library_id 隔离 | 是 |
| `test_derived_store_pg.py` | PostgresDerivedStore:四组独立 upsert、get_all 各下推参、delete、meta | 是 |
| `test_check_routing.py` | **主体**:pipeline 5 结局(pass/retry/reject-late/reject-early/skip/crash)+ pnl 分流 + short-circuit + 派生局部失败不阻断 | 是 |
| `test_check_scan.py` | `_scan_factors` 过滤、`_ensure_record` 补建/不覆盖、CHECKING 自愈、FactorLocked → locked | 是 |

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

## 手动端到端冒烟(`slow`,不随默认 suite)

真跑一个逻辑极简因子的完整 `ops check`(真 gsim,~1-2min),验证 CLI→Config→gsim→store
全链路装配。依赖真实 cc 数据就绪,不适合无人值守 CI,故标 `slow` 手动跑:

1. 造一个隔离 config(参考 `conftest.py:test_config` 的路径重定位 + `ops_test` 后端)。
2. 往 dropbox 放一个简单因子 → `uv run ops -c <config> submit -u <you> -s <date>`。
3. `uv run ops -c <config> check -f <factor>`,观察走完 6 stage 入库。
4. `uv run ops -c <config> list` 能查到、`info` 显示 metrics/datasources。
5. `uv run ops -c <config> rm <factor> -y` 清干净。

## teardown 测试库(需要时)

```bash
uv run python -c "import psycopg; pw=next(l.split('=',1)[1].strip() for l in open('scripts/postgres/.env') if l.startswith('OPS_PG_PASSWORD=')); c=psycopg.connect(f'host=10.9.100.160 port=15432 dbname=postgres user=ops password={pw}', autocommit=True); c.execute('DROP DATABASE ops_test'); c.close()"
```

平时不用 drop —— 每个测试自己按 library_id 清行,库可长期留着复用。
