# Schema v3:词汇正名 + 快照语义变更(2026-07-13,与用户逐条讨论收敛;二轮定稿)

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
| `snapshot_at` | 测得见证(= 最近一次测得时刻,第二节新语义;对账列) |
| `history.at` | 事件发生时刻(以上的真相源) |

**不变量**:`created_at <= submitted_at`(首提相等;backfill 存量 submitted_at
为 NULL 除外)。落地:CLAUDE.md 收录词汇表,list/status 等文档改口
("库内因子"→"在册因子")。

## 二、快照语义变更:factor_snapshot = 最近一次 check 测得的表现(用户提案,二轮采纳)

**动机**:测得值是 check 运行的事实,入库决定只是消费它。一轮方案(check
事件加 7 窄列)被用户方案取代 —— **v2b 建成审计表后,快照不再需要兼职
"入库见证"**(entered 事件 + entered_at 全权负责),绑着入库语义只是历史
惯性。改快照语义的四个优势:读路径零改动(list/approve/find 已 JOIN
snapshot,被拒因子的行自然出现)、消灭"合法无快照的 ACTIVE"特例(approve
放行的因子有被拒那次的测得值)、fields/tables/delay 一起覆盖(fail 时 py
还在 staging,datasources 照采)、v2b"check 仅四列"定案不用翻。

**新语义**:`factor_snapshot` = **测得快照**(最近一次 check 测得的表现)。
- `snapshot_at` = 测得时刻(再正名:对账见证 → 测得见证;列名照旧不改);
- 仍然**只由 check 写**、每次测量原子替换(复用 stale 自愈的 delete+insert)、
  永无离线重算 —— v2 反对的"refresh 可变刷新"依然被拒之门外,不可变精神
  保留为"每行不可变,替换即新测量";
- 写入时机:pass → archive 段照旧;**correlation 失败 → 也写**(simsummary
  已跑、CorrResult 已有 bcorr,零额外计算;datasources/delay 同批采集);
  checkbias/checkpoint 等早期失败没跑出指标,不写(NULL 诚实);
- compliance 失败:long_backtest 已完成、pnl 在,可低成本补跑 simsummary
  落快照 —— 随本批一起做(推翻一轮"不做"预案,写入点同 correlation)。

**代码适配面**:
- `repository.attach_snapshot`:entered_at 硬闸删除,`snapshot_at = 测得
  时刻`(由调用方传,= 该次 check 事件的 at);
- `Factor.__post_init__` 软校验改:snapshot_at 不再对 entered_at,改对
  "最近一次带指标的 check 事件"(或降级为不校验,doctor 全权);
- `check.py`:on_reject 对 correlation/compliance 增加快照采集落库
  (_persist_derived 泛化,pass/fail 共用);
- **doctor snapshot-stale 族判据重定义**:illegal(非 ACTIVE 却有快照)
  整个作废;新判据 = `snapshot_at == 最近一次测得指标的 check 事件的 at`
  (与 factor_history 交叉对账,比旧判据更强);--fix 语义随判据重写;
- list/approve/status:**零渲染改动**(数据源自然回来)。

**迁移**:给存量被拒因子回填快照行 —— 738 条 correlation fail_reason
解析(`key=value`,四种历史格式已验证)+ meta.json 补 delay/datasources;
compliance 22 条可选人工补跑;snapshot_at = 对应 check 事件的 at。

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
| check 事件加指标列(一轮方案) | 不做 | 被快照语义变更取代;v2b"仅四列"定案得以保留。代价:丢"历次测量时间序列",fail_reason 文本兜底 |
| entered_at 改名 | 不做 | 代价(代码+迁移+习惯)> 收益;保留 + 正名(同 snapshot_at 先例) |
| REJECTED 写快照 = 入库? | 否 | 写快照 ≠ 入库;入库判据只看 status/entered_at/entered 事件,词汇表为准 |
| 数据源字典表 | 维持缓议 | v2c 定案不变 |

## 六、已拍板记录(2026-07-13 二轮)

- **快照语义变更为"测得快照"**(用户提案,取代一轮的 check 事件加列);
- 词汇表全部术语(在册/已归档/入库/在库/已入库 + 入库时刻定义);
- created_at := submitted_at 数据修正(81 违反者,成因不追);
- legacy 老因子档案清理独立成批("找个时间一起解决")。

仍待拍板:`ops list` 混排时加 status 列(可选顺手项,实施时一并定)。

## 七、实施顺序

1. 代码批:attach_snapshot 闸重写 / check.py 失败路径采集 / Factor 软校验 /
   doctor 族判据重定义 + 解析器 + 测试 + 对抗评审(重点镜头:doctor 新旧
   判据切换期的误报面);
2. 迁移脚本(回填 738+22 快照行,幂等设计,沙盘实测);零 DDL 变更
   (snapshot 表结构不动,纯 INSERT),预计免禁写窗口;
3. 执行者复验(验收标准 = `ops list -s rejected` 出指标 + doctor 全绿);
4. 词汇表进 CLAUDE.md 随代码批同 PR。
