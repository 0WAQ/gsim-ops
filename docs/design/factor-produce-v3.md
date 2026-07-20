# 因子增量生产 v3.1:归档即生产态 + `ops produce` 薄驱动

> 2026-07-18(v3.1:评审后重构 —— gen 层删除,生产化前移到归档时)。
> 收敛过程与实证:`docs/remediation/DISCOVER-PRODUCE-PROD(-RESULT).md`(生产形态
> 摸底)+ `AUDIT-DUMP-CONSISTENCY(-RESULT).md`(dataset 一致性对账)。
> 取代分支上 v1 实现的过时部分,处置清单见 §10。

## 1. 需求与定位

ACTIVE 因子的 alpha_dump 日增生产是实盘 combo 的上游(mode0 经 AlphaLoad 逐因子
读 per-date dump)。现状:cchang 以私有脚本临时代管(7530 因子/全量 ~13 分钟/
手动触发/无告警),与 ops 生命周期脱钩 —— 在库未投产 443(hwang/xmf/ybai/sli
整批作者不在产线),在产不在库 555。本方案把产线正式收进 ops。

**核心设计原则(v3.1)**:**因子入库后就直接适配生产线** —— alpha_src 里的归档
XML 本身就是生产态,拿来即用;不存在"从归档 XML 再生成 prod.xml"的补丁层(gen)。
产线只是"驱动 + 生命周期同步"。库内因子不供人直跑,单独运行的合法出口是未来的
`ops export`(§11 TODO)。

## 2. 决策台账(已拍板,2026-07-17/18)

| # | 决策 | 理由 |
|---|---|---|
| D1 | 机制 = per-factor 持久生产 XML + `run_cp.py` checkpoint 续跑,enddate 交给 gsim 解析 | 生产范式现役验证;ops 侧就绪判定/缺失推导是重复造轮(v1 退场) |
| D2 | 窗口 = startdate 20110101 + backdays 256 | 实盘已运行在此行为上,一致性优先(保真方案 20150101+原 backdays 被否) |
| D3 | enddate = config 键(TODAY/TODAY-1/钉死日),缺省 **TODAY**(2026-07-18 修正) | **生产跑在 T 日盘前**:delay 机制使 T 日仓位由 T-1 数据算出,T 日 dump 正是当天实盘目标仓位 —— 生产必须到 TODAY。TODAY-1 是"只看回测表现不生产"的语义(pnl 统计只到 T-1)。字面量使 XML 长期复用 |
| D4 | 落点 = 沿用现有 dataset:`/nvme125/alpha_dump` + `/nvme125/alpha_pnl` + `/nvme125/checkpoint` | 资产/checkpoint 延续、combo 零改动;审计证实 dataset 可信(§8);且结构上分开"check 验证 dump"(alphalib sidecar)与"产线生产 dump"两个事实族 |
| D5 | 杂质净化单轨化(v3.1):规则在**归档时**执行一次 + 存量一次性迁移;gen 层不存在 | alpha_src 即生产态,残疾治在源头,不留永久补丁层 |
| D6 | dumpPnl = true,pnlDir = 产线 pnl 根(与 ops alpha_pnl 隔离) | 现役强制开;check 快照字节源不受污染 |
| D7 | produce 无状态:不写 factor_history,dump/checkpoint 文件即记录 | 每日全库 = 海量噪音事件 |
| D8 | 数据根 = `/nvme125/datasvc/data/cc_all` | cc_2025 在 170 无权限;审计证实与 160 cc_2025 交集窗口零漂移 |
| D9 | **归档 XML = 生产态,拆雷退役**(v3.1) | "输出指 /tmp 防手动砸库"被"库内因子不直跑 + ops export 出口(TODO)"取代;checkpoint 续跑范式下误跑 = 幂等日增,风险实质缩小(唯一残险:未加 factor_lock 的并发竞写) |
| D10 | **backdays 定格时机 = 归档**(v3.1):首检用作者原值把关设计意图,归档时 SET 256;此后 restage 复检对象即生产态 | "入库 = 进入生产世界"的边界推论;复检验证的行为 = 实际生产行为 |
| D11 | **@module 用跨机稳定路径** `/mnt/storage/alphalib/alpha_src/...`(v3.1) | 现状写本机挂载点绝对路径(160 归档的在 170 无效)—— 拿来即用的前提;/mnt/storage/alphalib 四机皆有、指本机实际挂载点 |

## 3. 总体架构

```
入库(check archive 段):归档 XML 经"生产化改写"(§4)落 alpha_src —— 即生产态
存量:一次性迁移脚本把现存 ~8419 个归档 XML 原地生产化(§9 步骤 2)

ops produce = sync(ACTIVE 集 ⇔ checkpoint 目录集)+ run(逐因子直接跑
              alpha_src/<因子>/Config.*.xml)+ 汇总。零 gen、零 src 副本。

盘面(路径写死在归档 XML 内,均为 170 本机;check 不读这些字段):
  alphalib/alpha_src/<因子>/           # 生产态 XML + 代码(JFS 共享;SSOT 不变)
  /nvme125/checkpoint/<因子>/archive.bin   # 持久;损坏 = 删掉全段重跑
  /nvme125/alpha_dump/<因子>/YYYY/MM/      # gsim 直写,尾部 ~5+N 天每日重写
  /nvme125/alpha_pnl/<因子>                # 单文件
```

与 check 的边界:check 每 stage 跑前 `prepare_for_initial`/`prepare_for_*` 全量
重写窗口/数据根/输出路径,**归档 XML 的字段值对 check 透明** —— 生产化不影响
验证主链路。同一因子在 sidecar(check 产)与 dataset(产线产)的 dump 是两个
事实族,不对账不互搬。`__pycache__`:runner subprocess 置
`PYTHONDONTWRITEBYTECODE=1`,root 不往 JFS alpha_src 写缓存。

## 4. 归档生产化改写(三张声明式规则表,SET → REPLACE → SUFFIX_STRIP)

执行时机:① `repo.archive` 归档段(新入库/重入库,一次);② 存量迁移脚本
(一次性)。规则纯函数放 `ops/core/prodxml.py`(infra→core 合法依赖),
参数(标 ⚙)来自 config `produce:` 块。

**SET(定位节点,强制设值)**:
1. Constants/@niodatapath = ⚙ cc_all 数据根(D8)
2. Constants/@backdays = ⚙ 256(D2/D10;>256 自 20110101 起早于数据起始,gsim 重建只读缓存崩)
3. Constants/@checkpointDir = ⚙ `<checkpoint_root>/<因子>/`
4. Constants/@checkpointDays = 5
5. Universe/@startdate = ⚙ 20110101(D2)
6. Universe/@enddate = ⚙ PROD_ENDDATE(D3,缺省 TODAY —— 盘前产 T 日仓位)
7. Stats/@pnlDir = ⚙ 产线 pnl 根;@dumpPnl = true(D6)
8. Portfolio/Alpha/@dumpAlphaFile = true;@dumpAlphaDir = ⚙ 产线 dump 根
9. Alpha/@module = `/mnt/storage/alphalib/alpha_src/<因子>/<原 module basename>`
   (D11;**文件名沿用原 module 的 basename,不用目录名拼** —— 目录名 ≠ .py 名
   的存量不少,拼错 → gsim 回退 gsim.alpha 找属性 → AttributeError;原 module
   缺失才回退 `<目录名>.py`)

**REPLACE(扫属性值,替换子串/前缀;顺序①→②→③)**:
- ① 存量杂质归一:`niodatapath: /datasvc/data/cc_2025 → /datasvc/data/cc_all`
  (前缀)、`niodatapath: /cache/data → /datasvc/data`(前缀)、
  `niodatapath|dataPath: /home/fguo/data_local → /datasvc/data/cc_all/cn_equity_feature_5min`(整值)
- ② `*`(所有属性):`/datasvc → ⚙ /nvme125/datasvc`(前缀迁移通吃;rawdata
  路径散落在 dataPath/rawpricePath/industryPath/ST 等多属性,精确列举会漏)。
  **★ Universe 例外:跳过 `<Universe>` 标签** —— secID/holidaysfile/calendarfile
  指向 gsim 侧基础数据,必须保持 `/datasvc`;加前缀 → secpath 元数据不匹配 →
  重建只读 Universe 缓存 → PermissionError 崩。
- ③ `module: StatsSimpleV5 → StatsSimpleV6`(check 已归一,防 backfill 存量残留)

**SUFFIX_STRIP**:`<Data>/@id` 削尾部 `Mod`(数据集改名史;**不能全局替换** ——
上千个 Alpha id 以 Mod 结尾是命名惯例)。

新提交路径的配合:submit/check 对新杂质**入口收紧**(硬校验旧路径形态即拒),
使 REPLACE-①/③、STRIP 随存量消化而自然退化 —— 单轨制(D5)。

运行期报错 ↔ 规则对照:pnl 不落盘→SET-7;`cc_2025 PermissionError`→REPL-①;
`rebuild cache in READ ONLY`→SET-2 或 REPL-② Universe 例外;
`cc_all/FULLMod PermissionError`→STRIP;`gsim.alpha has no attribute`→SET-9。

## 5. sync:产线同步(ACTIVE 集是 SSOT)

产线的实体只剩 checkpoint 目录(XML 在 alpha_src,dump/pnl 是产出)。同步语义:

- **新线**(ACTIVE 有、checkpoint 无):无需任何构建 —— 首跑时 gsim savedi=0
  天然全段。接管日一次性 +443(hwang/xmf/ybai/sli),全段首跑错峰。
- **停线**(checkpoint 有、ACTIVE 无):checkpoint 目录移入
  `<checkpoint_root>/.retired/`;dump/pnl **缺省不删**(`--purge-retired`
  显式回收当时未实现,现状 = 只归 .retired,回收需手工)。
- **重建线**(重入库:`submit --overwrite`/restage 后重过 check):归档时代码与
  XML 已更新,**归档段联动删除该因子 checkpoint** → 下次 produce 自然全段重跑。
- **接管闸门(一次性)**:停线名单(在产不在库 555)先与 combo mode0.xml 腿清单
  核对 —— 被引用的因子不许静默停产,清单报告用户裁决(补入库 or combo 摘腿,
  ops 不擅断)。

## 6. run:驱动与命令 UX

```
ops produce                     # sync + 逐因子跑 alpha_src XML(缺省全部 ACTIVE)
ops produce AlphaXxx  -u lhw    # 过滤
ops produce --dry-run           # 产线体检:新线/待停/checkpoint 健康/dump 落后天数,不跑
ops produce --sync-only         # 只做同步(建停线报告),不跑 gsim
ops produce --enddate 20260715  # 临时覆盖(钉死日重算场景;不改归档 XML,经临时副本)
ops produce --force AlphaXxx [-y]   # 删 checkpoint 全段重跑(checkpoint 范式无按日重产;确认制)
ops produce -w 16               # 并行 worker
```

- worker:ProcessPool + per-factor `factor_lock`(跨机 PG advisory)+ 锁内复验
  ACTIVE;单线失败不阻断;`BacktestError` stderr 截断入汇总。
- 输出:banner + 逐线 ✔/⚠/✘ + 汇总;**失败>0 退出码 1**(cron 判据)。
- 无状态(D7);幂等:重复跑 = checkpoint 续跑重写尾部,收敛。
- produce 仅在 170 运行(路径本机性;`--dry-run` 检测机器不符即警告)。
- **运行时点 = T 日盘前**(build_cc 08:20 之后、combo/开盘之前):T 日 dump 由
  T-1 数据经 delay 算出;delay=0 因子的 T 日行盘前无数据、由次日尾部重写自愈
  (盘中口径归 jdw delay0 产线,非本命令职责)。

## 7. config 变更

```yaml
produce:                     # 归档生产化(core/prodxml)与驱动共用
  nio_data_path: /nvme125/datasvc/data/cc_all   # D8
  enddate: TODAY                                # D3(盘前产 T 日仓位;TODAY-1 仅回测查看语义)
  startdate: '20110101'                         # D2
  backdays: 256                                 # D2/D10
  checkpoint_root: /nvme125/checkpoint          # D4
  dump_root:       /nvme125/alpha_dump          # D4
  pnl_root:        /nvme125/alpha_pnl           # D4/D6
  datasvc_prefix:  /nvme125                     # REPLACE-②(Universe 例外内置)
  module_prefix:   /mnt/storage/alphalib/alpha_src   # D11 跨机稳定 @module 前缀
```

废弃 v1 键:`production_start` / `workspace` / `readiness_dirs`。
注意:这些值经归档写死进 XML —— 改 config 只影响此后归档的因子;整改存量须
重跑迁移脚本(幂等)。

## 8. 正确性论证(实证,AUDIT-DUMP-CONSISTENCY-RESULT)

- backdays≤256 组(交集 95.3%)抽样 **40/40 逐字节相等**:gsim 位级确定 +
  160 cc_2025 vs 170 cc_all 交集窗口零漂移 + 现役产线计算正确,三事实同证。
- 4 条 drift 全在 >256 组且全部归因 D2 窗口政策(浅历史效应 / check 侧本身
  全 NaN 退化),非产线 bug、非 cc 漂移。
- **终审 = 行为对拍**(cchang 无持久 prod.xml 可 diff):样本因子迁移后的
  alpha_src XML(dump/checkpoint 临时指 scratch)直接跑,产物与 dataset
  **逐字节比对** —— 位级确定性已被证明,byte-diff 即终审。

## 9. 接管序列(每步可回退)

1. 代码落地 + 单测/e2e(scratch 产线,不触 dataset)。
2. **存量迁移**(170):迁移脚本对 ~8419 个归档 XML 原地生产化 —— dry-run 逐字段
   diff 报告 → 确认 → 执行(改前逐文件备份 `.bak` 或 git-on-JFS 前置快照)。
3. **行为对拍**(170):样本 20-50 因子(两 backdays 组 × 各作者)scratch 跑,
   dump 与 dataset byte-diff;全等才继续。
4. **闸门核对**:555 停线名单 vs combo 腿清单,报告裁决(§5)。
5. 权限交接:dataset 三根 chown 至 ops 运维模型;cchang 停手前双方各跑一天,
   对拍尾部日文件。
6. ops 正式接管日跑(手动一周观察)+ 443 新线首跑(全段,错峰)。
7. cron 进 170 crontab `# 3. generate alpha` 槽位(**盘前**:build_cc 08:20 之后、
   combo 之前,全量续跑 ~13 分钟窗口够;告警接 feishu 脚本)。cchang 线退役。

## 10. v1 代码处置清单

- **删**:`services/produce/dates.py` 全部(就绪/缺失推导);`produce.py::_install`
  (只装缺失日,与尾部重写冲突);`services/produce/xml_prepare.py`(逻辑并入
  core/prodxml);config 三键(§7)。
- **新**:`ops/core/prodxml.py`(三张规则表 + apply,纯函数);`repo.archive`
  归档段接入生产化改写 + checkpoint 联动删除;`scripts/migrate_prod_xml.py`
  (存量迁移,dry-run/备份/确认);runner env `PYTHONDONTWRITEBYTECODE=1`。
- **改**:`produce.py` 重写为 sync/run 两段(§5/§6);`cli/produce.py` 参数集;
  e2e 改产线形态;`rewrite_module_path` 支持稳定前缀(D11)。
- **留**:`ops/core/dumpfiles.py` / `ops/core/universe.py`(dry-run 落后天数与
  将来 pack 仍用);factor_lock/汇总/退出码骨架;`mark_write` 注册。

## 11. 不做什么 / TODO(plans.md 同步)

- **TODO(用户点名):`ops export`** —— 库内因子导出为可独立运行的副本;配套
  "不让用户直跑因子库因子"的约束(拆雷退役 D9 的替代保护)。
- feature 侧(PACK_L 扩行、pack --date、AlphaLoadFeat 切换)—— dump 已可喂现役
  combo(AlphaLoad),后议。
- submit/check 入口收紧新杂质(D5 单轨的配套,与主体同批或紧随)。
- combo 日增/实盘衔接(147)、滞后表因子 per-factor 处理、跨机 dump 历史归拢
  (pack 前置)、555/443 差集治理裁决(ops 出清单不擅断)。
