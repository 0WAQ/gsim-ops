# PG Schema v2 设计(2026-07-12,与用户逐条讨论收敛;已批 —— v2a 已上线,v2b 代码在分支 claude/schema-v2b,迁移待执行)

## Context

doctor v1/v1.1 清完 835 条历史欠账后,用户复审三表 schema,提出七点(冗余
时间戳、NULL 泛滥、check_history 装在 JSONB 里、字段分配、僵尸列、类型选择)。
本文档是讨论收敛的结果:**改什么、为什么、明确不改什么**,批准后分两批实施。

## 一、结构变更(v2b,大件)

### 1.1 `factor_history` 生命周期事件表(check_history JSONB 退役;
### 2026-07-12 与用户二轮讨论升级:从 check 专用表泛化为全操作审计表)

**动机**(用户第 3 点,连带解决第 1/2 点,二轮讨论扩展):一次检测/一次操作
是一条记录,不该是字段里的 JSON 数组元素。泛化的三个增量收益:
- **rm/cancel 不再蒸发历史**:现状 `ops rm` info 级联硬删后,因子曾经存在、
  谁提交、被拒几次、谁 approve、谁删的,全部消失 —— 多人多机库的真实审计缺口;
- **actor 可追溯**:每个事件记操作者;
- `ops status <name>` 详情从"检测历史"升级为完整生命周期时间线。

```sql
CREATE TABLE factor_history (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- 刻意无 FK(用户拍板,2026-07-12):审计要活过 ops rm。
    -- 指向已删因子的事件行属预期;同名重提续写同一 name 的时间线。
    name TEXT NOT NULL,
    op TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL,
    actor TEXT,                        -- submitted_by / OS 用户;'migration' = 回填合成
    -- check 事件专属,其它 op 为 NULL(仅此四列,不上 JSONB)
    started_at TIMESTAMPTZ,
    passed BOOLEAN,                    -- NULL = skip(沿用 CheckRecord 三态)
    failed_stage TEXT,
    fail_reason TEXT,
    CONSTRAINT chk_op CHECK (op IN ('submit', 'overwrite', 'check', 'approve',
                                    'restage', 'cancel', 'rm', 'backfill',
                                    'entered')),
    CONSTRAINT chk_fail_has_stage CHECK (passed IS DISTINCT FROM FALSE
                                         OR failed_stage IS NOT NULL)
);
CREATE INDEX ix_fh_name_at ON factor_history(name, at DESC);
```

(这张表的 id 是真主键 —— 事件无自然键;与三主表"id 死列"情况不同。
`entered` 事件 = check 归档入库 / approve 放行 / backfill 补录三径合流的
"入库"语义,回填合成也用它。)

**发射机制(可靠性关键)**:全部写路径已收口 `FactorRepository`
(register/transition/append_check/delete 单一门面,无旁路)—— 事件在 repo
方法内**同事务**发射(register/transition/delete 加 `op:` 参数,调用方本来
就知道自己是谁;append_check 天然即 check 事件),不依赖十个命令各自记得
写日志,漏记结构上不可能。只记**完成**的事件,不记"check 开始"(避免进程
崩溃留半截事件);CHECKING 瞬时态留在 state.status。

**随之从 factor_state 删除三列**:`rejected_at`、`last_fail_stage`、
`last_fail_reason` —— 全部变为派生:

```sql
-- 最近一次失败(list 的 fail_stage 列 / find(fail_stage=) 下推 / status 展示)
LEFT JOIN LATERAL (
    SELECT failed_stage, fail_reason, at AS rejected_at
    FROM factor_history h
    WHERE h.name = i.name AND h.op = 'check' AND h.passed = FALSE
    ORDER BY h.at DESC LIMIT 1
) lf ON TRUE
```

8k 因子 × 每因子个位数事件,LATERAL 成本可忽略;要更顺手可以建视图
`factor_last_fail`。**"从未被拒"从三个 NULL 变成事件表里没有失败行** ——
存在性表达,正是讨论要的语义。`entered_at` **仍物化保留**(cancel 守卫 /
doctor 判据的热路径,事件表是审计真相、entered_at 是其物化缓存)。

**代码适配面**:`infra/store/pg_store.py`(append_check → INSERT 事件行;
get 的 check_history 改从事件表组装;transition 不再维护 rejected_at/
last_fail_*)、`FactorRecord`(去三字段;check_history 保留为可选加载的内存
形态,CheckRecord dataclass 不变)、`repository`(op: 参数 + 同事务发射)、
`repository.find`(fail_stage 下推改 LATERAL)、`cli/list` fail_stage 列与
`cli/status` 详情(时间线渲染)、json dev/test 后端同步语义、tests、
doctor(新增"指向已删因子的事件行属预期"的认知,不加检查族)。

**迁移(回填深度:尽量重建,用户拍板)**:
1. 存量 8419 行 check_history JSONB 展开成 op='check' 事件(真实数据,
   jsonb_array_elements,核对行数);
2. 从现有时间戳合成生命周期事件,`actor='migration'` 标记:created_at →
   submit、entered_at → entered、rejected_at → (最近 check-fail 已含,不重复);
3. 核对后删 state 三列 + 删 check_history JSONB 列。

### 1.2 `fields` / `tables`:JSONB → `TEXT[]`(用户第二次纠正点)

它们就是字符串列表,JSONB 是三表迁移时从 derived 层原样搬来的偷懒类型。
`TEXT[]` 是诚实类型:psycopg 原生 list 适配(代码反而更简)、GIN(array_ops)
支持 `@>` 包含查询(反查语义不变)、存储略小。

```sql
ALTER TABLE factor_snapshot
    ALTER COLUMN fields TYPE TEXT[] USING
        (SELECT coalesce(array_agg(x), '{}') FROM jsonb_array_elements_text(fields) x),
    ALTER COLUMN tables TYPE TEXT[] USING
        (SELECT coalesce(array_agg(x), '{}') FROM jsonb_array_elements_text(tables) x);
-- GIN 索引重建为 array_ops;下推 SQL:fields @> ARRAY[%s]、
-- tables glob 经 unnest + LIKE(形状同现状)
```

**考虑过并放弃的更重方案**:完全范式化成 `factor_field`/`factor_table` 两张
关联表。放弃理由:这两个列表与快照行**同生共死**(discard 一起删、re-archive
一起重新采集),永远整体读写、元素没有独立生命周期和属性 —— 关联表的收益
(元素级 FK/属性)在这里没有买家,只多两张表和读侧聚合。若未来要做"数据源
字典表"(给 field 挂说明/负责人),届时再升。

## 二、修正与加固(v2a,小件,先行)

- **补执行 `migrate_drop_snapshot_index_cols.sql`**:has_pnl/dump_days 代码侧
  2026-07-06 已删,但删列迁移**从未在生产执行**(用户查活表发现;JOURNAL 无
  执行记录,infra/CLAUDE.md 却是"已删列"的既成口吻 —— 文档失实同批改口)。
  脚本幂等,备份后跑;
- **CHECK 约束**:`ALTER TABLE factor_state ADD CONSTRAINT chk_active_entered
  CHECK (status <> 'active' OR entered_at IS NOT NULL)` —— "不该 NULL 的状态
  下 NULL"在写入口被数据库拒掉(上线前先 SELECT 验存量 8419 行全满足)。
  rejected_at 侧的 CHECK 不做(该列 v2b 删除);
- **scripts/postgres/README.md 重写**:现文档通篇仍是"派生层存储/确认
  factor_derived 建好"(derived 层 2026-07-07 已删,照文档操作会找一张不存在
  的表);01-schema.sql 头注释引用的 `store._init_schema()` 也已不存在
  (现实是 `ops/infra/schema.py::ensure_schemas`);
- **DDL 双真相源 pin 测试**:01-schema.sql 是代码 `_SCHEMA` 的手抄镜像
  (S2 挂账,"两处同改"靠人肉,上次就 bootstrap 出过旧世界 P0-3)。加一个
  测试把两处 DDL 规范化(去注释/空白)后比对,drift 即红 —— 不做代码生成
  那么重,先钉死。

## 三、明确不改(讨论定案,防回潮)

| 项 | 决定 | 理由 |
|---|---|---|
| 三主表 `id SERIAL` | **保留** | 用户原则:不拿业务字段做主键。补充事实:本系统 name 已是全链身份(盘面路径/跨机锁/FK),id 提供的改名保险只覆盖 DB 层,真改名是全盘面迁移 —— 但保留 id 成本≈0,留作未来口子 |
| `snapshot_at` | **保留 + 正名** | 逻辑上 ≡ entered_at,但 entered_at 是可变列(重入库覆盖),快照行自带时间戳才使"旧快照冒充新入库"可检测 —— doctor 抓 662 的证据列。语义正名:不可变行的出生证明 + 对账见证 |
| `delay`/`fields`/`tables` 留 snapshot | **保留 + 正名** | 它们是**代码版本属性**(--overwrite 后随新代码变),不是身份属性;与 metrics 同生共死(discard/attach 同批)。挪 info 会打破身份不可变、并给未入库因子添 NULL。正名:快照 = 入库那一刻的全部事实(代码属性 + 表现指标),不只"指标" |
| info/state 1:1 拆表 | **保留** | 身份不可变 vs 状态机可变,写路径不同;register 已原子化 |
| `entered_at` | **保留** | 不可派生(approve/backfill 直接设,无 check 事件)+ 硬消费方(cancel 守卫/doctor 判据) |
| `submitted_at`/`updated_at`/`created_at` | **保留** | 语义各异:最近提交动作(restage 不刷)/ 行簿记 / 身份首次登记;见时间字段语义表(讨论记录) |

## 四、实施顺序与验证

1. **v2a**(先行,一次执行者窗口):补执行删列迁移 + CHECK(先验存量)+
   README/注释修缮 + pin 测试。除删两列僵尸外零结构变更,风险极低;
2. **v2b**(独立批):factor_history 事件表 + state 删三列 + TEXT[] 类型迁移。
   代码先行(store/repository/cli/tests 全绿 + 对抗评审)→ 迁移脚本
   (BEGIN 事务:建表 → JSONB 展开核对行数 → 删列 → 类型改写 → 索引重建)
   → 执行者短窗口(禁写命令)执行 → 复验(fast suite + doctor 全绿 +
   `ops status <有历史因子>` 详情对照迁移前)。
3. 生产迁移全程 pg_dump 备份先行;两批各自可独立回滚(v2b 事务内失败自动
   回滚,成功后回滚 = 恢复备份)。

## 已拍板记录(2026-07-12 二轮)

- factor_check → **factor_history 全操作审计表**(用户提议,采纳);
- **历史活过 rm**(无 FK;指向已删因子的事件属预期);
- **回填尽量重建**(check_history 真实展开 + 时间戳合成,actor='migration')。

## 遗留讨论 —— v2c 批收口(2026-07-12)

- ~~check_history 内存形态剥离~~ **已做**(v2c ④):FactorRecord 纯状态机
  快照,全史按需 `store.checks(name)`;json 后端 history 合成 check 事件,
  status 时间线两后端统一;
- ~~discovery_method CHECK~~ **已做**(v2c,migrate_v2c_smalls.sql);
- **数据源字典表:维持缓议**(定案)—— 触发条件是"要给 field 挂说明/负责人
  等元素级属性"的真需求出现;在那之前 TEXT[] 够用,不预建;
- ~~status 时间线展示~~ 已随 v2b 落地。
另收口两处 \d 观察(v2c):status 重复索引删除、factor_state_new_* 命名归一。
