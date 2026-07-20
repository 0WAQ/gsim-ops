# 产线批引擎(produce v4):静态组 + 腿静音 + 代际累积

> 2026-07-18 收敛定稿。前置:`factor-produce-v3.md`(归档即生产态 + per-factor
> 薄驱动,PR #23)。本文只换 **run 层引擎**:逐因子进程 → 分组批进程,
> 合约(dump/pnl 布局、dataset 落点、ACTIVE 集 SSOT、归档即生产态)零改动。
> 全部设计锚在 170 六轮 scratch 实证上(实验记录见 §7)。

## 1. 动机与形态

per-factor 模式每因子付一次数据装载(全库日更 ~30 分钟、7.5k 进程、fd 压力)。
gsim `PortfolioType` 支持 unbounded sibling `<Alpha>`:一个进程装一次数据、
算 N 个因子,**腿级 dump 与腿级 pnl 均原生支持**(dump:腿上
`dumpAlphaFile/Dir`,按 Alpha id 建子目录;pnl:Stats 按腿落
`<pnlDir>/<AlphaId>` 单文件,742 腿现役 combo 产物 + 微实验双证)——
批产物与 per-factor 产物**位级一致**(充分 warmup 下)。

批 XML 是运行时组装的临时产物(从各因子生产态 XML 拼装),不是每因子改写,
不违背"归档即生产态、零 gen"。

## 2. 四条不变量(实证驱动,别"优化"掉)

- **I1 manifest 指纹闸门**:gsim checkpoint **按腿序号反序列化,不按名字**——
  删中间腿后续腿静默读错 slot,**不报错产出错误数据**(实验④b',全案最危险
  发现)。每组 checkpoint 旁存 manifest(组 id + 有序成员表 + 冻结 .py 摘要 +
  批 XML 哈希),跑前重组比对,不一致即拒跑强制重建 —— 静默错位结构上不可能。
- **I2 组不可变**:铸组时把成员 .py **冻结拷贝进组目录**,批 XML 引用冻结副本
  —— 组对 alpha_src 的后续变化(--overwrite/restage 重入库)免疫,不存在
  "新代码 + 旧 positional 状态"的带病组合。成员与顺序永不增删
  (加腿 = EOFError 崩,删腿 = 静默污染,实验④)。
- **I3 腿静音吸收 churn**:`dumpAlphaFile` 翻 false 不改 checkpoint 布局,
  续跑位级一致(实验②'):离库 → 静音(计算照跑 ~0.6s/腿/日的浪费,换免重建);
  重入库 → 静音 + 因子转 per-factor 跑新代码;原码复活(approve)→ 解除静音。
  **任何一天都不存在"必须立刻重建组"的事件。**
- **I4 双引擎并存**:batch = 日更舰队;per-factor(v3 现状)= 新人 / 隔离区 /
  外科手术(--force / --enddate / 金丝雀)。互为回退。

## 3. 分组策略

- **切组**:按 author,**200~300 腿/组**。上限由**全史重建**定,不由日更定:
  重建 RSS 随窗口长度涨(200 腿全史峰值 264 GB、2h40m,实验⑤;36 天窗口才
  22 MB/腿),300 腿 ≈ 400 GB,1 TB 机上最多 2~3 组并行重建;700 腿/组贴
  天花板,禁。
- **代际累积**:新入库因子走 per-factor 引擎攒着;攒到 ~200 → 冻结为新代
  Gen-N+1,**周末 scratch 铸 checkpoint**后原子换入。铸组 = 唯一的全史重建
  场景,计划内、不打扰生产(历史 dump 本就位级相同,铸组产物落 scratch 丢弃,
  只取 checkpoint + manifest;铸完前新人继续 per-factor 日更)。
- **清淤**:组静音比例 > 50% 时列入季度重铸窗口(纯省计算,不影响正确性)。
- **Data 模块并集**:组内各腿的 `<Data>` 声明按 id 去重合并;同 id 不同属性
  的冲突因子踢出该组(留 per-factor 或归入同签名组)。同 author 因子数据
  模块高度同质,冲突预期罕见。

## 4. 运行编排(produce v4 日更)

```
ops produce(盘前)
 ├─ sync:ACTIVE ⇔ {组 manifest ∪ per-factor 名单}
 │    新 ACTIVE → per-factor 名单;离库 → 所在组腿静音(改组 XML + manifest 版本);
 │    重入库(归档时 checkpoint 已废)→ 组内静音 + 转 per-factor
 ├─ 批引擎:逐组 [指纹校验 → pre-check(.py 存在+py_compile)→ run_cp.py 续跑]
 │    组适度并行(日更 RSS≈基线级,30 组预计 10~25 分钟)
 ├─ per-factor 引擎:新人/隔离区(v3 现路径)
 └─ 汇总 + 退出码(任一组/因子失败 → 1)
```

**故障处理**:组炸 → stderr 定位肇事腿 → 打入隔离名单(转 per-factor)→
该组静音肇事腿后重跑当日(静音不废 checkpoint,I3);定位不了 → 整组当日
失败告警,per-factor 兜底可手动补。

**pnl**:批 Stats `dumpPnl=true, pnlDir=<pnl_root>` —— 腿级 pnl 文件名 =
Alpha id,与 per-factor 引擎产物同构,D6 无损。sibling 模式无容器合成 pnl,
无需清理。

## 5. 测试纪律(用户定,2026-07-18)

实验一律 **≤5 因子 × ≤3 个月窗口**;规模结论从生产存量产物推算
(200 腿全史标定已做过一次,不再重复)。

## 6. 实施批次(v4,PR #23 合入后新分支)

1. `core/batchxml.py`:批 XML 组装器(sibling 腿拼装 / Data 并集去重 /
   冲突检出)+ manifest 类型(有序成员 + 摘要 + 指纹)——纯函数 + 单测。
2. 组仓储:组目录布局(冻结 .py 副本 / 批 XML / manifest)+ 铸组(scratch
   mint + 原子换入)+ 静音操作 —— `services/produce/groups.py`。
3. produce 接批引擎:sync 扩展(组对账 + 静音联动)+ 双引擎调度 + 指纹闸门。
4. 铸组/清淤 CLI(`ops produce --mint` / `--regroup`,确认制)。
5. 170 实机:单组(≤200 腿)影子铸组 + 与 per-factor 引擎产物 byte 对拍 →
   分代滚动接管舰队。

## 7. 实证记录索引(全部 170 scratch,2026-07-18)

| # | 实验 | 结论 |
|---|---|---|
| ① | sibling/容器 dump 语义 | 腿级 dump 原生;容器级另有合成 |
| ② | 5 腿批 vs 单进程 byte-diff | 充分 warmup 位级一致;差异纯 warmup 不足 |
| ③ | 腿级故障 | 无隔离,任一腿错 = 整组死(加载期与运行期皆然) |
| ④ | 36 天窗口规模标定 | ~0.56s/腿边际;RSS 22MB/腿(短窗口) |
| ⑤ | batch + checkpoint 续跑 | 组级 archive.bin 正常,续跑 = 一遍跑(位级);~1.1MB/腿状态 |
| ④' | 成员增删 × checkpoint | 加腿崩(EOFError);删尾腿侥幸;**删中间腿静默污染**(I1 依据) |
| ⑥ | 200 腿全史重建 | 2h40m / 峰值 264GB / ckpt 187MB(分组上限依据) |
| ①' | pnl 腿级实证 | pnl 按 Alpha id 逐腿落单文件,与 alpha_pnl 约定吻合 |
| ②' | 静音续跑 | dumpAlphaFile 翻转不改 checkpoint 布局,续跑位级一致(I3 依据) |
