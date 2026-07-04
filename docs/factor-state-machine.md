# 因子状态机设计

## 状态定义

| 状态 | 业务含义 |
|---|---|
| SUBMITTED | 等待审查(唯一待审入口) |
| ACTIVE | 通过验证,生产因子 |
| REJECTED | 验证未通过,不合格 |

- CHECKING 是 check 运行中的瞬态(代码中为独立状态值);reconcile 已下线,崩在半路的因子由下次 `ops check` 按 staging 目录扫到并重跑覆盖
- 没有 DELETED 状态:一个因子要么存在(active/rejected/未来 decay 等),要么被 `ops rm` 彻底删除而不存在。删除不是一种状态。
- DECAYING / RETIRED 暂未实现

## 命令体系

| 命令 | 语义 | 前置条件 | 来源 | version |
|---|---|---|---|---|
| `ops submit` | 新因子入系统 (默认跳过已入库) | 因子名不存在于 state | dropbox | = 1 |
| `ops submit --overwrite` | 已有因子提交新代码 | 因子名已存在于 state | dropbox | += 1 |
| `ops restage` | 原代码不变,召回 staging 待重跑 check | ACTIVE 或 REJECTED | alpha_src | 不变 |
| `ops approve` | 多样性豁免:为数据覆盖放行 correlation-rejected 因子 | REJECTED 且 last_fail_stage=correlation | — | 不变 |
| `ops cancel` | 撤回未入库的 SUBMITTED 因子 (删 staging + 硬删 state record) | SUBMITTED (或 CHECKING + `--force`) | — | — |
| `ops clear` | 清 staging 孤儿目录 (state 无 record) | — | — | — |
| `ops rm` | 彻底删除因子(src/pnl/dump/feature + state + derived,不可逆) | 任意状态 | — | — |

## 状态转移图

```
ops submit (新因子)     ops submit --overwrite (新代码, version+=1)
    │                       │
    ▼                       ▼
         ┌───────────┐
         │ SUBMITTED │ ←── ops restage (ACTIVE)
         └───────────┘ ←── ops restage (REJECTED)
              │
         ops check
              │
         ┌────┴────┐
         ▼         ▼
    ┌────────┐  ┌──────────┐
    │ ACTIVE │  │ REJECTED │
    └────────┘  └──────────┘
                     │
                ops approve (多样性豁免, correlation 失败)
                     ▼
                ┌────────┐
                │ ACTIVE │
                └────────┘
```

## 各状态下因子数据分布

alpha_src 是所有因子的 src 归档,不区分状态(ACTIVE/REJECTED 都在里面,状态靠 state 区分)。

> recycle 已退役(2026-07):它曾是"给研究员看的 REJECTED 副本",但研究员 work tree 在 dropbox 够不着 root-owned 的本地 recycle,且其内容(src / 失败阶段 / 原因)在 alpha_src + state 里都有权威副本,故整体下线。

| 状态 | alpha_src | alpha_pnl | alpha_dump | alpha_feature |
|---|---|---|---|---|
| SUBMITTED | 无(在 staging) | 无 | 无 | 无 |
| ACTIVE | 有 | 有 | 有 | 有(需 ops pack) |
| REJECTED(checkbias/checkpoint 失败) | 有 | 无 | 无 | 无 |
| REJECTED(compliance/correlation 失败) | 有 | 有 | 有 | 有 |

## 转移时数据产物规则

| 转移 | alpha_src | alpha_pnl | alpha_dump | alpha_feature |
|---|---|---|---|---|
| submit (新因子→SUBMITTED) | 无(在 staging) | 无 | 无 | 无 |
| check 通过 (→ACTIVE) | staging 移入 | 新产出 | 新产出 | 无(需 ops pack 单独产出) |
| check 失败 checkbias/checkpoint (→REJECTED) | 保留 src | 不保留 | 不保留 | 无 |
| check 失败 compliance/correlation (→REJECTED) | 保留 src | 保留 | 保留 | 生成并保留 |
| restage (ACTIVE→SUBMITTED) | 保留(拷贝到 staging) | 保留 | 保留 | 保留 |
| restage (REJECTED→SUBMITTED) | 保留(拷贝到 staging) | 清掉 | 清掉 | 清掉 |
| approve (REJECTED→ACTIVE, 仅 correlation 失败) | 保留 | 保留 | 保留 | 保留 |
| submit --overwrite (新代码, version+=1) | 新代码到 staging,旧 src 保留 | 保留 | 保留 | 保留 |

规则说明:
- ACTIVE restage 保留产物:不暂停生产
- REJECTED restage 清掉产物:无生产顾虑,check 会重新产出
- submit --overwrite 保留旧产物:作为对比基准
- REJECTED 后两阶段失败保留完整产物:数据完整,有分析参考价值
- REJECTED 前两阶段失败不保留:短期数据不完整,无意义
- approve 是多样性豁免(为数据覆盖放行,非质量):仅适用于 correlation 失败,产物已完整,翻状态即可,无需重跑

## 版本控制

当前阶段:state record 加 `version` 计数器,check_history 每条标注对应 version。
代码只保留当前版本(不做历史快照)。

未来方向:加代码快照(git 管理 alpha_src 或 .versions/ 归档),类似 MVCC。

## 业务背景

- 研究员没有 alpha_src 的读写权限(recycle 曾作为给研究员看的副本,已退役)
- 目前没有真正的生产环境,ACTIVE 只是"通过验证入库"
- 有些手写因子质量高但被机器因子挤占(correlation 被拒),其完整产物有分析价值
- 数据产物可再生性:src 不可再生,pnl 代价高尽量保留,dump/feature 可再生
