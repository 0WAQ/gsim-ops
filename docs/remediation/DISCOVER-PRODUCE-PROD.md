# DISCOVER:因子日增生产的 gsim 生产形态摸底(执行者于 170;只读)

背景:因子增量生产(`ops produce`)要对齐实盘 combo 的生产模式 —— gsim 支持
`enddate="TODAY"` + checkpoint 续跑,生产配置范本在 `170:/nvme125/combo`。
本手册全部只读(ls / cat / grep),不改任何文件、不跑任何回测。
产出贴回 `DISCOVER-PRODUCE-PROD-RESULT.md`。

## 1. 产线目录形态

```bash
ls -la /nvme125/combo/
find /nvme125/combo -maxdepth 2 -type d | head -40
```

回答:每个 combo 一个目录各自带 config?还是集中式?目录内标准构成
(config / checkpoint / 产出 / 日志)?

## 2. 生产 config 原文(最关键)

```bash
grep -rl 'TODAY' /nvme125/combo --include='*.xml' | head
# 挑一份代表性的贴全文:
cat <上面任选一份>.xml
```

判读要点(贴全文即可,判读方来抠):
- `Universe/@startdate` 写死何值、`@enddate` 的 TODAY 写法;
- `Constants`:`@niodatapath` 指哪(cc / cc_all / 别的)、`@checkpointDir`、
  `@checkpointDays`、`@backdays`;
- `Portfolio/Alpha`:`@dumpAlphaFile` / `@dumpAlphaDir` 实际指向;
- `Stats`:`@dumpPnl` 开不开、`@pnlDir`;
- `Modules/Data` 各项的 `@niodatapath` 前缀。

## 3. TODAY 的解析语义(gsim 源码)

```bash
grep -rn 'TODAY' /usr/local/gsim --include='*.py' | head -20
# 贴命中处上下文 ±15 行(通常在 Universe/日期解析处)
```

回答:TODAY = 日历今天,还是数据轴上最后可见日?当天数据没 build 完时跑,
gsim 收口 / 报错 / 产垃圾,哪一种?

## 4. checkpoint 续跑的落盘行为

```bash
ls -la <某产线的 checkpointDir>/
ls -la <某产线的 dump 产出目录>/2026/07/ | tail -15
stat <最近两天的 dump 文件>   # 看 mtime:每日跑完是只新增当日,还是全段重写
```

回答:checkpoint 文件构成与大小;每日一跑后 dump 目录是**只多一天**还是
历史文件 mtime 也变(重写)?checkpoint 损坏/缺失时的处置惯例(删了重跑全段?)。

## 5. 每日触发方式

```bash
crontab -l 2>/dev/null; sudo crontab -l 2>/dev/null
ls /etc/cron.d/ 2>/dev/null
systemctl list-timers 2>/dev/null | head
```

回答:combo 生产由什么驱动、几点、失败了谁知道(日志/告警)。

## 6. 单次运行成本(有日志就贴)

```bash
ls -la /nvme125/combo/*/log* /nvme125/combo/*/*.log 2>/dev/null | head
# 有日志:贴最近一次运行的起止时间戳
```

回答:一条产线每日一跑的 wall time 量级。

---

判读方拿到结果后收敛三件事:① ops produce 是否删除就绪判定/缺失推导,改为
静态产线目录 + TODAY 续跑;② dump 直落生产目录还是经工作区安装;③ cron 时刻
与告警通道。
