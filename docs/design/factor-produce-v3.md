# 因子增量生产 v3:`ops produce` 产线化实施方案

> 2026-07-18。收敛过程与实证:`docs/remediation/DISCOVER-PRODUCE-PROD(-RESULT).md`
> (生产形态摸底)+ `AUDIT-DUMP-CONSISTENCY(-RESULT).md`(dataset 一致性对账)。
> 取代分支上 v1 实现(就绪判定/缺失推导模型)的过时部分,v1 处置清单见 §10。

## 1. 需求与定位

ACTIVE 因子的 alpha_dump 日增生产是实盘 combo 的上游(mode0 经 AlphaLoad 逐因子
读 per-date dump)。现状:cchang 以私有脚本临时代管(7530 因子/全量 ~13 分钟/
手动触发/无告警),与 ops 生命周期完全脱钩 —— **在库未投产 443 个(hwang/xmf/
ybai/sli 整批作者不在产线),在产不在库 555 个**。本方案把产线正式收进 ops:
`ops produce` 成为唯一产线,cchang 线为过渡参照。

## 2. 决策台账(全部已拍板,2026-07-17/18)

| # | 决策 | 理由 |
|---|---|---|
| D1 | 机制 = per-factor 持久 prod.xml + `run_cp.py` checkpoint 续跑,enddate 交给 gsim 解析 | 生产范式现役验证;ops 侧就绪判定/缺失推导是重复造轮(v1 退场) |
| D2 | 窗口 = **startdate 20110101 + backdays 强制 256** | 实盘已运行在此行为上,一致性优先;改窗口 = 生产行为突变(保真方案 20150101+原 backdays 被否) |
| D3 | enddate = config 键(`TODAY`/`TODAY-1`/钉死日),缺省 **TODAY-1** | 因子级不产当天(数据未就绪产空仓垃圾);TODAY-1 为现役实跑形态 |
| D4 | 落点 = **沿用现有 dataset**:`/nvme125/alpha_dump` + `/nvme125/alpha_pnl` + `/nvme125/checkpoint`,路径纳入 config | 资产/checkpoint 延续、combo 零改动;且结构上分开"check 验证 dump"(alphalib sidecar)与"产线生产 dump"两个事实族 —— check 归档 rmtree 与产线同居的冲突自然消失。审计证实 dataset 可信(§8) |
| D5 | 杂质净化 = **双轨**:gen 兼容存量杂质;submit/check 入库收紧新增(**TODO 后议**,存量归一后 gen 规则退化) | 存量 7530 不能被治理阻塞上线 |
| D6 | dumpPnl = true,pnlDir = 产线 pnl 根(与 ops alpha_pnl 隔离,语义不混) | 现役强制开,有实际用途;check 快照字节源不受污染 |
| D7 | 无状态:不写 factor_history,dump/checkpoint 文件即记录 | 每日全库 = 海量噪音事件 |
| D8 | 数据根 = `/nvme125/datasvc/data/cc_all`(170 本机产线 cc) | cc_2025 在 170 无权限;cc_all 持续日增;审计证实与 160 cc_2025 交集窗口零漂移 |

## 3. 总体架构

```
ops produce = gen(模板 → prod.xml)+ sync(ACTIVE 集 ⇔ 产线集)+ run(逐线续跑驱动)

产线布局(per-factor,路径全部来自 config produce 块):
  <xml_root>/<因子>/prod.xml + <因子>/src/(alpha_src 副本)
  <checkpoint_root>/<因子>/archive.bin        # 持久;损坏 = 删掉全段重跑
  <dump_root>/<因子>/YYYY/MM/YYYYMMDD{v1,v2}.npy   # gsim 直写,尾部 ~5+N 天每日重写
  <pnl_root>/<因子>                            # 单文件
```

与 check 的边界:check 是验证(cc_2025 冻结窗口、alphalib sidecar,零改动);
produce 是生产(cc_all、dataset)。同一因子两处 dump 是**两个事实族**,不对账、
不互搬(pack/feature 后议时再定消费哪边)。

## 4. gen:XML 改写规则(三张声明式表,SET → REPLACE → SUFFIX_STRIP)

输入 = `alpha_src/<因子>/` 的归档 XML(拷贝副本,永不改原件)。规则参数化:
标 ⚙ 的值来自 config,不硬编码。执行顺序有语义(REPLACE ① 先归一旧形态,
② 再统一迁移前缀)。

**SET(定位节点,强制设值)**:
1. Constants/@niodatapath = ⚙ 产线数据根(cc_all)
2. Constants/@backdays = 256(D2;>256 自 20110101 起会早于数据起始,gsim 重建只读缓存崩)
3. Constants/@checkpointDir = ⚙ `<checkpoint_root>/<因子>/`
4. Constants/@checkpointDays = 5
5. Universe/@startdate = ⚙ 20110101(D2)
6. Universe/@enddate = ⚙ PROD_ENDDATE(D3,缺省 TODAY-1;CLI 可覆盖)
7. Stats/@pnlDir = ⚙ 产线 pnl 根;@dumpPnl = true(D6)
8. Portfolio/Alpha/@dumpAlphaFile = true;@dumpAlphaDir = ⚙ 产线 dump 根
9. Alpha/@module = `<产线 src 副本>/<模板原 module 的 basename>` ——
   **文件名沿用模板原 basename,不用目录名拼**(目录名 ≠ .py 名的因子存量不少,
   拼错 → gsim 回退 gsim.alpha 找属性 → AttributeError);模板 module 缺失才回退
   `<目录名>.py`

**REPLACE(扫属性值,替换子串/前缀;顺序①→②→③)**:
- ① 存量杂质归一(D5 双轨的 gen 半边):
  `niodatapath: /datasvc/data/cc_2025 → /datasvc/data/cc_all`(前缀)、
  `niodatapath: /cache/data → /datasvc/data`(前缀)、
  `niodatapath|dataPath: /home/fguo/data_local → /datasvc/data/cc_all/cn_equity_feature_5min`(整值)
- ② `*`(所有属性): `/datasvc → ⚙ /nvme125/datasvc`(前缀迁移通吃;rawdata 路径
  散落在 dataPath/rawpricePath/industryPath/ST 等多属性,精确列举会漏)。
  **★ Universe 例外:跳过 `<Universe>` 标签** —— secID/holidaysfile/calendarfile
  指向 gsim 侧基础数据,必须保持 `/datasvc` 原样;加前缀 → secpath 元数据不匹配 →
  重建只读 Universe 缓存 → PermissionError 崩。
- ③ `module: StatsSimpleV5 → StatsSimpleV6`(check 已归一,防 backfill 存量残留,保留兜底)

**SUFFIX_STRIP(标签+属性精确定位,削值尾后缀)**:
- `<Data>/@id` 削尾部 `Mod`(数据集改名史;**不能全局替换** —— 上千个 Alpha id
  以 Mod 结尾是命名惯例,绝不能动)

运行期报错 ↔ 规则对照(排障字典):pnl 不落盘→SET-7;`cc_2025 PermissionError`→
REPL-①;`rebuild cache in READ ONLY`→SET-2 或 REPL-② Universe 例外;
`cc_all/FULLMod PermissionError`→STRIP;`gsim.alpha has no attribute`→SET-9。

## 5. sync:产线同步(ACTIVE 集是 SSOT)

每次 `ops produce` 开跑前对账 ACTIVE 集 ⇔ 产线目录集:

- **建线**(ACTIVE 有、产线无):拷 src 副本 + gen prod.xml;无 checkpoint 首跑
  即全段(gsim savedi=0 天然全跑)。接管日一次性 +443(hwang/xmf/ybai/sli)。
- **停线**(产线有、ACTIVE 无):产线目录移入 `<xml_root>/.retired/`(不删 dump ——
  破坏性回收走 `--purge-retired` 显式确认,缺省只停不删)。
- **重建线**(src 代码变更:`submit --overwrite` 重入库 / restage 后重过 check):
  以 `alpha_src` 的 mtime/内容摘要 vs 产线副本判定;重建 = 删 checkpoint + 新 gen +
  全段重跑。
- **接管闸门(一次性)**:停线名单(在产不在库 555)先与 combo mode0.xml 腿清单
  核对 —— 被 combo 引用的因子不许静默停产,清单报告给用户裁决(治理归属:
  这批因子要么补入库要么 combo 摘腿,ops 不擅断)。

## 6. run:驱动与命令 UX

```
ops produce                     # sync + 逐线续跑(缺省全部 ACTIVE 产线)
ops produce AlphaXxx  -u lhw    # 过滤
ops produce --dry-run           # 产线状态报告:缺线/待建/待停/checkpoint 健康/落后天数,不跑
ops produce --sync-only         # 只做产线同步(建线/停线报告),不跑 gsim
ops produce --enddate 20260715  # 临时覆盖 PROD_ENDDATE(钉死日重算场景)
ops produce --force AlphaXxx [-y]   # 重建线:删 checkpoint 全段重跑(checkpoint 范式下
                                    # 无按日重产的细粒度;确认制)
ops produce -w 16               # 并行 worker
```

- worker:ProcessPool + per-factor `factor_lock`(跨机 PG advisory)+ 锁内复验
  ACTIVE;单线失败不阻断;`BacktestError` stderr 截断入汇总。
- 输出:banner + 逐线 ✔/⚠/✘ + 汇总;**失败>0 退出码 1**(cron 判据)。
- 无状态(D7);幂等:重复跑 = checkpoint 续跑重写尾部,收敛。

## 7. config 变更

```yaml
produce:
  nio_data_path: /nvme125/datasvc/data/cc_all   # D8
  enddate: TODAY-1                              # D3(TODAY / TODAY-1 / YYYYMMDD)
  startdate: '20110101'                         # D2
  backdays: 256                                 # D2
  xml_root:        /nvme125/produce/xml         # 产线 prod.xml + src 副本
  checkpoint_root: /nvme125/checkpoint          # D4 沿用现有资产
  dump_root:       /nvme125/alpha_dump          # D4
  pnl_root:        /nvme125/alpha_pnl           # D4/D6
  datasvc_prefix:  /nvme125                     # REPLACE-② 前缀(Universe 例外内置)
```

废弃 v1 键:`production_start` / `workspace` / `readiness_dirs`。
路径为 170 本机事实,produce 仅在 170 运行(dump/checkpoint 本机性;文档与
`--dry-run` 提示机器不符时警告)。

## 8. 正确性论证(实证,AUDIT-DUMP-CONSISTENCY-RESULT)

- backdays≤256 组(交集 95.3%)抽样 **40/40 逐字节相等**:gsim 位级确定 +
  160 cc_2025 vs 170 cc_all 交集窗口零漂移 + 现役产线计算正确,三事实同证。
- 4 条 drift 全在 >256 组且全部归因 D2 窗口政策(浅历史效应 / check 侧本身全 NaN
  退化),非产线 bug,非 cc 漂移。
- **gen 验证 = 行为对拍**(cchang 无持久 prod.xml 可 diff):对样本因子
  (两 backdays 组 × 各作者)用 ops gen 的 prod.xml 跑进 scratch,dump 与 dataset
  **逐字节比对** —— 位级确定性已被证明,byte-diff 即终审。

## 9. 接管序列(每步可回退)

1. 代码落地 + 单测/e2e(scratch 产线,不触 dataset)。
2. **行为对拍**(170):样本 20-50 因子 gen → scratch 跑 → 与 dataset byte-diff;
   全等才继续。
3. **闸门核对**:555 停线名单 vs combo 腿清单,报告裁决(§5)。
4. 权限交接:dataset 三根 chown 至 ops 运维模型(root/组),cchang 停手前双方各跑
  一天对拍尾部日文件。
5. ops 正式接管日跑(手动一周观察)+ 443 新线首跑(全段,错峰)。
6. cron 进 170 crontab `# 3. generate alpha` 槽位(build_cc 08:20 之后;告警通道
   接 feishu 脚本)。cchang 线退役。

## 10. v1 代码处置清单

- **删**:`services/produce/dates.py` 全部(就绪三重规则/缺失推导/resolve_target);
  `produce.py::_install`(只装缺失日 —— 与尾部重写范式冲突);config 三键(§7)。
- **改**:`xml_prepare.py` 重写为三张规则表驱动(§4);`produce.py` 重写为
  gen/sync/run 三段(§3/§5/§6);`cli/produce.py` 参数集(§6);e2e 改产线形态。
- **留**:`ops/core/dumpfiles.py` / `ops/core/universe.py`(布局走查/轴读取,
  dry-run 落后天数与将来 pack 仍用);factor_lock/汇总/退出码骨架;
  `mark_write` 注册。

## 11. 不做什么 / 后议(plans.md 同步)

- feature 侧(PACK_L 扩行、pack --date、AlphaLoadFeat 切换)—— 后议,dump 即可喂
  现役 combo(AlphaLoad)。
- 入库净化收紧 + 存量归一 + gen 规则退化(D5 TODO)。
- combo 日增/实盘衔接(147)、滞后表因子 per-factor 处理、跨机 dump 历史归拢(pack 前置)。
- 555/443 差集的治理裁决本身(ops 出清单,不擅断)。
