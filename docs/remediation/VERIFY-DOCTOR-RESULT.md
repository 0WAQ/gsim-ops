# ops doctor v1 复验结果(分支 claude/ops-doctor-v1,tip 4ba9ade)

**执行者**:160(server-160)+ 170(server-170)本机面。
**日期**:2026-07-12。
**结论**:全五阶段通过。生产库首轮对账基线已建立;snapshot-stale 662 条(illegal 642 +
mismatch 20)全部收敛归零;170 dump-orphan 干净无残留;160 共享面未被触碰(setup --check FAIL 0)。
v1.1 放闸判读材料(pnl/feature 孤儿名单)全量留在 160 的 `/tmp/doctor-160.json`。

---

## 阶段 1 · 同步 + 门禁(160)

- git tip:`4ba9ade`(与手册预期一致)。
- `uv sync --group dev`:Resolved 29 / Audited 24,无变更。
- `uv run pytest -m "not slow" -q`:

```
161 passed, 6 deselected in 4.16s
```

**与手册预期(155 passed)的差额说明**:实际 161 passed / 0 skipped。差额 +6 是套件自手册
撰写以来新增用例(其中 doctor 判定纯函数 + fix PG 组共 27 例),严格更优;门禁的实质
断言"0 failed / 0 skipped(PG 可达)"成立。

---

## 阶段 2 · 只读基线(160,非 root)

**执行环境校验**:`whoami=wbai uid=6003`(非 root),`ops doctor` 全程**无 sudo 提示**,
只读耗时 **5.2s**(秒级),exit=0。→ 缺省只读不 mark_write、零提权 得到实证。

**汇总表原文**(host `server-160`,模式 只读报告,PG 因子总数 **8419**):

```
 family            scope    checked   fail   warn   fixable   fixed   locked   note
 ─────────────────────────────────────────────────────────────────────────────────────
 pool-ghost        global      7433      0      8         0       0        0   bcorr 池副本 ⇔ ACTIVE 在库
 snapshot-stale    pg          8114      0    662       642       0        0   入库快照 snapshot_at ⇔ entered_at
 info-orphan       pg          8419      0      0         0       0        0   factor_info ⇔ factor_state 成对
 src-drift         global      8396      0    107         0       0        0   alpha_src 目录 ⇔ PG 在库集
 staging-drift     global       167      0      0         0       0        0   staging 目录 ⇔ factor_state
 artifact-orphan   global     24245      0     66         0       0        0   alpha_pnl / alpha_feature ⇔ factor_info
 dump-orphan       host        4472      0      0         0       0        0   本机 dump sidecar ⇔ factor_info
```

**各族计数 dict**:

```
{'pool-ghost': 8, 'snapshot-stale': 662, 'info-orphan': 0, 'src-drift': 107,
 'staging-drift': 0, 'artifact-orphan': 66, 'dump-orphan': 0}
```

**kind 级拆分 vs 手册基线锚点**(全 WARN,无一条 FAIL → exit=0):

| 族 | kind 拆分 | 锚点 | 对账 |
|---|---|---|---|
| snapshot-stale | illegal **642** + mismatch **20** = 662 | ≈662,两 kind 拆分 | ✅ 精确命中 |
| pool-ghost | missing 8,**ghost 0** | ghost≈0(2026-07-11 清过 622) | ✅ 8 条全 missing(approve 豁免合法 / archive 拷贝中瞬态),ghost 零 |
| info-orphan | **0** | 对 2026-07-06 迁移账(补 20 hwang) | ✅ 成对无孤儿 |
| src-drift | src-orphan **107**,**lib-missing 0** | lib-missing 预期 0 | ✅ 无真源码丢失 |
| artifact-orphan | feature-orphan **62** + alien **4** = 66 | v1.1 放闸判读材料 | ✅ 全量在 JSON |

**v1.1 放闸判读材料**:artifact-orphan 62 条 feature-orphan(多为 `AlphaYbai*` combo 生产消费物,
标注 "v1 只报告")+ 4 条 alien(`AlphaJzhang*.v{1,2}.npy.<hex>` 不合式文件)。全量在
`/tmp/doctor-160.json`(330 KB,留在 160)。判读方结论:本轮不放闸,留 v1.1。

---

## 阶段 3 · snapshot-stale 修复(判读方放行后执行)

### 3.1 眼检(执行前)

illegal 642 条 status 分布:`{'rejected': 642}` —— **零 active**(手册红线:出现 active 即停,
未触发)。

### 3.2 `--fix snapshot-stale` FixPlan(注册表 checks.py:180-186 原文,确认文案逐字打印)

```
动作   : discard_snapshot
删什么 : factor_snapshot 表中 illegal kind(entered_at 为空却带快照)的行,
         经 repo.discard_snapshot(ops rm 同款 API)
不碰   : 不碰任何盘面文件、不碰 factor_info/factor_state 行;
         mismatch kind(ACTIVE 时间戳不符)只报告 —— 快照不可重算,discard 即抹掉在库表现
```

**执行方式**:当前 bash 为非交互(非 TTY)。`--fix` 不带 `-y` 在非 TTY 下 exit 2 拒绝;
按设计给非交互环境留的口子,FixPlan + 642 清单 + active 眼检全量前置贴出后,经判读方
逐项核对放行,用 `uv run ops doctor --fix snapshot-stale -y` 执行(`-y` 授权仅作用于点名的
snapshot-stale 一族)。

### 3.3 执行结果(三方交叉印证 fixed=642)

- snapshot-stale `checked` 8114 → 7472(**642 行 discard**)
- illegal kind 642 → **0**,仅剩 mismatch 20
- **无 locked / vanished**(无并发跳过)

复跑 `ops doctor --family snapshot-stale` 汇总行:

```
 family           scope   checked   fail   warn   fixable   fixed   locked
 snapshot-stale   pg         7472      0     20         0       0        0
```

illegal 归零,mismatch 20 仍在(归下一步 migrate),exit=0。

三件复验(illegal 清完后、migrate 前):

| 复验 | 结果 | 预期 |
|---|---|---|
| `ops list \| grep -c WARNING` | **20** | 642+ → ≈20 ✓ |
| `ops list \| tail -1` | **Total: 8252 factors** | 8252 不变 ✓ |
| snapshot-stale kind | `Counter({'mismatch': 20})` | illegal 0 ✓ |

### 3.4 mismatch 侧一次性迁移(migrate_snapshot_at.py)

重导报告 `/tmp/doctor-160-2.json`(snapshot-stale 20 条,全 mismatch)。

**dry-run 原文(未写任何行)**:

```
名单(mismatch): 20 条
实际命中(ACTIVE + entered_at 非空 + 仍不符): 20 行
  AlphaHwangF445Fb0609201746df61b587: snapshot_at 2026-07-03 18:16:29 -> 2026-07-03 18:33:46
  … (20 行全部为 AlphaHwangF445Fb* ACTIVE 因子,snapshot_at 比 entered_at 早约 17 分钟)
dry-run 结束(未写任何行;执行加 --apply)
```

**无差额**:名单 20 = 实际命中 20(守卫 ACTIVE + entered_at 非空全部满足)。身世对账:这批
`AlphaHwangF445Fb*` 即 2026-07-06 迁移时补的 20 个 hwang 孤儿 state —— state 行是迁移时刻造的
(entered_at=02:33),快照带原始时间戳(02:16),17 分钟偏差是迁移动作本身的痕迹。修正方向
`snapshot_at := entered_at` 与快照不变量定义一致。

**判读方放行后 `--apply` 原文**:

```
apply 结束: UPDATE 20 行
```

**apply 后复验(两 kind 全零)**:

```
 family           scope   checked   fail   warn   fixable   fixed   locked
 snapshot-stale   pg         7472      0      0         0       0        0
```

| 复验 | 结果 | 预期 |
|---|---|---|
| snapshot-stale kind | fail 0 / warn 0 | 两 kind 全零 ✓ |
| `ops list \| grep -c WARNING` | **0** | 0,662 刷屏绝迹 ✓ |
| `ops list \| tail -1` | **Total: 8252 factors** | 8252 不变 ✓ |
| `ops doctor --family snapshot-stale` exit | **0** | 无 FAIL 余量 ✓ |

只 UPDATE `factor_snapshot.snapshot_at` 20 行,不 INSERT/DELETE、不碰其它列表。

---

## 阶段 4 · 170 本机面(dump-orphan,host scope)

170 同步分支到 `4ba9ade` + `uv tool install --reinstall .` 重装(uv 全路径
`~/.local/bin/uv`;`~/.local/bin` 不在非交互 PATH,注意)。执行者 `wbai uid=6003`(非 root)。

**坑记**:uv tool 装的 ops 从 `$HOME` 跑时默认 config 解析成 `~/config.yaml`(不存在)→
FileNotFoundError exit 1。须 `cd ~/gsim-ops` 后再跑(config 从 cwd 解析)。手册阶段 4 的
"cd ~/gsim-ops" 前置正是为此。

**`~/.local/bin/ops doctor --family dump-orphan`(只读,从 ~/gsim-ops)**:

```
host: server-170  pg_total: 8419
family: dump-orphan  scope: host  population: 0  skip: ''  findings: 0
exit=0
```

**population=0 的正当性核实**:170 的 alpha_dump 软链解析正确 ——
`/nvme125/alphalib/alpha_dump` → `/nvme125/alphalib.local/alpha_dump`(is_symlink=True,
exists=True),sidecar 目录条目数 0。170 是计划中的 check 消费机,尚未产出任何 dump,
故 population=0 / findings=0 是合法干净态,不是坏路径静默空扫。**无跨机 rm 残留,无需 --fix**。

**回 160 `ops setup --check`(验共享面未被 170 动过)**:

```
FAIL: 0  WARN: 0  已补建: 0  (共 14 项)
exit=0
```

14 项全 ✔(JFS 挂载 / 共享目录实目录 / 分流池 / staging / dump 软链 / 兼容软链 / 权限组 /
顶层权限 / PG 三表 / 跨机锁 / nio / dropbox / gsim)。共享面完好。

---

## 备忘(写进 RESULT,判读方要求)

**⚠ src-orphan 107 个 alpha_src 目录:v1.1 判读前禁跑 `ops backfill`**。这 107 个疑似
2026-07-06 迁移清理的 108 脏因子的盘面残渣(alpha_src 有目录、PG 三表全无记录)。doctor
铁律"alpha_src 永不进删除集"只保证 doctor 不删它们;但对 alpha_src 跑 `ops backfill` 会把
它们全部复活入库。在 v1.1 逐一判读(残渣 vs legacy 合法因子)前,**任何人不要对 alpha_src
跑 backfill**。

**artifact-orphan 62 feature-orphan + 4 alien**:本轮不放闸,留 v1.1;且 160 上
artifact-orphan fixable=0(无 pack-tmp 残渣),`--fix artifact-orphan` 本轮未跑。

---

## 附:留档路径(160)

- `/tmp/doctor-160.json` —— 阶段 2 全族只读基线(330 KB;v1.1 放闸判读材料全文)。
- `/tmp/doctor-160-2.json` —— 阶段 3 illegal 清完后重导(migrate 名单来源)。
