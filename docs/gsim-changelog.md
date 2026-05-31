# Gsim 更新日志

## 2026-05-28

### FeatureReader (`AlphaLoadFeat`) — alpha_feature 加载支持

`gsim/alpha/__init__.py` 新增导出 `AlphaLoadFeat`，源码位于 `gsim/alpha/module/alpha_load_feature.py`。

#### 背景

**原有方式**（通过 alpha_dump 加载，`AlphaLoad`）:
- 因子值组织为 `yyyy/mm/yyyymmdd{v1,v2}.npy`
- 20100101-20251231 约 140 个文件夹，5400 个文件/因子
- 3000 个因子 = 42 万文件夹，1600 万文件
- 存储和传输都非常低效

**新方式**（通过 alpha_feature 加载，`AlphaLoadFeat`）:
- 每个因子聚合为单个 `.npy` 文件
- 路径模式：`{featDir}/{alphaId}.{ver}.npy`
- 大幅减少文件数量，提升存储和传输效率

#### `AlphaLoadFeat` 实现细节

源码 `gsim/alpha/module/alpha_load_feature.py`：

```python
class AlphaLoadFeat(AlphaBase):
    def __init__(self, cfg):
        AlphaBase.__init__(self, cfg)
        self.alphaId = cfg.getAttributeString('id')
        self.valid = dr.getData(cfg.getAttributeString('universeId'))
        self.ver = cfg.getAttributeStringDefault('ver', '1')
        self.lag = cfg.getAttributeDefault('lag', 0)
        self.demean = cfg.getAttributeDefault('demean', True)
        self.intraMode = cfg.getAttributeDefault('intraday', False)
        self.featDir = Path(cfg.getAttributeStringDefault('featDir', 'feats'))
        self.featPath = self.featDir / f"{self.alphaId}.{self.ver}.npy"
        
        if not self.featPath.exists():
            print(f'feature {self.alphaId} missing')
            return
        
        assert 5484 == len(uv.Instruments), "len(uv.Instruments) must equal to 5484"

    def generate(self, di):
        feat = np.memmap(self.featPath, mode='r', dtype=np.float64,
                         shape=(di - self.lag, len(uv.Instruments)))
        alpha = feat[-1]
        common_col = min(len(alpha), len(uv.Instruments))
        self.alpha[:common_col] = alpha[:common_col]
        self.alpha[~self.valid[di]] = np.nan
        if self.demean:
            self.alpha[:] -= np.nanmean(self.alpha)
```

#### XML 配置

```xml
<Alpha id="AlphaFromFeature" module="AlphaLoadFeat"
    universeId="ALL_TRD"
    featDir="/mnt/storage/alphalib/alpha_feature"
    ver="1" lag="0" demean="true">
    <Description name="AlphaFromFeature" author="wbai" birthday="20240101"
        universe="ALL_TRD" category="loaded" delay="1"/>
</Alpha>
```

#### 参数说明

| 参数 | 默认 | 说明 |
|-----|------|------|
| `featDir` | `feats` | feature 文件根目录 |
| `ver` | `'1'` | 版本号（决定加载 `{id}.{ver}.npy`） |
| `lag` | `0` | 时间延迟偏移 |
| `demean` | `true` | 是否减去当日均值 |
| `intraday` | `false` | 日内模式标记（暂未实现 generate_ti） |

#### 实现要点

- 使用 `np.memmap` 内存映射加载，避免一次性读入全部数据
- 数据形状假设为 `(n_dates, 5484)`，`5484` 是 A 股全市场标的数（硬编码）
- 文件不存在时静默返回（不抛错），但 `generate` 调用会失败
- `~self.valid[di]` 掩码确保无效标的为 NaN
- `demean=true` 在 combo 中常用，避免不同因子的均值偏移

#### 在 Combo 中使用

`combo_src/` 下的因子组合配置应优先使用 `AlphaLoadFeat`：

```xml
<Modules>
    <Combo id="MyCombo" module="/path/to/MyCombo.py"/>
</Modules>

<Portfolio id="MyPort" booksize="20e6" homecurrency="CNY">
    <Stats module="StatsSimple" .../>
    
    <Alphas id="MyComboGroup" combo="MyCombo">
        <Description name="MyComboGroup" author="wbai" birthday="20240101"
            universe="ALL_TRD" category="combo" delay="1"/>
        
        <Alpha id="AlphaA" module="AlphaLoadFeat" universeId="ALL_TRD"
            featDir="/mnt/storage/alphalib/alpha_feature" ver="1">
            <Description .../>
        </Alpha>
        <Alpha id="AlphaB" module="AlphaLoadFeat" universeId="ALL_TRD"
            featDir="/mnt/storage/alphalib/alpha_feature" ver="1">
            <Description .../>
        </Alpha>
    </Alphas>
</Portfolio>
```

#### 影响

- **alpha_dump 逐步弃用**: 新建 combo 推荐使用 `AlphaLoadFeat`
- **ops pack**: ops 已实现 `ops pack` 命令，将 `alpha_dump` 聚合为 `alpha_feature`
- **ops sync**: alpha_dump 已降级为纯本地中间产物，sync 不再传输

#### 待实现

`ops pack` 增量模式（见 `.claude/plans.md`）：
- `ops pack --date YYYYMMDD`
- PACK_L 动态化
- 并发安全

---

## 参考资料

- Gsim 架构：[gsim-architecture.md](gsim-architecture.md)
- XML 配置（含 FeatureReader 节）：[gsim-xml-config.md](gsim-xml-config.md#featurereaderalphaloadfeat)
- ops pack 实现：`ops/services/pack/`
- ops sync 设计：`ops/services/sync/`
