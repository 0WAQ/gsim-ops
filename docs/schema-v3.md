# Schema v3:词汇正名 + 检测结果安家(2026-07-13,与用户逐条讨论收敛;待批)

## Context(起点:zxu 被拒因子在 list 里整行空)

`ops list -s rejected` 全部指标列 `—`、delay `?`。两轮生产取证(2026-07-13)
定格因果:**指标列七列全部只读 factor_snapshot,而快照只随"入库"写**——
被拒因子归档了产物(src/pnl 在盘上)、登记了档案(PG 有行),但指标没有
随任何一步落库。测得的值其实都在:correlation checker 内部跑了 simsummary,
结果只进了 fail_reason 文本(全库 738/738 覆盖,格式自 2026-04-27 稳定)。

根因一句话:**"一次检测测得的表现"是独立事实族,07-06 三表重构把它和
"入库时快照"当成了同一个东西,于是被拒因子的测得结果无家可归。**
连带暴露:围绕"库"的词汇已经歧义("在库/入库/因子库"各挂在不同所指上)。

## 一、词汇表(已拍板,2026-07-13)

**"因子库"自此专指成员集合**(combo 消费、bcorr 对比池的范围);存储面叫
alphalib / 归档。四个正交谓词:

| 术语 | 定义 | 实现锚点 |
|---|---|---|
| **在册** | 有档案记录、目录可见(含被拒) | `status != 'submitted'` = `ops list` 因子集 |
| **已归档** | 盘面产物落 alphalib | src/pnl/dump 物理位置;ACTIVE 与晚期 REJECTED 都发生 |
| **入库**(动作) | 从不在库变为在库的那一刻 | `transition(ACTIVE)`;history `entered` 事件 |
| **在库**(状态) | 因子库成员 = ACTIVE | `status='active'` |
| **已入库**(完成时) | 至少发生过一次入库,离库不清除 | `entered_at` 非空(cancel 守卫的真正语义) |

**入库时刻 = 因子(最近一次)变为在库的时刻**(用户定名);重入库覆盖,
历次记录在 history `entered` 事件序列。时间戳全家对照:

| 时间戳 | 词汇 |
|---|---|
| `created_at` | 在册登记时刻(首提时 == submitted_at,逐字符同值) |
| `submitted_at` | 最近一次提交动作(--overwrite 刷新;restage 不刷) |
| `entered_at` | 最近一次入库时刻(已入库的物化判据;列名保留不改,正名即可) |
| `updated_at` | 行簿记,无业务语义 |
| `snapshot_at` | 入库见证(≡ 当次 entered_at,对账列) |
| `history.at` | 事件发生时刻(以上的真相源) |

**不变量**:`created_at <= submitted_at`(首提相等;backfill 存量 submitted_at
为 NULL 除外)。落地:CLAUDE.md 收录词汇表,list/status 等文档改口
("库内因子"→"在册因子")。

## 二、检测结果安家:check 事件携带测得值(待批,核心变更)

**动机**:测得值是 check 运行的事实,入库决定只是消费它。现状它寄居在
fail_reason 文本里(correlation)或随入库固化进快照(pass),被拒因子的
评审(approve 多样性豁免)拿不到结构化数据。

**DDL**:factor_history 的 check 行加**窄类型列**(全部 nullable;非 check
op 与没跑出指标的失败恒 NULL):

```sql
ALTER TABLE factor_history
    ADD COLUMN ret DOUBLE PRECISION, ADD COLUMN shrp DOUBLE PRECISION,
    ADD COLUMN mdd DOUBLE PRECISION, ADD COLUMN tvr DOUBLE PRECISION,
    ADD COLUMN fitness DOUBLE PRECISION, ADD COLUMN bcorr DOUBLE PRECISION,
    ADD COLUMN delay INT;
```

- **这推翻 v2b"check 专属仅四列、不上 JSONB"定案** —— 当时反对的是无结构
  JSONB;窄类型列 + 新证据(无正主事实族、780 个被拒因子的评审需求)构成
  翻案理由。定案表照 v2 惯例更新并记翻案依据;
- **写侧**:CheckRecord 增补对应字段;correlation checker 把 simsummary 结果
  结构化填入(pass 与 fail 都填),fail_reason 回归纯"原因"(违规说明);
  pass 路径 archive 段复用同一份结果(simsummary 只跑一次);
- **读侧**:`ops list` 对无快照的 REJECTED 行,指标/delay 从 last_fail
  LATERAL 直取(**该 JOIN 本来就在,零额外查询零文本解析**),淡色渲染
  区分"入检值 ≠ 入库快照";approve 计划表同源展示 ret/shrp/bcorr;
- **factor_snapshot 角色不变**:仍是入库时事实的固化(= 入库那次 check 的
  测得值拷贝),读路径不动;
- **迁移**:存量 738 条 correlation fail_reason 解析回填(`key=value`
  解析器,四种历史格式已验证覆盖);6988 条 pass 事件可选自快照回填
  (推荐做,事实族不裂两半);checkbias/checkpoint 的 20 条本就无值,NULL 诚实。

## 三、数据修正(已拍板)

- `created_at := submitted_at`,对违反 `created_at > submitted_at` 的行
  (现 81 个 fguo,07-10 凌晨成因不明的批量写;用户拍板:不追,直接改);
- 迁移合成 submit 事件的 at 同步修正(81 条 timeline 倒挂随上条消解)。

## 四、独立排期(不在本批)

- **legacy 老因子档案清理批**(用户点名"找个时间一起解决"):
  created_at/submitted_at 全量对账、`discovery_method='backfill'` 存量正名、
  各类 NULL 盘点(submitted_at/author='unknown' 等);
- **`ops backfill` 护栏/退役**:bootstrap 已完成,正常流程不再产生补录;
  现状留着反而是 src 孤儿复活风险(doctor v1 警告过)。

## 五、明确不做(定案,防回潮)

| 项 | 决定 | 理由 |
|---|---|---|
| REJECTED 写 factor_snapshot | 不做 | 快照 = 入库见证,语义不掺水;测得值的家在 check 事件 |
| compliance 失败补算指标 | 不做 | pnl 在盘上可人工重算;22 个存量不值得开新采集路径 |
| entered_at 改名 | 不做 | 代价(代码+迁移+习惯)> 收益;保留 + 正名(同 snapshot_at 先例) |
| 数据源字典表 | 维持缓议 | v2c 定案不变 |

## 六、待拍板点

1. **翻 v2b"仅四列"定案**,check 事件加 7 窄列(推荐:翻);
2. **pass 事件也携带/回填测得值**(推荐:是,事实族不裂两半);
3. `ops list` 混排时加 status 列(可选,顺手项)。

## 七、实施顺序

1. 代码批:CheckRecord/DDL/checker 落值/list·approve 渲染/解析器 + 测试
   + 对抗评审;
2. 迁移脚本(加列 + 回填,幂等设计,沙盘实测);加列为低风险 DDL,预计
   免禁写窗口(照 v2c 先例,风险分级即流程分级);
3. 执行者窗口 + 复验(list -s rejected 出指标为验收标准);
4. 词汇表进 CLAUDE.md 随代码批同 PR。
