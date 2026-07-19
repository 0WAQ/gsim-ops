# 分组产线:produce 从 per-factor 改为分组形态

> 2026-07-19 定案(用户拍板:分组上线,per-factor 太慢;组大小 500;组信息入 PG;
> 生产根 `/nvme125/production/alpha`)。机制实证正主:
> `docs/remediation/BATCH-PRODUCE-MECHANICS-RESULT.md`(sibling 平铺位级一致、
> 无腿级容错、checkpoint 序号语义)。per-factor 设计(factor-produce-v3.md)
> 仍管归档生产化(prodxml 三张规则表是两形态 XML 的共同源头)。

## 1. 为什么分组

per-factor 日产的成本大头是 ~7500 次进程启动 + Universe/Data 初始化(init-bound);
分组的 sibling `<Alpha>` 平铺把 init 摊到全组(实证:固定开销 ~4.5s/进程,
边际 ~0.69s/腿)。500 腿/组 → 全库 ~15 组,日常续跑分钟级,且 fd/内存压力
集中可控(runner 已加 RLIMIT_NOFILE 提权,根治 sudo 下 EMFILE)。

## 2. 硬约束(实证,施工红线)

1. **checkpoint 单体化**:一组一个 `archive.bin`,按腿序号反序列化 ——
   **组的腿集合与顺序一经写入永不修改**。加腿崩、删中间腿静默污染。
2. **静音是唯一合法编辑**:`dumpAlphaFile=false` 不改 checkpoint 布局,
   其余腿续跑位级一致 —— 退库/代码漂移/坏腿都走静音。
3. **组 = 故障域**:一条腿加载/运行失败整组死 —— 跑前 pre-check(存在性+语法),
   坏腿自动静音;单组失败不波及其他组,失败 >0 退出码 1。
4. **组自包含**:`code/<factor>/` 是建组时从 alpha_src 拷贝的冻结副本,
   组 XML 不引用活代码(重入库热替换会用旧状态配新代码,崩或静默错)。

## 3. 存储:roster 入 PG,盘面是派生物

- `produce_group`(gid PK, author, delay, status active/superseded, created_at)
- `produce_group_member`(gid FK, factor, **ordinal**, muted;PK (gid,factor),
  UNIQUE (gid, ordinal))

ordinal 即 checkpoint 腿序号,落库定死。`member.factor` **不 FK factor_info**:
rm 的因子 roster 行不删(删行 = 序号移位 = 静默污染),只置 muted。
重组 = 新 gid 建组 + 旧组 superseded(roster 留痕,gid 永不复用)。
读写只经 `FactorRepository` 组方法;DDL 正主 `scripts/postgres/`(init 镜像 +
migrate_produce_group.sql),代码引导 `ops/infra/schema.py`。

## 4. 状态模型:组产 / 单产 / 待产(2026-07-19 定稿)

**可生产 = 全部 ACTIVE**(delay 无关);**现行生产闸 = delay==1**(delay0 归 jdw
盘中产线,是部署事实不是状态;放开闸 = 加一层 delay0 目录,模型不变)。

```
可生产(ACTIVE)
├── 在产 = 组产(in-group)+ 单产(single-factor)
└── 待产(pending)= 可生产 − 在产
```

- **组产**:roster/ordinal 在 `produce_group` 两表;组大小 500,按 author+delay 分层。
- **单产**:`produce_single(factor PK, author, admitted_at)` 注册表;概念上 =
  "组大小为 1 的组"(以因子名代 gid,无 roster/ordinal),冻结代码 + 自有 XML +
  checkpoint + logs,与组同构。**XML 生成路径不同**:组 = 合并式(sibling 平铺),
  单产 = 补丁式(归档 XML 单 `<Alpha>` 原形态保留,改四处:module→冻结副本、
  checkpointDir、dumpAlphaDir、pnlDir)。
- **待产**:纯推导,**零盘面零库表足迹**;新到因子默认停在这里,只报告,
  **永不生产**(默认屏蔽;上产线必须显式动作)。

**状态转移**:
| 转移 | 触发 | 性质 |
|---|---|---|
| 待产 → 单产 | `--single-only <名字>` 显式点名(人工闸) | 准入:冻结 + 生成 XML + 注册 |
| 待产/单产 → 组产 | `scripts/bootstrap_groups.py --apply` 封组 | 单产注册移除;组首跑全史 |
| 组产 → 单产 | 代码漂移(重入库) | **自动**(产线连续性);组内腿静音留 roster |
| 单产漂移 | 代码漂移 | 重冻结 + 删 checkpoint 全段重跑(自动) |
| 离 ACTIVE | 组产:静音留 roster;单产:注册移除 | 一个因子只有一个生产之家 |

## 5. 盘面布局(全部在新生产根,与旧 dataset 隔离)

```
/nvme125/production/alpha/
  groups/<author>/delay1/<gid>/
    group.xml          # sibling <Alpha> 平铺,腿按因子名字典序
    code/<factor>/     # 冻结代码副本(建组时 cp -a)
    checkpoint/        # 组 archive.bin
    logs/              # 每次运行一份全量日志
  single/<author>/delay1/<factor>/
    <factor>.xml       # 单产 XML(补丁式,准入时生成)
    code/              # 冻结副本(目录本身即因子内容)
    checkpoint/
    logs/
  dump/<factor>/YYYY/MM/   # 共享产物(组产单产同根)
  pnl/<factor>
```

无 `pending/` 任何目录。旧 dataset(`/nvme125/alpha_dump|alpha_pnl|checkpoint`)
与 cchang 产线不动;combo 切 alphaDir 是后续独立步骤。

## 6. 命令面

```bash
# 建组/封组(一次性 + 后续攒批;缺省 dry-run 出报告 + 首组样品 XML)
uv run python scripts/bootstrap_groups.py
sudo .venv/bin/python scripts/bootstrap_groups.py --apply -y

# 日常驱动(170,T 日盘前)
ops produce --grouped                 # 缺省 = 组产 + 注册单产全跑(待产只报告)
ops produce --grouped --groups-only   # 只跑组产
ops produce --grouped --single-only            # 只跑全部注册单产
ops produce --grouped --single-only AlphaXxx   # 点名跑;待产中的先准入再跑(人工闸)
ops produce --grouped --dry-run       # 体检:组腿数/静音/checkpoint + 单产 checkpoint
ops produce --grouped --sync-only     # 只收敛(静音/降级/准入移除),不跑 gsim
ops produce --grouped -w 4 --timeout 43200     # bootstrap 全史首跑
```

- config `produce.grouped`:root / group_size(500)/ workers(8);
  块缺席 = 未启用,`--grouped` 响亮报错,旧 per-factor 不受影响。
- `--timeout`:bootstrap 全史首跑必须放大(config.mode.timeout 1800 远远不够)。
- bootstrap:全库 500 腿/组全史 ~660GB/组,1TB 机器基本串行
  (cchang 兜底期执行,产物全在新根,旧线零接触)。

## 7. 与 per-factor produce 的边界

- `--grouped` 缺席时 `ops produce` 行为不变(旧 dataset、cchang 交接期兜底)。
- 单产是分组模式的注册形态(roster 在 PG),与 per-factor `ops produce`
  (旧 dataset)是两回事;`--force`/`--enddate` 等定向语义暂不进入分组模式
  (单因子重算走 per-factor 定向)。
- 上线序列:bootstrap_groups --apply → produce --grouped -w4(bootstrap 首跑)
  → 新根 dump vs 旧 dataset 尾部 byte-diff 抽验 → 手动观察数日 →
  combo 切 alphaDir → cchang 退役(后两步不在本批)。

## 8. 不做(本批)

- checkpoint 移植工具(.so 格式破解):bootstrap 走全史重跑,一次性成本已接受。
- 重组自动化:初版手动(bootstrap 脚本重跑封新组;组重组脚本后议)。
- delay0 分组、combo/信号入 production 根(目录已预留,立项再议)。
