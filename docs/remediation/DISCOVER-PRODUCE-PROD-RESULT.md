# DISCOVER-PRODUCE-PROD: 执行结果

执行环境: server-170 (10.9.100.170), 2026-07-17 ~20:45 CST

---

## 1. 产线目录形态

```
$ ls -la /nvme125/combo/
total 46
drwxrwxr-x  7 wbai alpha-core  7 Jul 17 16:15 .
drwxr-xr-x 11 root root       11 Jul 14 16:04 ..
drwxrwxr-x  5 wbai wbai        5 Jul 17 04:13 checkpoint
drwxrwxr-x  5 wbai wbai        5 Jul 16 13:34 combo_dump
drwxrwxr-x  6 wbai wbai        6 Jul 16 13:34 combo_pnl
drwxrwxr-x  4 wbai wbai        8 Jul 17 17:10 test
drwxrwxr-x  5 wbai alpha-core  6 Jul 16 13:32 xml

$ find /nvme125/combo -maxdepth 2 -type d | head -40
/nvme125/combo
/nvme125/combo/xml
/nvme125/combo/xml/lhw
/nvme125/combo/xml/zxu
/nvme125/combo/xml/fguo
/nvme125/combo/combo_dump
/nvme125/combo/combo_dump/lhw
/nvme125/combo/combo_dump/zxu
/nvme125/combo/combo_dump/fguo
/nvme125/combo/test
/nvme125/combo/test/combo_pnl
/nvme125/combo/test/combo_dump
/nvme125/combo/combo_pnl
/nvme125/combo/combo_pnl/lhw
/nvme125/combo/combo_pnl/combo_eq
/nvme125/combo/combo_pnl/zxu
/nvme125/combo/combo_pnl/fguo
/nvme125/combo/checkpoint
/nvme125/combo/checkpoint/combo_fguo
/nvme125/combo/checkpoint/combo_zxu
/nvme125/combo/checkpoint/combo_lhw
```

**回答**: 集中式布局。`/nvme125/combo/` 是顶层,按职能分子目录:
- `xml/` — 配置,按 author 再分(lhw/zxu/fguo),每人有 mode0.xml + mode1.xml
- `xml/combo_eq.xml` — 等权合并 combo(聚合三人)
- `checkpoint/` — 按 `combo_{author}` 分目录,各含一个 `archive.bin`
- `combo_dump/` — 按 author 分,产出目录
- `combo_pnl/` — 按 author + combo_eq 分,PNL 产出
- `test/` — 临时测试用

标准构成 = **config(xml/)** + **checkpoint(checkpoint/)** + **dump 产出(combo_dump/)** + **PNL 产出(combo_pnl/)**。无独立日志目录(日志落 /tmp)。

---

## 2. 生产 config 原文

### 2.1 含 TODAY 的 XML 列表

```
$ grep -rl 'TODAY' /nvme125/combo --include='*.xml'
/nvme125/combo/xml/lhw/mode0.xml
/nvme125/combo/xml/lhw/mode1.xml
/nvme125/combo/xml/zxu/mode1.xml
/nvme125/combo/xml/zxu/mode0.xml
/nvme125/combo/xml/fguo/mode0.xml
/nvme125/combo/xml/fguo/mode1.xml
```

### 2.2 代表性 config: lhw/mode1.xml (全文)

```xml
<?xml version='1.0' encoding='utf-8'?>
<gsim>
    <Constants backdays="256" niodatapath="/nvme125/datasvc/data/cc" niomapprivate="true" authorWeight="lhw:1.0," time_intensive="false" product_id="combo_lhw"></Constants>
    <Universe startdate="20200101" enddate="TODAY-1" secID="/datasvc/rawdata/secID" holidaysfile="/datasvc/rawdata/holidays" calendarfile="/datasvc/rawdata/wind_calendar.csv"></Universe>
    <Modules>
        <Data id="ALL" module="UmgrAll" path=""></Data>
        <Data id="ALL_TRD" module="UmgrTrd" path=""></Data>
        <Data id="Basedata" module="DmgrBasedata" rawpricePath="" industryPath="" ST="" path="" niomapprivate="true"></Data>
        <Data id="PriceLimit" module="DmgrPriceLimit" dataPath="" path=""></Data>
        <Data id="adjfactor" module="DmgrAdjfactor" dataPath="" niomapprivate="true" path=""></Data>
        <Data id="adjprice" module="DmgrAdjprice" niomapprivate="true" path=""></Data>
        <Data id="ipo" module="DmgrIPO" dataPath="" path=""></Data>
        <Data id="ashareeodprices" module="Dmgrashareeodprices" dataPath="" niomapprivate="true"></Data>
        <Data id="aindexeodprices" module="Dmgraindexeodprices" dataPath="" niomapprivate="true"></Data>
        <Data id="Dmgr_MktRet" module="/usr/local/gsim/dm_src/Dmgr_MktRet.py" niomapprivate="true"></Data>
        <Combo id="Combo_bj202" module="/usr/local/gsim/combo_src/Combo_bj202.cpython-310-x86_64-linux-gnu.so"></Combo>
    </Modules>
    <Portfolio id="Portfolio" booksize="20e6" homecurrency="CNY">
        <Stats module="StatsSimpleV6" mode="1" index_ret="Dmgr_MktRet.mkt_avg_ret" thres="90" 
            tradePrice="close" tax="0." fee="0." slippage="0." printStats="true" 
            dumpPnl="true" pnlDir="/nvme125/combo/combo_pnl/lhw/mode1/"></Stats>
        <Alpha id="lhw" module="AlphaLoad" universeId="ALL_TRD" alphaDir="/nvme125/combo/combo_dump/lhw" ver="v2"></Alpha>
            <Operations>
                <Operation module="AlphaOpPower" exp="1.0"></Operation>
            </Operations>
    </Portfolio>
</gsim>
```

**注意**: mode1 无 `checkpointDays`/`checkpointDir`(不用 checkpoint)、无 `Combo` module、
`Alpha` 单一 AlphaLoad 指向 `combo_dump/lhw`(即 mode0 的产出)。

### 2.3 代表性 config: lhw/mode0.xml (结构摘要,768 行)

```xml
<?xml version='1.0' encoding='utf-8'?>
<gsim>
    <Constants backdays="256" niodatapath="/nvme125/datasvc/data/cc" niomapprivate="true"
               authorWeight="lhw:1.0," time_intensive="false" product_id="combo_lhw"
               checkpointDays="5" checkpointDir="/nvme125/combo/checkpoint/combo_lhw"></Constants>
    <Universe startdate="20200101" enddate="TODAY" secID="/datasvc/rawdata/secID"
              holidaysfile="/datasvc/rawdata/holidays" calendarfile="/datasvc/rawdata/wind_calendar.csv"></Universe>
    <Modules>
        <!-- 标准 Dmgr 套件 (同 mode1) -->
        <Combo id="Combo_bj202" module="/usr/local/gsim/combo_src/Combo_bj202.cpython-310-x86_64-linux-gnu.so"></Combo>
    </Modules>
    <Portfolio id="Portfolio" booksize="20e6" homecurrency="CNY">
        <Stats module="StatsSimpleV6" tradePrice="close" index_ret="Dmgr_MktRet.mkt_avg_ret"
               mode="0" thres="90" tax="0." fee="0." slippage="0." printStats="true"
               dumpPnl="true" pnlDir="/nvme125/combo/combo_pnl/lhw/mode0/"></Stats>
        <Alphas id="lhw" universeId="ALL_TRD" booksize="20e6" delay="1" combo="Combo_bj202"
                irWindow="120" mindays="60" irExp="1.0" irCon="0.0"
                dumpAlphaCombo="true" dumpAlphaFile="true"
                dumpAlphaDir="/nvme125/combo/combo_dump/lhw/" moduleId="Alpha" lg="240">
            <Alpha id="AlphaLhw5mCoarseV11424947AccelSeg3749ReturnTailAboveVwapRegimeSMD8"
                   module="AlphaLoad" universeId="ALL_TRD" alphaDir="/nvme125/alpha_dump/" ver="v2"></Alpha>
            <!-- ... ~700 个 Alpha 条目 ... -->
            <Alpha id="AlphaLhwV8Ei14568T1CorrVpPD4L20_105ca8af"
                   module="AlphaLoad" universeId="ALL_TRD" alphaDir="/nvme125/alpha_dump/" ver="v2"></Alpha>
            <Operations>
                <Operation module="AlphaOpPower" exp="1.0"></Operation>
            </Operations>
        </Alphas>
    </Portfolio>
</gsim>
```

**关键差异 mode0 vs mode1**:
| 属性 | mode0 | mode1 |
|---|---|---|
| `enddate` | `TODAY` | `TODAY-1` |
| `checkpointDays` | `5` | (无) |
| `checkpointDir` | `/nvme125/combo/checkpoint/combo_lhw` | (无) |
| `Combo` module | `Combo_bj202.so` | (无) |
| `Alpha` | ~700 个因子,`alphaDir="/nvme125/alpha_dump/"` | 单个 AlphaLoad 指向 `combo_dump/lhw`(mode0 产出) |
| `dumpAlphaCombo` | `true` | (无) |
| `dumpAlphaFile` | `true` | (无) |
| `dumpAlphaDir` | `/nvme125/combo/combo_dump/lhw/` | (无) |

### 2.4 combo_eq.xml (等权聚合,非 TODAY,贴全文)

```xml
<?xml version='1.0' encoding='utf-8'?>
<gsim>
    <Constants backdays="256" niodatapath="/nvme125/datasvc/data/cc" niomapprivate="true" authorWeight="zxu:1.0," time_intensive="false" product_id="combo_zxu"></Constants>
    <Universe startdate="20230101" enddate="20260713" secID="/datasvc/rawdata/secID" holidaysfile="/datasvc/rawdata/holidays" calendarfile="/datasvc/rawdata/wind_calendar.csv"></Universe>
    <Modules>
        <Data id="ALL" module="UmgrAll" path=""></Data>
        <Data id="ALL_TRD" module="UmgrTrd" path=""></Data>
        <Data id="Basedata" module="DmgrBasedata" rawpricePath="" industryPath="" ST="" path="" niomapprivate="true"></Data>
        <Data id="PriceLimit" module="DmgrPriceLimit" dataPath="" path=""></Data>
        <Data id="adjfactor" module="DmgrAdjfactor" dataPath="" niomapprivate="true" path=""></Data>
        <Data id="adjprice" module="DmgrAdjprice" niomapprivate="true" path=""></Data>
        <Data id="ipo" module="DmgrIPO" dataPath="" path=""></Data>
        <Data id="ashareeodprices" module="Dmgrashareeodprices" dataPath="" niomapprivate="true"></Data>
        <Data id="aindexeodprices" module="Dmgraindexeodprices" dataPath="" niomapprivate="true"></Data>
        <Data id="HS300" module="/usr/local/gsim/source_ref/umgr_index.py" dataPath="/datasvc/rawdata_wind/HS300/"  niomapprivate="true"/>
        <Data id="ZZ500" module="/usr/local/gsim/source_ref/umgr_index.py" dataPath="/datasvc/rawdata_wind/ZZ500/"  niomapprivate="true"/>
        <Data id="TOP3300" module="/usr/local/gsim/source_ref/umgr_topliquid.py" univsize="3300"  niomapprivate="true"/>
        <Data id="TOP3000" module="/usr/local/gsim/source_ref/umgr_topliquid.py" univsize="3000"  niomapprivate="true"/>
        <Data id="DmgrWbai_AIndexCSI500Weight" module="/usr/local/gsim/source_ref/DmgrWbai_AIndexCSI500Weight.py" dataPath="/datasvc/rawdata_wind/AIndexCSI500Weight/" niomapprivate="true"/>
        <Data id="DmgrWbai_AIndexCSI1000Weight" module="/usr/local/gsim/source_ref/DmgrWbai_AIndexCSI1000Weight.py" dataPath="/datasvc/rawdata_wind/AIndexCSI500Weight/" niomapprivate="true"/>
        <Data id="Dmgr_adv20" module="/usr/local/gsim/dm_src/Dmgr_advN.py" ndays="20" nioimapprivate="true"/>
        <Data id="Dmgr_MktRet" module="/usr/local/gsim/dm_src/Dmgr_MktRet.py" niomapprivate="true"></Data>
        <Data id="asharebalancesheet" module="/usr/local/gsim/source_ref/Dmgr_asharebalancesheet.py" dataPath="/datasvc/rawdata_wind/asharebalancesheet" niomapprivate="true"/>
        <Data id="equ_factor_return" module="Dmgrequ_factor_return" dataPath="/datasvc/rawdata/rawdata_datayes/equ_factor_return" niomapprivate="true" />
    </Modules>
    <Portfolio id="Portfolio" booksize="20e6" homecurrency="CNY">
        <Stats module="StatsSimpleV6" mode="2" index_ret="aindexeodprices.s_dq_pctchange_000852" thres="90" 
            tradePrice="close" tax="0." fee="0." slippage="0." printStats="true" 
            dumpPnl="true" pnlDir="/nvme125/combo/combo_pnl/combo_eq/TOP3300/mode2"></Stats>
        <Alphas id="opt" universeId="TOP3300" booksize="20e6" delay="1" combo="AlphaComboEqualProd"
                dumpAlphaCombo="false" dumpAlphaFile="false" dumpAlphaDir="/nvme125/combo/combo_dump/combo_eq/" moduleId="Alpha" lg="240">
            <Alpha id="zxu" module="AlphaLoad" universeId="TOP3300" alphaDir="/nvme125/combo/combo_dump/zxu" ver="v2"/>
            <Alpha id="lhw" module="AlphaLoad" universeId="TOP3300" alphaDir="/nvme125/combo/combo_dump/lhw" ver="v2"/>
            <Alpha id="fguo" module="AlphaLoad" universeId="TOP3300" alphaDir="/nvme125/combo/combo_dump/fguo" ver="v2"/>
            <Operations>
                <Operation module="AlphaOpVectorNeutralize" factor="equ_factor_return.Alpha20"/>
                <Operation module="AlphaOpPower" exp="1.0"/>
            </Operations>
        </Alphas>
    </Portfolio>
</gsim>
```

**注意**: combo_eq.xml `enddate` 写死 `20260713`(非 TODAY),`combo="AlphaComboEqualProd"`,
读入三人各自的 `combo_dump`(即 mode0 产出)。

### 2.5 fguo/mode1.xml (全文,同结构)

```xml
<?xml version='1.0' encoding='utf-8'?>
<gsim>
    <Constants backdays="256" niodatapath="/nvme125/datasvc/data/cc" niomapprivate="true" authorWeight="fguo:1.0," time_intensive="false" product_id="combo_fguo"></Constants>
    <Universe startdate="20200101" enddate="TODAY-1" secID="/datasvc/rawdata/secID" holidaysfile="/datasvc/rawdata/holidays" calendarfile="/datasvc/rawdata/wind_calendar.csv"></Universe>
    <Modules>
        <Data id="ALL" module="UmgrAll" path=""></Data>
        <Data id="ALL_TRD" module="UmgrTrd" path=""></Data>
        <Data id="Basedata" module="DmgrBasedata" rawpricePath="" industryPath="" ST="" path="" niomapprivate="true"></Data>
        <Data id="PriceLimit" module="DmgrPriceLimit" dataPath="" path=""></Data>
        <Data id="adjfactor" module="DmgrAdjfactor" dataPath="" niomapprivate="true" path=""></Data>
        <Data id="adjprice" module="DmgrAdjprice" niomapprivate="true" path=""></Data>
        <Data id="ipo" module="DmgrIPO" dataPath="" path=""></Data>
        <Data id="ashareeodprices" module="Dmgrashareeodprices" dataPath="" niomapprivate="true"></Data>
        <Data id="aindexeodprices" module="Dmgraindexeodprices" dataPath="" niomapprivate="true"></Data>
        <Data id="Dmgr_MktRet" module="/usr/local/gsim/dm_src/Dmgr_MktRet.py" niomapprivate="true"></Data>
    </Modules>
    <Portfolio id="Portfolio" booksize="20e6" homecurrency="CNY">
        <Stats module="StatsSimpleV6" mode="1" index_ret="Dmgr_MktRet.mkt_avg_ret" thres="90" 
            tradePrice="close" tax="0." fee="0." slippage="0." printStats="true" 
            dumpPnl="true" pnlDir="/nvme125/combo/combo_pnl/fguo/mode1/"></Stats>
        <Alpha id="fguo" module="AlphaLoad" universeId="ALL_TRD" alphaDir="/nvme125/combo/combo_dump/fguo" ver="v2"></Alpha>
            <Operations>
                <Operation module="AlphaOpPower" exp="1.0"></Operation>
            </Operations>
    </Portfolio>
</gsim>
```

---

## 3. TODAY 的解析语义(gsim 源码)

### 3.1 grep 结果

gsim Python 代码中 **无 `TODAY` 大写匹配**(非 .venv):

```
$ grep -rn 'TODAY' /usr/local/gsim --include='*.py'
(无结果)
```

`Calendar.py` 中有 `today()` 函数:

```
$ grep -rn -i 'today' /usr/local/gsim/gsim/ --include='*.py'
/usr/local/gsim/gsim/utils/Calendar.py:72:def today():
/usr/local/gsim/gsim/combo/combo_equal_prod.py:25:        today = uv.Dates[di]
/usr/local/gsim/gsim/combo/combo_equal.py:29:        today = uv.Dates[di]
```

### 3.2 Universe 模块是编译的 .so

```
$ find /usr/local/gsim/gsim/data -name "Universe*"
/usr/local/gsim/gsim/data/Universe.cpython-310-x86_64-linux-gnu.so
```

无 .pyx / .c 源码,只能 `strings` 分析:

```
$ strings Universe.cpython-310-x86_64-linux-gnu.so | grep -i "today\|enddate\|startdate"
startdate > enddate
endDate
enddate
endDate: 
today
TODAY
```

上下文:
```
bdays
array
TODAY
Dates
_time
```

### 3.3 Calendar.today() 全文

```python
# /usr/local/gsim/gsim/utils/Calendar.py (节选)

def today():
    '''
    current physical date
    '''
    return int(datetime.now().strftime('%Y%m%d'))
```

### 3.4 run_cp.py vs run.py (checkpoint 版本差异)

```diff
--- run.py
+++ run_cp.py
@@ +8
+from gsim import Checkpoint as checkpoint
@@ +17,18
+checkpoint.initialize(Config(root.find('Constants')))
@@ +32,36
+checkpointDays = Config(root.find('Constants')).getAttributeDefault('checkpointDays', 5)
+savedi = checkpoint.load()
@@ (main loop)
+    if di<=savedi:
+        continue
+    if di == Universe.endIndex - checkpointDays:
+        checkpoint.save(di)
```

**回答**:
- `TODAY` = **日历物理今天**(`Calendar.today()` = `datetime.now().strftime('%Y%m%d')`)。
- `TODAY-1` = 物理今天减 1 个日历日(Universe.so 内部解析偏移量)。
- 当天数据没 build 完时的行为:从日志观察,gsim **不报错也不收口** —— 若因子 dump 文件不存在会打
  `alpha file missing on day XXXXXXXX` 警告,并产出 NaN/空持仓(见 §6 zxu4 日志);
  mode1 有人为 catch 逻辑(`仅最后一天因当天因子未就位而 NaN 退出——已接住`)。
  即:**产垃圾(空仓)而非报错退出**,但有 wrapper 层(cchang 的 run_combo.sh)兜底检测。

---

## 4. checkpoint 续跑的落盘行为

### 4.1 checkpoint 文件

```
$ ls -la /nvme125/combo/checkpoint/combo_lhw/
total 53858
drwxrwxr-x 2 wbai wbai         3 Jul 14 14:27 .
-rw-rw-r-- 1 wbai wbai 129091099 Jul 14 14:27 archive.bin     # 123 MB

$ ls -la /nvme125/combo/checkpoint/combo_zxu/
-rw-rw-r-- 1 wbai wbai 19176727 Jul 14 16:39 archive.bin      # 18 MB

$ ls -la /nvme125/combo/checkpoint/combo_fguo/
-rw-rw-r-- 1 wbai wbai 986777347 Jul 17 04:13 archive.bin     # 941 MB
```

**构成**: 单个 `archive.bin` 文件,大小与因子数成正比(lhw ~700 因子 = 123MB, fguo ~5000+ 因子 = 941MB)。

### 4.2 dump 产出 mtime 分析

```
$ ls -la /nvme125/combo/combo_dump/lhw/lhw/2026/07/ | tail -15
-rw-rw-r-- 1 wbai wbai 95594 Jul 14 14:27 20260710.1
-rw-rw-r-- 1 wbai wbai 44000 Jul 14 14:27 20260710v2.npy
-rw-rw-r-- 1 wbai wbai 95594 Jul 14 14:27 20260713.1
-rw-rw-r-- 1 wbai wbai 44000 Jul 14 14:27 20260713v2.npy
-rw-rw-r-- 1 wbai wbai     0 Jul 14 14:27 20260714.1          # ← 空文件(当日数据未就位)
-rw-rw-r-- 1 wbai wbai 44000 Jul 14 14:27 20260714v2.npy

$ stat 20260710v2.npy
Modify: 2026-07-14 14:27:10.466403727 +0800
$ stat 20260713v2.npy
Modify: 2026-07-14 14:27:12.306440311 +0800
$ stat 20260714v2.npy
Modify: 2026-07-14 14:27:13.670467420 +0800
```

**所有文件 mtime = 2026-07-14 14:27** —— 历史日期的文件也在同一次运行中被重写。

fguo 更清晰(Jul 17 凌晨跑):
```
$ stat fguo/2026/06/20260630v2.npy
Modify: 2026-07-17 04:08:11.618829808 +0800    # ← 6月的文件
$ stat fguo/2026/07/20260714v2.npy
Modify: 2026-07-17 04:15:54.002804590 +0800
$ stat fguo/2026/07/20260716v2.npy
Modify: 2026-07-17 04:17:24.590942213 +0800    # ← 最新日(空仓)
```

**回答**:
- checkpoint 由单个 `archive.bin` 构成(gsim 内部序列化的全状态二进制)。
- **每次跑从 checkpoint 恢复后,会重写 checkpoint 点以后的所有 dump 文件**(不是只增一天)。
  即 `checkpointDays=5` 时,checkpoint 记录 `endIndex - 5` 的状态,续跑重新模拟最后 5+N 天并重写对应产出。
- checkpoint 损坏/缺失:删 `archive.bin` 后从头全跑(startdate 起)——无增量修复,只有"全段重跑"。
  `run_cp.py` 逻辑: `savedi = checkpoint.load()`,若加载失败则 savedi=0,所有 di 都不 skip。

---

## 5. 每日触发方式

### 5.1 crontab (user: wbai)

```
# 1. pull rawdata
00 8 * * * /production/build_cc/daily_pull.sh > /production/build_cc/sync.log

# 2. build cc
20 8 * * 1-5  ulimit -n 65535 && /usr/local/gsim/.venv/bin/python3 /usr/local/gsim/run.py /production/build_cc/config.xml > /production/build_cc/build.log && /usr/local/gsim/.venv/bin/python3 /production/build_cc/update_l2_meta.py

# 3. generate alpha
# 4. generate combo
## signals
25 8 * * 1-5 /bin/bash -c 'ulimit -n 65536; /usr/local/gsim/.venv/bin/python3 /production/signals/run.py > /production/signals/logs/$(/bin/date +\%Y\%m\%d).log 2>&1'
## jdw delay0
28 14 * * 1-5 /bin/bash -c 'ulimit -n 65536; /usr/local/gsim/.venv/bin/python3 /production/signals/jdw/run.py > /production/signals/jdw/logs/$(/bin/date +\%Y\%m\%d).log 2>&1'
```

**注意**: `# 4. generate combo` 只是注释占位,**crontab 中无 `/nvme125/combo` 相关任务**。

### 5.2 实际触发:cchang 手动/脚本

观察到当前正在运行的进程:
```
$ ps aux | grep combo
cchang  3456290  bash /home/cchang/stock_combo_product/run_combo.sh prod --author zxu
cchang  3456330  /usr/local/gsim/.venv/bin/python3 /usr/local/gsim/run_cp.py /nvme125/combo_cchang/xml/combo_zxu/mode0.xml
```

cchang 有独立的 combo 管理脚本 `/home/cchang/stock_combo_product/run_combo.sh`(无读权限)。
fguo 的 checkpoint 在 Jul 17 04:13 更新,说明有凌晨自动触发(可能是 cchang 自己的 crontab 或 tmux 守护)。

### 5.3 systemctl timers

无 combo 相关 timer:
```
$ systemctl list-timers
(仅 sysstat/dpkg-db-backup/logrotate/apt-daily 等系统定时器)
```

**回答**:
- `/nvme125/combo` 的 combo 生产 **不在 wbai 的 crontab 中**。
- 由 cchang 通过 `/home/cchang/stock_combo_product/run_combo.sh` 驱动(手动或其自有 cron)。
- 失败通知:从日志看,无告警通道,靠人工检查 /tmp 日志 + checkpoint mtime。
- `/production/signals/` 是另一套生产线(MHE/wbai 实盘信号),由 wbai cron 08:25 触发,
  **与 `/nvme125/combo` 因子增量生产无关**(signals 消费 combo_dump 但不产 alpha_dump)。

---

## 6. 单次运行成本

### 6.1 日志文件

```
$ ls /tmp/combo_*.log /tmp/combo_*.err
/tmp/combo_combo_zxu_mode0.err
/tmp/combo_lhw_run.log
/tmp/combo_run_zxu.log
/tmp/combo_run_zxu2.log
/tmp/combo_run_zxu3.log
/tmp/combo_run_zxu4.log
```

### 6.2 运行时间实测

**zxu mode0(checkpoint 续跑,~185 因子)**:
```
/tmp/combo_combo_zxu_mode0.err
Birth: 2026-07-17 20:11:19   Modify: 2026-07-17 20:18:24
→ wall time ≈ 7 分钟
```

**zxu4 全段跑(无 checkpoint,从 20180101 到 20260714,~185 因子)**:
```
/tmp/combo_run_zxu4.log
Birth: 2026-07-14 19:24:22   Modify: 2026-07-14 19:47:42
→ wall time ≈ 23 分钟
```

**lhw 全段(从 log startDate 20180101 到 endDate 20260713,~700 因子)**:
```
/tmp/combo_lhw_run.log
Birth: 2026-07-13 19:32:36   Modify: 2026-07-13 19:33:39
→ 文件修改时间差 ~1 分钟(但 log 以 crash 结束,非完整运行)
```

**fguo checkpoint 续跑(~5672 因子,checkpoint 到 endDate 约 5 天)**:
```
checkpoint/combo_fguo/archive.bin  Modify: 2026-07-17 04:13
dump 20260630v2.npy                Modify: 2026-07-17 04:08
dump 20260716v2.npy                Modify: 2026-07-17 04:17
→ wall time ≈ 9+ 分钟(从 checkpoint 恢复后重算最后约 12 天)
```

**回答**:
- checkpoint 续跑(日常增量): **7~10 分钟/产线**(因子数 185~5672 差异不大,瓶颈在 I/O + combo 优化)。
- 无 checkpoint 全段(从 2018~2026,~2000 交易日): **~23 分钟/产线**(185 因子);大因子池可能更长。
- 多条产线串行跑(lhw + zxu + fguo 各自 mode0 + mode1) 预计总耗时 ~40~60 分钟。

---

## 附录: 产线拓扑总结

```
单因子 alpha_dump (per-factor 日产)
    ↓ (AlphaLoad 读入)
mode0: 多因子 → Combo_bj202 组合优化 → combo_dump/{author}/ (per-author 日产出)
    ↓ (checkpoint 续跑,enddate=TODAY)
mode1: 读 combo_dump → StatsSimpleV6 → combo_pnl/{author}/mode1/ (PNL)
    ↓ (enddate=TODAY-1,不用 checkpoint)
combo_eq: 读三人 combo_dump → AlphaComboEqualProd 等权 → combo_pnl/combo_eq/ (最终 PNL)
    ↓ (enddate 写死,手动更新)
```

**数据路径**: `niodatapath="/nvme125/datasvc/data/cc"`,因子读取 `alphaDir="/nvme125/alpha_dump/"`(本机 sidecar)。

---

## 7. `/nvme125/alpha_dump` 身份调查(补充,2026-07-17 21:08)

### 7.1 路径身份

```
$ ls -ld /nvme125/alpha_dump /nvme125/alphalib/alpha_dump /nvme125/alphalib.local/alpha_dump
drwxrwxr-x 7589 wbai alpha-data 7589 Jul 17 18:29 /nvme125/alpha_dump
lrwxrwxrwx    1 root root         28 Jun  8 09:44 /nvme125/alphalib/alpha_dump -> ../alphalib.local/alpha_dump
drwxr-sr-x    2 root alpha-data    2 Jul 11 19:32 /nvme125/alphalib.local/alpha_dump
```

**结论**: 三个路径是**三个独立实体**:
| 路径 | 身份 | 用途 |
|---|---|---|
| `/nvme125/alpha_dump` | **独立 ZFS dataset** (`nvme125/alpha_dump`, 1.35T used, 11.6T avail) | combo 生产的因子日产出(cchang 运维) |
| `/nvme125/alphalib/alpha_dump` | JFS 软链 → `alphalib.local/alpha_dump` | ops alphalib sidecar 约定(当前**空目录**) |
| `/nvme125/alphalib.local/alpha_dump` | 本地 sidecar 实目录 | ops 设计中的 alpha_dump 落盘位(当前**空**,未投产) |

**`/nvme125/alpha_dump` 不是 JFS 的一部分**,不是 alphalib sidecar,是 cchang 独立建的 ZFS dataset,
与 ops alphalib 体系完全脱钩。combo 生产配置硬编码 `alphaDir="/nvme125/alpha_dump/"` 读它。

### 7.2 内容规模

```
$ ls /nvme125/alpha_dump | wc -l
7587

$ ls /nvme125/alpha_dump | head
AlphaCchangAmtAwareRev
AlphaCchangIntraOvnFusion
AlphaCchangLimitDynamics
AlphaCchangVwapPressure
AlphaFguo12_10
AlphaFguo12_14
AlphaFguo12_2
AlphaFguo12_4
AlphaFguo12_5
AlphaFguo12_7
```

7587 个因子目录,覆盖 fguo / lhw / zxu / cchang 四位研究员。

### 7.3 最近 dump 文件(样本)

```
$ ls -la /nvme125/alpha_dump/AlphaFguo12_10/2026/07/ | tail -5
-rw-rw-r-- 1 cchang cchang 44000 Jul 17 18:33 20260715v2.npy
-rw-rw-r-- 1 cchang cchang 44000 Jul 17 18:33 20260716v1.npy
-rw-rw-r-- 1 cchang cchang 44000 Jul 17 18:33 20260716v2.npy
-rw-rw-r-- 1 cchang cchang 44000 Jul 17 18:33 20260717v1.npy
-rw-rw-r-- 1 cchang cchang 44000 Jul 17 18:33 20260717v2.npy

$ stat /nvme125/alpha_dump/AlphaFguo12_10/2026/07/20260717v2.npy
Modify: 2026-07-17 18:33:10.944944972 +0800
Uid: (1003/cchang) Gid: (1003/cchang)
```

### 7.4 所有权统计

```
$ find /nvme125/alpha_dump -maxdepth 4 -name "20260717v2.npy" -exec stat -c "%U" {} \; | sort | uniq -c
   7530 cchang
```

**100% cchang 写入**。

### 7.5 生产时间窗

```
# 今天(Jul 17)的 dump 写入时间范围:
最早: 2026-07-17 18:33:10 (AlphaFguo12_10)
最晚: 2026-07-17 18:46:28 (AlphaZxu_260706_AftIntraSkew_delay1)
→ 全量 7530 因子在 ~13 分钟内完成
```

### 7.6 生产机制:per-factor gsim + checkpoint

```
# 配套 per-factor checkpoint 目录:
$ ls /nvme125/checkpoint/ | wc -l
7535

$ du -sh /nvme125/checkpoint/
4.2G

$ ls -la /nvme125/checkpoint/AlphaFguo12_10/
-rw-rw-r-- 1 cchang alpha-core 440823 Jul 17 18:33 archive.bin

# checkpoint 写入时间与 dump 一致 (18:33-18:46):
$ find /nvme125/checkpoint -name "archive.bin" -printf "%T+ %p\n" | sort -r | head -3
2026-07-17+18:46:28  AlphaZxu_260706_AftIntraSkew_delay1/archive.bin
2026-07-17+18:45:56  AlphaZxu_260706_NoonVolShare_delay1/archive.bin
2026-07-17+18:45:55  AlphaZxu_260706_LgTradeRetCorr_delay1/archive.bin

# alpha_pnl 同时写入:
$ stat /nvme125/alpha_pnl/AlphaFguo12_10
Modify: 2026-07-17 18:33:11.059944843 +0800
Uid: (1003/cchang) Gid: (1003/cchang)

# ZFS dataset:
$ zfs list nvme125/alpha_pnl
nvme125/alpha_pnl  1.02G  11.6T  1.02G  /nvme125/alpha_pnl
```

**机制**: cchang 的 `run_combo.sh` 脚本对每个因子并发跑 gsim (使用 `run_cp.py` = checkpoint 版),
每因子有独立 config(生产端 overwrite 了原 `alpha_src` 里的 XML,将 `enddate` 改为 `TODAY-1`、
`dumpAlphaDir` 改为 `/nvme125/alpha_dump`、`checkpointDir` 改为 `/nvme125/checkpoint/<factor>/`),
**单次增量生产 = 读 checkpoint → 续跑最后 N 天 → 产出 dump + pnl + 更新 checkpoint**。

### 7.7 谁触发 & 几点跑

```
# 当前正在运行的进程:
$ ps aux | grep combo
cchang  3456290  bash /home/cchang/stock_combo_product/run_combo.sh prod --author zxu
cchang  3456330  /usr/local/gsim/.venv/bin/python3 /usr/local/gsim/run_cp.py /nvme125/combo_cchang/xml/combo_zxu/mode0.xml

# cchang 工作环境:VS Code Remote + tmux,run_combo.sh 从 VS Code terminal 手动启动。
# 无系统级 cron。
# /home/cchang/ 权限 0750,脚本内容不可读。
```

**回答**:
1. `/nvme125/alpha_dump` 是 **cchang 运维的独立 ZFS dataset**,不是 JFS/alphalib 的一部分。
2. 每日由 cchang 通过 `/home/cchang/stock_combo_product/run_combo.sh` 脚本触发,**非自动 cron**。
3. 执行链: `run_combo.sh` → 并发 gsim `run_cp.py` (per-factor config + checkpoint) → 写 `/nvme125/alpha_dump/<factor>/` + `/nvme125/alpha_pnl/<factor>` + `/nvme125/checkpoint/<factor>/`。
4. 今天 18:33-18:46 跑完 7530 因子(~13 分钟,checkpoint 续跑)。
5. combo 在 20:11 才启动(alpha_dump 就位后手动触发)。
6. **全链条无告警、无监控**,人工目视 + mtime 检查。
