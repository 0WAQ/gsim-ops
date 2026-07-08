# 生产验证 · PV7 专项(自鬼影回收 + 自名过滤)

**验证目标**:`ops restage` 后在**生产相关性阈值(corr_threshold=0.7)**下 re-check,
correlation 阶段不再撞上自己的旧 pnl(自鬼影必拒,JOURNAL PV7)。此前 L3-L4 验证
用 `corr_threshold=1.01` 恒走"低相关直接通过"分支,没有覆盖这个路径。

两个验证点:
- **A(主修)**:restage 回收 check 面产物(`alpha_pnl/<name>` + bcorr 池副本),
  服务面(dump/feature)保留;随后生产阈值 re-check 通过 correlation。
- **B(双保险)**:手工把旧 pnl 塞回对比池(模拟回收失败的残留),correlation
  的**自名过滤**仍然不把自己列为竞品。

**前提**:server-160 已部署含 PV7 的分支(`claude/remediation-wave0` @ `2eb53fe`
或更新;wave3/stage-table 亦可,行为相同)。**不依赖多机升级**,单机即可执行。
执行窗口内其它机器不得运行 ops 写命令。

**红线**:沿用 `VERIFY-L3-L4.md` 全部红线 —— 所有写操作只允许针对金丝雀
`AlphaWbaiCanary001`;任何一步实际输出与预期不符**立即停止并报告**,不要自行修复;
不对生产 PG 做直接 SQL 写;不碰 redis/sentinel。

---

## 阶段 0 · 前置

```bash
cd ~/gsim-ops
git fetch origin claude/remediation-wave0 && git log --oneline -3   # 应含 2eb53fe
git status -sb | head -1
uv sync
hostname                       # server-160
export CANARY=AlphaWbaiCanary001
export CDATE=$(date +%Y%m%d)
sudo -n /home/wbai/.local/bin/ops --help >/dev/null 2>&1 && echo NOPASSWD-OK || echo NEED-SETUP
```

`NEED-SETUP` 则按 `VERIFY-L3-L4.md` 阶段 0 的 sudoers 两行配置(160 上 2026-07-08
已配过,预期 `NOPASSWD-OK`)。

金丝雀名下无残留(应全部无输出;L3-L4 收官清理后应为零):

```bash
ls -d /tank/vault/alphalib/{alpha_src,alpha_dump,staging}/$CANARY 2>/dev/null
ls /tank/vault/alphalib/{alpha_pnl,pnl_manual,pnl_automated}/$CANARY 2>/dev/null
ls /tank/vault/alphalib/alpha_feature/$CANARY.*.npy 2>/dev/null
```

基线:`uv run ops list 2>/dev/null | tail -1` 记录 Total(结束后须一致)。

`【速查】` 状态 snippet 与 `VERIFY-L3-L4.md` 阶段 0 完全相同(读 state/info/snap 三表),
下文直接引用。

**两份 config**(与 L3-L4 的唯一区别:pv7 版 `corr_threshold` 用生产值 0.7;
业绩门槛仍放宽 —— 金丝雀过不了真业绩门槛,本专项验证点只在 correlation):

```bash
uv run python - <<'EOF'
import yaml
for fname, corr in (("config.verify.yaml", 1.01), ("config.verify-pv7.yaml", 0.7)):
    raw = yaml.safe_load(open("config.yaml"))
    raw["checker"]["correlation"].update({
        "ret%": 1.0, "shrp": 0.1, "tvr_d0%": 500.0, "tvr_d1%": 500.0,
        "corr_threshold": corr,
    })
    open(fname, "w").write(yaml.safe_dump(raw, allow_unicode=True))
    print("wrote", fname, "corr_threshold =", corr)
EOF
```

**金丝雀放进 dropbox**(L3-L4 收官时已删,重建;复用 e2e `good` 模板):

```bash
uv run python - <<'EOF'
import os, sys
from pathlib import Path
sys.path.insert(0, "tests/e2e")
import conftest as e2e
name = os.environ.get("CANARY", "AlphaWbaiCanary001")
date = os.environ.get("CDATE")
py_tpl, delay = e2e._TEMPLATES["good"]
d = Path("/mnt/storage/dropbox/wbai") / date / name
d.mkdir(parents=True, exist_ok=True)
(d / f"{name}.py").write_text(py_tpl.format(name=name))
(d / f"Config.{name}.xml").write_text(e2e._xml(name, delay))  # discovery_method 缺省 manual
print("canary at", d)
EOF
```

---

## 阶段 1 · PV7-1 入库(前置,非验证点)

```bash
uv run ops submit -u wbai -s $CDATE -f $CANARY
uv run ops check -f $CANARY -c config.verify.yaml     # corr=1.01,快速入库
```

预期:ACTIVE。【速查】state=(active, v1, entered_at 有值);snap 有值。

产物基线断言(全部存在):

```bash
ls /tank/vault/alphalib/alpha_pnl/$CANARY /tank/vault/alphalib/pnl_manual/$CANARY
ls -d /tank/vault/alphalib/alpha_dump/$CANARY
ls /tank/vault/alphalib/alpha_feature/$CANARY.*.npy
```

留存旧 pnl 副本(PV7-4 双保险要用):

```bash
cp /tank/vault/alphalib/alpha_pnl/$CANARY /tmp/pv7-pnl-old
```

## 阶段 2 · PV7-2 restage 回收断言(验证点 A 前半)

```bash
uv run ops restage $CANARY -y
```

预期输出**必须包含**(顺序不限):

```
✔ 已回收 alpha_pnl/AlphaWbaiCanary001
✔ 已回收 pnl_manual/AlphaWbaiCanary001
```

且**不得出现** `已删除 alpha_dump`(默认无 --purge,服务面保留)。

文件断言:

```bash
# check 面已回收(应无输出):
ls /tank/vault/alphalib/{alpha_pnl,pnl_manual,pnl_automated}/$CANARY 2>/dev/null
# 服务面保留(应存在):
ls -d /tank/vault/alphalib/alpha_dump/$CANARY
ls /tank/vault/alphalib/alpha_feature/$CANARY.*.npy
```

【速查】state=(submitted, v1);snap=None(离库删快照,R1)。

## 阶段 3 · PV7-3 生产阈值 re-check(验证点 A 后半)

```bash
uv run ops check -f $CANARY -c config.verify-pv7.yaml
```

**判读规则**(这一步是本专项的核心,严格按下表):

| 结果 | 判定 |
|---|---|
| 7 stage 全过 → ACTIVE,correlation 低相关直接通过 | ✅ 理想结果 |
| correlation 走了"打败竞品"分支,竞品是**别的**因子,最终过/不过均可 | ✅ 验证点通过(没撞自己);记录竞品名与 corr 原文 |
| correlation 失败且失败详情里竞品名 = `$CANARY` 自己 | ❌ **PV7 回归,立即停止报告** |
| 其它 stage 失败 | ❌ 停止报告(与 PV7 无关的新问题) |

金丝雀是独特假因子,预期与库内真因子低相关;但若碰巧与某真因子 corr≥0.7 被拒,
验证点仍算通过 —— 本专项验证的是"不和自己比",不是"必须入库"。被拒时因子为
REJECTED,下一步照常进行(restage 同样支持 REJECTED 召回)。

【速查】若入库成功:state=(active, v1, 新 entered_at);snap 有值且
snapshot_at = 新 entered_at(快照重建)。池副本重新出现:
`ls /tank/vault/alphalib/pnl_manual/$CANARY` 存在。

## 阶段 4 · PV7-4 双保险(验证点 B:自名过滤)

模拟"回收失败留下残留"的场景 —— 手工把旧 pnl 塞回对比池,验证 correlation
即使看到同名池文件也不把自己列为竞品。

```bash
# 1. 再次召回(ACTIVE 或 REJECTED 都支持;输出应再次出现"已回收"两行)
uv run ops restage $CANARY -y

# 2. 手工塞回旧 pnl(模拟 unlink 失败残留)。⚠ 池目录 root-only,需要 root 写:
sudo cp /tmp/pv7-pnl-old /tank/vault/alphalib/pnl_manual/$CANARY
```

若执行环境 sudo 只放行了 ops 入口(NOPASSWD 仅限 `/home/wbai/.local/bin/ops`),
第 2 行会要密码:**停下请 wbai 在自己终端执行这一行**,确认
`ls /tank/vault/alphalib/pnl_manual/$CANARY` 存在后继续。

```bash
# 3. 生产阈值 re-check:
uv run ops check -f $CANARY -c config.verify-pv7.yaml
```

预期:结果与 PV7-3 **完全一致**(同为通过,或同为被同一个真竞品挡)。
correlation 日志/失败详情里**不得出现** `$CANARY` 自己作为竞品或最大相关因子
—— 出现即自名过滤回归 ❌。

check 通过时 archive 会重新拷贝池副本覆盖残留,无需手工清理。

## 阶段 5 · 清理(逐项确认)

```bash
uv run ops rm $CANARY -y          # 级联清 src/pnl/dump/feature/池副本 + PG 三表
rm -f /tmp/pv7-pnl-old config.verify.yaml config.verify-pv7.yaml
rm -rf /mnt/storage/dropbox/wbai/$CDATE/$CANARY
rm -f docs/reports/check/check-$CANARY-*.json
git status -sb                    # 工作树应干净
# 零残留复查(全部无输出):
ls -d /tank/vault/alphalib/{alpha_src,alpha_dump,staging}/$CANARY 2>/dev/null
ls /tank/vault/alphalib/{alpha_pnl,pnl_manual,pnl_automated}/$CANARY 2>/dev/null
ls /tank/vault/alphalib/alpha_feature/$CANARY.*.npy 2>/dev/null
uv run ops list 2>/dev/null | tail -1   # Total 与基线一致
```

## 阶段 6 · 报告

结果写入 `docs/remediation/VERIFY-PV7-RESULT.md`(**不要自行 commit**,留给 wbai
审阅)并在会话里汇报。逐步一行(步骤/命令/预期/实际关键输出原文/判定),外加:

- PV7-2 与 PV7-4 第 1 步的"已回收"输出原文;
- PV7-3 与 PV7-4 的 correlation 关键日志原文(max_corr、竞品名 —— 无论过没过);
- 基线 Total 前后对比;
- 任何非预期输出的完整原文。

全部 ✅ 即 PV7 行为级验证完成(自动化断言已由 160 fast suite 覆盖,本专项补的
是"真 gsim + 真库 + 生产阈值"的端到端路径)。
