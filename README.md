# ops

Gsim alpha 因子的验证、回测与生命周期管理 CLI。

## 安装

```bash
uv sync
uv run ops --help
```

## 命令

| 命令 | 说明 |
|---|---|
| `submit`  | 从 dropbox 提交因子到 staging |
| `check`   | 对 staging 因子跑 7 阶段验证流水线 |
| `run`     | 在因子库中跑回测 |
| `list`    | 列出因子库中的因子(过滤/排序/指标/数据源) |
| `info`    | 显示单个因子详情 |
| `status`  | 查询因子生命周期状态 |
| `pack`    | 聚合 alpha_dump 为 alpha_feature 矩阵 |
| `restage` | 召回已入库因子到 staging 重跑 check |
| `approve` | 多样性豁免:放行 correlation-rejected 因子 |
| `cancel`  | 撤回未入库的 submitted 因子 |
| `clear`   | 清理 staging 孤儿目录(state 无 record) |
| `rm`      | 彻底删除因子(不可逆) |
| `combo`   | combo 端到端代测(predict + backtest) |
| `setup`   | 拉平本机 alphalib 部署 |
| `doctor`  | 盘 ↔ Postgres 数据对账(只读;`--fix` 修复) |

## 因子生命周期

```
dropbox/{user}/{date}/AlphaXxx/     (QR 所有,只读)
    │  submit
    ▼
staging/AlphaXxx/ + meta.json       (SUBMITTED)
    │  check(7 阶段)
    ├─ pass ─► alpha_src/AlphaXxx/   (ACTIVE)
    └─ fail ─► alpha_src/AlphaXxx/   (REJECTED)
```

生命周期状态存 Postgres(`factor_info` / `factor_state` / `factor_snapshot` /
`factor_history` 四表);`meta.json` 随因子目录走,是其身份证。

### 验证流水线(ops check)

`validate → checkbias → checkpoint → long_backtest → compliance → correlation → archive`

| 阶段 | 作用 |
|---|---|
| validate | 最小回测,验证代码/配置能跑 |
| checkbias | 短回测 + AST 注入 DataFirewall,检前视偏差 |
| checkpoint | 断点续跑稳定性 |
| long_backtest | 全历史回测(2015–2025) |
| compliance | 仓位约束(个股 ≤5%,多/空 ≥50,总 ≥100) |
| correlation | 业绩门槛(ret/shrp/tvr)+ bcorr <0.7(否则须打败竞品) |
| archive | 测得快照落库,搬入因子库 |

失败路由:validate / long_backtest 失败回 SUBMITTED 留 staging 重试;
其余阶段失败置 REJECTED。详见 `docs/gsim-factor-validation.md`。

### 示例

```bash
ops submit -u wbai -s 20260401                 # 提交某日全部因子
ops check                                      # 检测 staging 全部因子
ops list --sort-by shrp -n 10                  # 按夏普排序取前 10
ops list --filter-by "ret>30,tables=ashare*"   # 按指标/数据源过滤
ops status AlphaWbaiReversal                   # 查单个因子状态
ops doctor                                     # 盘 ↔ PG 对账
```

## 文档

- `docs/architecture.md` — **项目架构总览**(分层/生命周期/存储/拓扑,先读这个)
- `CLAUDE.md` — 命令、SSOT、拓扑、技术债(维护者参考)
- `docs/` — gsim 框架 + 因子开发(研究员)、schema/设计文档
- `.claude/plans.md` — 路线图
