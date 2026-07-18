# Produce(v3:薄驱动)

因子产线驱动:`ops produce` = sync(ACTIVE 集 ⇔ checkpoint 目录集)+ run
(逐因子直接跑 alpha_src 的归档 XML,gsim checkpoint 续跑)。设计正主
`docs/design/factor-produce-v3.md`(决策台账 D1-D11、接管序列)。

## 与旧模型的边界(别加回来)

本命令**不改写 XML、不算缺失区间、不判数据就绪**:
- 归档 XML 即生产态(入库时 `repo.productionize_src` 写好;`core/prodxml.py`
  三张规则表是正主)——produce 只消费;
- "补到哪天"由 XML 里 `enddate="TODAY"` 交给 gsim 解析;"从哪继续"由
  checkpoint(`savedi`)决定;续跑每日重写尾部 ~5+N 天(尾部重写自愈:
  数据修正/末日空值都被明天的运行覆盖)。
- v1 的就绪三重规则/缺失推导/只装缺失日 2026-07-18 退场 —— 与尾部重写范式
  根本冲突(只装缺失会拒收被修正的尾部日)。

## 语义要点

- **无状态**:不写 factor_history(每日全库 = 噪音),dump/checkpoint 文件即
  记录;幂等,重复跑 = 续跑收敛。
- **sync 只动 checkpoint**:停线(离库)= checkpoint 归 `.retired/`,dump/pnl
  不删(破坏性回收永远显式);新线零构建(无 checkpoint 首跑天然全段);
  重入库的 checkpoint 作废在归档侧(repo.productionize_src)。只认 `Alpha*`
  目录 —— checkpoint 根可能与其它产线同住。定向模式(点名/-u)跳过停线对账,
  定向跑不该有全局副作用。
- **未迁移守卫**:XML 的 dumpAlphaDir ≠ 产线 dump 根 → `unmigrated` 拒跑
  (拆雷态 XML 输出指 /tmp,跑了产出静默丢失)。退出码同失败。
- **--force = 删 checkpoint 全段重跑**(checkpoint 范式无按日重产),必须显式
  点名因子 + 确认;**--enddate = 钉死日重算**,走临时 XML 副本 + 一次性
  checkpoint 目录 —— 用生产 checkpoint 跑回头日期会把存档点拽回过去,污染
  日更续跑。
- **运行时点 = T 日盘前**(build_cc 后):T 日 dump 由 T-1 数据经 delay 算出。
  产线路径(dataset 三根)是 170 本机事实,别机跑路径不存在响亮失败。
- 失败/未迁移 > 0 → 退出码 1(cron 判据)。

## 测试

`tests/test_produce.py`:json 后端 + fake backtest(workers=1 串行进程内路径);
sync 停线/新线、未迁移守卫、worker 四路、--force/--enddate 语义、退出码。
