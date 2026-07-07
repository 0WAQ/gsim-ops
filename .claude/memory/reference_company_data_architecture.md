---
name: reference-company-data-architecture
description: "公司数据三层架构 rawdata→cc→dm/feature 的人 / 机器 / 流程拆分, 147→145 跨机房传输, owner 分工, .meta 双重契约 (reader ACL + writer 水位)"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 7ff88a50-5f38-42d3-a39c-f97ddef36c12
---

# 公司数据架构 (人 / 机器 / 流程视角)

物理 shape / 字段清单 / 读法见 [[reference-cc-all-data-layout]]。这一份只讲**架构**: 数据从哪来、谁负责、契约怎么走。

## 三层分层

```
rawdata  →  cc (common cache)  →  dm (data manager)
```

公司内部约定的逻辑分层。物理上 cc 和 dm **都落在 /datasvc/data/{cc_all, cc_2024, cc_2025}/** 平面里, 不分目录。统一叫 **feature** —— 因为逸飞的 level2 既像 cc 又像 dm, 边界模糊, 索性 cc + dm 都叫 feature。

"cc" 这个词既指逻辑层级, 也泛指 `/datasvc/data/` 下 cc_2024 / cc_2025 / cc_all 这组目录。`/datasvc/data/cc` 本身是软链 → `cc_2024` (历史快照, 给 read_cache 之类的 template 当默认根)。

## rawdata 链路 — wbai 单点

| 维度 | 内容 |
|---|---|
| 抓取机 | **server-147** (10.12.174.152), 中信托管机房 |
| 维护人 | **wbai** (你, 单点) |
| 角色 | 唯一能直连中信内网三个数据源的出口机 |
| 上游 | wind (Oracle) / datayes 通联 (MySQL) / citics 中信 (ClickHouse, 主要高频) |
| 落地 | `/datasvc/data/rawdata/{wind,datayes,citics}/` (147) |
| 节奏 | 每日增量 CSV |
| 流向 | 147 抓 → 传回 server-145 `/datasvc/data/rawdata/` |

mapping / 日历的元数据**不在 `rawdata/` 里, 在同级的 `/datasvc/rawdata/`** (注意路径):
- 股票 ID: `/datasvc/rawdata/secId` (N=5484)
- 交易日历: `/datasvc/rawdata/wind_calendar.csv`
- 节假日: `/datasvc/rawdata/holidays`

**Why:** rawdata 抓取在中信 IDC 内网, 跨机房安全策略只对 147 开口, 其它机器没办法。
**How to apply:** 任何"加新数据源 / 改抓取频率"的事必须经过你, 中信网络出口不会变。

## cc 生成两条 pipeline

### A. wbai 这条 (日频 + 中频)

- 走 `/production/build_cc/config.xml` (wbai 拥有 + 手动调度)
- gsim 的 **data-writer** 模块吃 rawdata CSV, 用 `source_ref/Dmgr_xxx.py` 转换成 `.npy`
- 当前线上 config 是**精简版** (部分数据源已停更增量, wbai 剔了出去); **全量 config 在 `/datasvc/template/`**
- 手动调度, 不是 daily cron。`cc_all/.meta` 水位会落后几天 (例: 2026-06-06 周六看 .meta 是 20260602, 周四周五没刷)
- 周末 / 节假日不 append, 严格按 `wind_calendar.csv` 来
- **三地各跑一次**: 上海 147 / 北京 160 / 本地 144 各自 build, 同 rawdata CSV 但不同时间不同 config 跑, 输出可能微小差异。**cc 不是 byte-identical 镜像**, 见 [[reference-server-topology]]

### B. yifei 这条 (level2)

- yifei 自研框架, 从数据提供商**推送流**直接落 .npy (不经过 rawdata, 不经过 gsim data-writer)
- **生产节点是北京 160**, 不是上海 147 (校正之前理解)。每天 ~20:00 盘后生产当日 .npy
- **wbai 隔天 backfill**: 把 yifei 落的 .npy 整合 / 注册成 cc 平面的 feature tag —— 即使数据存在, 不经过 wbai 这一步 gsim 也读不到
- 物理在北京 160 上 `cc_all/cn_equity_feature/` 和 `cc_all/cn_equity_feature_5min/` 等子目录, NFS 共享给 145/150
- shape 一次性写满几年 (`(4636, 5484)` 量级), 不遵守"日增 +1 行" 的活体协议
- cc_2024 / cc_2025 下对应 level2 子目录是**软链**回 cc_all
- 147 上 L2 没有 2025 前数据, 是因为 L2 不在 147 产, 只是后期 sync 才有部分, 没历史

## dm (cc 派生层)

- gsim 的 **data-manager** 模块, 代码在 `source_ref/` 的姊妹目录 `/usr/local/gsim/dm_src/`
- 吃 cc 已就绪的数据, 算出衍生 feature 写回 cc 平面 (`Dipv`, `Dpv*`, `Dmgr_MktRet`, `Dmgr_adv20` 等)
- 框架跟 data-writer **是同一套** (都继承 `gsim.data.DataManagerMapped`), 只是源码物理放两个目录

## "feature 设计 / 生产 / adapter" 三件事是三拨人

校正之前的脑图。level2 这条:

| 环节 | 负责人 |
|---|---|
| feature **算法设计** | 因子研究员 (fguo / lhw / sli / pwang / yq 等) |
| feature **数据生产** (推送流 → .npy) | **yifei** (QD, 跑 pipeline) |
| **gsim adapter module** (`Dmgr*L2*.py` 等) | **wbai** (你, 全部) |

含义:
- `DmgrLhw_L2FeatureCuts1430.py` 的 `Lhw` 前缀 ≠ module 作者, 八成是**feature 设计者**, module 实际由你写
- level2 adapter 改 / 加都不跨人, 你一个人能搞
- 但 feature 算法语义变化要回去问最初设计者, adapter 只是 wrapper
- yifei 在的环节是"生产", 不是"设计" / "适配"

**Why:** 公司里 level2 链路是三层人接力, ops 改东西要找对人。
**How to apply:** ops 这边动 level2 adapter 直接你来; 动 feature 算法要 cc 设计者; 动数据落盘频率 / shape 要 yifei。

## `.meta` 是双向契约

详细字段见 [[reference-cc-all-data-layout]]。架构含义:

- **reader 侧 ACL**: gsim dataloader 按 `.meta` 第一行 cutoff date 截断, 不让 qr 看到允许范围之外的数据
- **writer 侧水位**: gsim data-writer 跑增量时, 比较 XML cfg endDate vs `.meta` 第一行, 从水位往后接着写
- **同一个文件做两件事** —— 改一个文件就同时改了"可见范围"和"已生产范围"

软链 + 改 .meta 是日常操作: 同名 symlink 指向真实 .npy + 同目录放新的 .meta 限制可见范围。**没有"截断"这种物理操作**, 只是契约层换张表。

### 已知风险 (cooperative-only)

`.meta` 是 gsim dataloader 层面的契约, 不是 FS 强制。qr 走 `np.memmap` 直读 cc_all 就能绕过, 读到 yifei 那条等 ACL 之外的数据 (cc_2024 里 level2 是软链 → cc_all)。

- wbai 这条: 软链 + .meta 写法本身也有同样漏洞 (你自己也会偷懒用软链), 已知风险待修
- yifei 这条: 权限不在你这边, 已知, 之后跟他商量

物理隔离手段: 独立物理副本 (wbai 这条 cc_2024 走的就是这条) 或 FS 权限, 两条都没强制铺开。

## 跨机房网络注意

详细拓扑见 [[reference-server-topology]]。要点:
- 147 在上海中信 IDC (10.12.174.152), 145 / 160 / 150 在北京 IDC (10.9/16), 144 在本地办公网 (10.6/16)
- 147 → 北京 160 是跨机房 CSV 增量传输, **不是 cc bytes 镜像**, 各地独立 build_cc
- 本地 144 是**冷副本**: 历史 CSV 一次性推过去自建, 不在生产同步链路内, 只有 cc_2024 / cc_2025 (没 cc_all)
- 任何 "在 145 上跑脚本拉 147 数据" 的活儿要考虑带宽 / 延迟, 不能 chatty
- 本地办公室任何研究**只看得到 ≤ 2025 末数据** (硬 OOS 边界, 不用扫文件验证)

相关:
- [[reference-server-topology]] — 三地三机房物理拓扑 + NFS / JFS 分工
- [[reference-cc-all-data-layout]] — cc_all 物理布局 / shape / 读法 / 字段清单
- [[reference-gsim-data-modules]] — Dmgr / Umgr module 模板, 怎么把 rawdata 转成 cc / dm
- [[reference-gsim-xml-config]] — XML config 怎么把 module 串起来
