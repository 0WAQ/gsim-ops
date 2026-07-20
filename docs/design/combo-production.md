# combo 产线:/nvme125/production/combo(2026-07-20 立项首跑)

> 状态:首版 XML 生成 + 首跑验证完成(2026-07-20 凌晨,钉死窗 20260710)。
> 生成器 `scripts/build_combo_xml.py`(唯一正主,勿手写 XML)。alpha 侧分组
> 产线见 `factor-produce-groups.md`;combo 读的是**因子 dump**,是两段产线的
> 衔接点。

## 1. 形态:4 combo × 3 mode

```
combo(组合优化器)              mode0(计算)           mode1(pnl)        mode2(pnl vs 基准)
fguo   (1439 腿,delay1 在库)  → dump/fguo/      → pnl/fguo/mode1   → pnl/fguo/mode2
lhw    (662 腿)                → dump/lhw/       → pnl/lhw/mode1    → pnl/lhw/mode2
zxu    (121 腿)                → dump/zxu/       → pnl/zxu/mode1    → pnl/zxu/mode2
combo_eq(聚合上三条 dump)     → dump/combo_eq/   → pnl/combo_eq/mode1 → pnl/combo_eq/mode2
```

- **mode0**:腿(AlphaLoad 读 `/nvme125/alpha_dump`,cchang 现行 dataset)→
  Combo 组合优化 → combo dump;checkpoint 续跑(`run_cp.py`,首跑 savedi=0 全史)。
  combo_eq 的 mode0 用 `AlphaComboEqualProd` 聚合三条 author dump,自产 dump +
  checkpoint(它的 mode1/2 才读)。
- **mode1/mode2**:**不重复计算** —— 单 `<Alpha>` 直读本线 combo dump →
  Stats mode=1 / mode=2(mode2 带中证1000基准);无 checkpoint(run.py)。
- 全局口径(用户 2026-07-20 定):全员 **Combo_su10**(window=900/max_depth=5/
  ndays=10)、**TOP3000** universe、**全 mode 中性化**(AlphaOpVectorNeutralize
  `equ_factor_return.Alpha20` → AlphaOpPower,Operations 在 Alphas 容器外)、
  index_ret 统一中证1000、容器命名一律作者名(无 fguo_su10 式特例)。

## 2. 盘面布局

```
/nvme125/production/combo/
  xml/{fguo,lhw,zxu,combo_eq}.mode{0,1,2}.xml   # 12 份,生成器产物
  checkpoint/combo_{fguo,lhw,zxu,eq}/archive.bin
  dump/<container>/YYYY/MM/
  pnl/<owner>/mode{0,1,2}/
  logs/<stage>-<ts>.log                          # gsim 原生输出全量落盘
```

腿清单 = ops 库 delay1 ACTIVE(按名排序,顺序即 checkpoint 腿序号;未投产
ACTIVE 无 dump 者排除出 XML 并照报名单)。author 大小写已归一
(`scripts/postgres/migrate_author_case.sql`)。

## 3. Data 套件(踩过的坑,别删)

TOP3000 与中性化不是零依赖:`Modules` 必须含 HS300 / ZZ500 /
DmgrWbai_AIndexCSI{500,1000}Weight / Dmgr_adv20 / asharebalancesheet /
equ_factor_return —— 缺了 gsim `DataRegistry.build` 直接 KeyError(首跑
事故,2026-07-20)。套件照抄现役 combo_eq.xml,生成器内置。

## 4. startdate 口径(数据现实)

**20200101**(现役口径)。曾试 20110101:Combo_su10 是 LightGBM 组合优化,
lhw/zxu 因子 2020 前 100% NaN → 首日 0 有效样本,`LGBMRegressor` 拒绝
(2026-07-20 事故)。fguo 2011 有 30% 有效样本可跑,但统一回 2020——
将来要拉 fguo 到 2011 再说,per-author startdate 的口子留在生成器里。

## 5. 运行编排(首跑手动,排程后议)

```
① author mode0 ×3 并行(全史)
② combo_eq.mode0(依赖三条 dump)
③ mode1/mode2 ×8 串行(读 dump,秒级)
```

首跑记录(2026-07-20,窗 20200101→20260710):mode0 fguo ~4h / lhw ~1.5h /
zxu ~15min;产物计数与腿数精确吻合(mode0 pnl = 腿数+1 容器)。运行脚本
当次 `/tmp/combo_run.sh`(排程产品化后议)。

## 6. 后议

- **日增排程**:mode0 盘前续跑(分钟级)+ mode1/2;进 170 crontab 的槽位
  与 cchang run_combo.sh 的交接节奏(双方各跑对拍数日)。
- **alphaDir 切新根**:alpha 分组产线接管后,腿从 `/nvme125/alpha_dump` 切到
  `/nvme125/production/alpha/dump`(生成器改一个常量重出 XML)。
- **cchang combo 线退役**:`/nvme125/combo{,_cchang}/` 旧线与 dataset 归并。
- **用消费方接管**:signals/实盘的 combo_dump 读取方随排程一起迁。
