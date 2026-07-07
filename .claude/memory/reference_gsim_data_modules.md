---
name: reference-gsim-data-modules
description: "gsim 数据 module 模板 (Dmgr/Umgr 继承 DataManagerMapped), NIO_MATRIX=memmap 套壳, tag 扁平命名空间, level2 read-only adapter 模式, 源码在 source_ref/ + dm_src/"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 7ff88a50-5f38-42d3-a39c-f97ddef36c12
---

# gsim 数据 module 模板

gsim 整套数据层的核心抽象 (cc 写 / 读 / dm 派生 / level2 adapter 全在这一个模板下)。底层 memmap + .meta 物理细节见 [[reference-cc-all-data-layout]] 和 [[reference-company-data-architecture]]。

## 两个源码目录

| 目录 | 角色 | 数量 (2026-06) |
|---|---|---|
| `/usr/local/gsim/source_ref/` | rawdata → cc 转换 module + 一些 universe 描述符 | ~62 个 .py |
| `/usr/local/gsim/dm_src/` | cc → dm 派生 + level2 read-only adapter | ~43 个 .py |

物理隔离, 但**框架是同一套**, 都继承 `gsim.data.DataManagerMapped`。

gsim 自带的 builtin module 在 `gsim/data/module/` (`umgr_full.py`, `umgr_gim.py`, `ipo.py`, `price_limit.py` 等), 在 XML 里可以裸 class 名引用。

## 命名前缀约定 (历史包袱, 不规范)

`Dmgr<Author>_<thing>.py` / `Umgr<x>.py` / `umgr_<x>.py` / `interval_<x>.py` / `signal_<x>.py` / `Dmgr_<thing>.py` 各种风格混存。前缀作者**不一定是 module 作者**, 可能是 feature 算法设计者 (例: `DmgrLhw_L2FeatureCuts1430.py` 实际是 wbai 写的 adapter, lhw 设计了 feature)。已被研究员大量引用, 不会改名。

**不能从命名猜任何东西, 唯一可靠的是文件内容**。

## 标准 module 模板

继承 `gsim.data.DataManagerMapped`, 文件名 stem ≈ class 名 (runtime 动态 import 后按这个 convention 取 class):

```python
from gsim.utils.NioData import *
from gsim.data import DataManagerMapped, DataRegistry as dr, Universe as uv

class DmgrXxx(DataManagerMapped):
    def __init__(self):
        DataManagerMapped.__init__(self)
        self.matrix = NIO_MATRIX()              # 容器, 内部是 np.memmap

    def initialize(self, id, path, cfg):
        DataManagerMapped.initialize(self, id, path, cfg)
        self.dataPath = cfg.getAttributeString('dataPath')
        self.addDailyData(self.matrix, "Xxx.field_name")   # 注册 tag

    def loadData(self, di_start):                # 或 loadDay(self, di)
        for di in range(di_start, len(uv.Dates)):
            ...
            self.matrix[di, ii] = value
```

## 几条要点

### 1. 读 / 写由 XML `niomapprivate` 决定, 不是 module 自己

```
<Data niomapprivate="true"  ...>   → dataloader 路径, load* 不被调用
<Data niomapprivate="false" ...>   → data-writer 路径, runtime 调 loadData/loadDay
```

`loadData(di_start)` vs `loadDay(di)` 两个签名共存, 哪个有哪个空都行, runtime 自己挑。**同一个 module 文件被读写两侧共用**, 配 XML 时换 flag。

### 2. NIO_MATRIX = `np.memmap` 套壳

- `nio_matrix.data` 属性就是裸 `np.memmap`
- NioData 封装不太干净, **研究员习惯 `dr.getData('xxx').data` 直接拿 memmap 用**, 不走任何 NioData 接口
- 含义: 不能假设大家走规范接口; `.data` 是事实标准

### 3. tag 是**扁平 namespace**, 物理目录被忽略

```
物理: cc_all/aindexeodprices/aindexeodprices.s_dq_close_000001.npy
逻辑: dr.getData('aindexeodprices.s_dq_close_000001')   ← 只看文件名 stem
```

gsim 把 `cc_all/<dir>/` 这一层 flat 掉, 全平面共享一个 tag namespace。所以 `addDailyData(matrix, tag)` 里的 tag 必须**手工加 prefix**, 不然撞名。

- wbai 写的 module 都有 prefix (`AIndexCSI500Weight.weight`, `aindexeodprices.s_dq_close_000001`)
- 逸飞早期不知道这套规则, level2 .npy 全裸名 (`buy_amt_h4.npy`), 所以大量软链 + 改名兜底
- adapter module 在 `dr.registerData(mid, matrix, self.tag + "." + stem)` 那一行**人为补 prefix**

### 4. ops 那条铁律的工程理由

CLAUDE.md "不信 XML `<Data>` 声明, 要解析 Python 里 `dr.getData('xxx')`" — 现在我懂为啥:
- XML `<Data id="X">` 只声明 id (粗粒度), 真正的 feature tag 是 module 内部 `addDailyData(matrix, "X.field1")` 决定的, 一个 id 可能注册一堆细粒度 tag
- XML 字段名 typo (`nioimapprivate` 见过, 静默 fallback to Constants 默认值) 让 XML 不可信
- 注释掉的 `<Data>` 块在 XML 里照样存在
- 只有 `dr.getData()` 调用是真触达数据的地方

## level2 read-only adapter 模式 (特例)

例: `/usr/local/gsim/dm_src/DmgrLhw_L2FeatureCuts1430.py`

```python
class DmgrLhw_L2FeatureCuts1430(DataManagerMapped):
    def initialize(self, id, path, cfg):
        files = self._discover_files(roots)             # 扫 .npy
        for stem, filename in sorted(files.items()):
            matrix = _ReadonlyRawFloat64Matrix(filename, n_inst)  # 自定义 memmap 套壳
            dr.registerData(self.mid, matrix, self.tag + "." + stem)  # 手工补 tag

    def loadData(self, di_start): return    # 空
    def loadDay(self, di):        return    # 空
```

要点:
- `initialize` 扫一层物理目录, 给每个 `.npy` 注册 tag (补 prefix), 用自己写的 `_ReadonlyRawFloat64Matrix(np.memmap)` 包一下
- `loadData` / `loadDay` 是空的 (数据已经存在, 不用 writer 算)
- shape 直接从 `file_size / row_bytes` 反推, **不依赖 .meta** —— 这是 `feature_cuts_1430` 这种 yifei 直出的特例
- 大多数 level2 子目录 (`fguo_*`, `sli_*`, `zzk_*` 等) 仍然有标准 `.meta`, 走正常 reader 路径

## 物理目录布局对应

```
cc_all/
├── <DmgrXxx_dir>/         一个 module 一个目录
│   ├── .meta              三件套: cutoff date + dateCapacity + instrumentCapacity
│   └── <tag>.<field>.npy  手工 prefix 的 .npy 文件 (多个)
│
└── cn_equity_feature/     level2 多包一层
    ├── fguo_0105/
    │   ├── .meta          标准三件套
    │   └── *.npy
    ├── feature_cuts_1430/ yifei 特例
    │   └── *.npy          无 .meta, 无 prefix
    └── ...
```

## 还不确定的点 (低优先)

- 同一个 .py module 能否在多个 `<Data>` 里实例化 — NIO_MATRIX 是 instance 属性, 但 `addDailyData(tag)` 是写死的, 撞 tag 会怎样不知道。 设计盲区, 没人这么用
- writer 跑增量是 mmap append 一行还是重写整文件 — 看起来是 append, NIO_MATRIX 是否支持原地 grow 不明
- runtime 从 .py 文件挑 class 的具体机制 — 八成是 stem ≈ class 名, 没验证, 不关键

相关:
- [[reference-company-data-architecture]] — 三层架构 + owner 分工
- [[reference-cc-all-data-layout]] — cc_all 物理 shape / 字段清单
- [[reference-gsim-xml-config]] — XML 怎么把 module 串起来
- [[gsim-architecture]] — gsim 整体目录 / 工具链
