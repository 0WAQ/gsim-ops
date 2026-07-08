# 生产验证 · 第 3-4 层执行手册(金丝雀写路径 + 并发锁)

> 执行者:server-160 上的 Claude(或人工)。本手册假定第 1-2 层已通过
> (fast suite 51 passed / e2e 6 条路径全绿 / 只读冒烟通过,见 JOURNAL PV1-PV4)。
> 分支:`claude/remediation-wave0`,HEAD 须包含 `b4cf81c`(BrokenPipe 修复)。

**本层在验什么**:四个 P0 数据正确性修复在真实生产环境(真 gsim、真 JFS、真生产
PG `ops` 库)的行为 —— 用一个金丝雀因子走完整生命周期:

| 编号 | 修复 | 本手册验证点 |
|---|---|---|
| R1 | 重新入库拿不到新快照 | restage 删 snapshot 行;二次入库后 snapshot_at 换新 |
| R2 | 裸 restage = 全库召回 | `ops restage -y` 无选择器必须被拒绝 |
| R3 | cancel 删唯一源码 | 曾入库因子 restage 后 cancel 必须被拒绝且 staging 完好 |
| R4 | re-archive 踩 Errno 20 | 二次入库对单文件 pnl 不炸 NotADirectoryError |

预计总耗时 ~20-30 分钟(两次 check 各 ~2-3 分钟,e2e good 模板实测)。

---

## 安全红线(执行者必读,违反任意一条即停止)

1. **所有写操作只允许针对金丝雀因子 `AlphaWbaiCanary001`**,任何写命令必须显式带
   因子名。唯一例外是步骤 L3-3 的裸 `ops restage -y` —— 它的**预期就是被拒绝**;
   如果它没有被拒绝而是列出了因子计划/确认提示,**输入 N(或 Ctrl-C)立即终止并
   报告**,这本身就是一个严重失败结论。
2. 禁止:任何 redis 操作(它是 JFS metadata,与本验证无关)、执行
   `migrate_drop_derived.sql`、对生产 `ops` 库的任何直接 SQL 写(本手册的状态
   检查 snippet 全部只读)。
3. 发现生产代码 bug:**停止、记录原文、报告**,不要在生产机上现场改代码。
4. 任何步骤"实际"与"预期"不符 → 停止后续验证步骤,只执行"清理"章节中
   **金丝雀自己名下**的清理项,然后报告。
5. 验证窗口内其它机器不得运行 ops 写命令(锁键新旧版本不互斥;由 wbai 确认安排)。

其它须知:写命令(submit/check/restage/cancel/rm)会经 `ops/infra/sudo.py` 自动
sudo 提权;日志在 `~/.cache/ops/logs/ops.log`(提权后可能落
`/root/.cache/ops/logs/ops.log`);check 会在 repo 的 `docs/reports/check/` 落一个
报告 json,清理时删掉。

---

## 阶段 0 · 前置检查

```bash
cd ~/gsim-ops
git log --oneline -3          # 应包含 b4cf81c;分支 claude/remediation-wave0
git status -sb | head -1      # 与 origin 一致,无本地脏改动
uv sync
hostname                       # server-160
export CANARY=AlphaWbaiCanary001
export CDATE=$(date +%Y%m%d)
```

**sudo 非交互检查**(写命令会自提权,非交互 shell 里 sudo 无法读密码,验证会
卡死在 L3-1):

```bash
sudo -n /home/wbai/.local/bin/ops --help >/dev/null 2>&1 && echo NOPASSWD-OK || echo NEED-SETUP
```

若输出 `NEED-SETUP`:**停止,让 wbai 在自己的终端里配置一次**(这正是 sudo.py
文档字符串的部署建议 + roadmap 的 "sudo NOPASSWD wrapper" 待办):

```bash
# 由 wbai 本人执行(需输一次 sudo 密码);提权目标是整个 ops 入口,单行即可:
echo 'wbai ALL=(root) NOPASSWD: /home/wbai/.local/bin/ops' | sudo tee /etc/sudoers.d/wbai-ops
sudo chmod 440 /etc/sudoers.d/wbai-ops
sudo visudo -c
```

配置后重跑探测,`NOPASSWD-OK` 才继续。

基线记录(报告里要用):

```bash
uv run ops list 2>/dev/null | tail -1        # 记录 Total 因子数(基线,预期 7485 上下)
ls /tank/vault/alphalib/pnl_manual | wc -l   # manual 对比池须非空(correlation 需要对手)
```

⚠ 若 `pnl_manual` 为空:金丝雀改用 `discovery_method="automated"`(阶段 1 的
snippet 里改一处),对比池换 `pnl_automated`(后续清理路径同步换)。

确认金丝雀名下无残留(应全部无输出):

```bash
ls -d /tank/vault/alphalib/{alpha_src,alpha_dump,staging}/$CANARY 2>/dev/null
ls /tank/vault/alphalib/{alpha_pnl,pnl_manual,pnl_automated}/$CANARY 2>/dev/null
```

**状态速查 snippet**(下文多次引用,记作 `【速查】`;全部只读):

```bash
uv run python - <<'EOF'
import os
from pathlib import Path
from ops.infra.config import Config
from ops.infra.info import default_info_store
from ops.infra.snapshot import default_snapshot_store
from ops.infra.store import default_store
c = Config.load(Path("config.yaml"))  # Config.load 只收 Path,传 str 会 AttributeError
n = os.environ.get("CANARY", "AlphaWbaiCanary001")
rec = default_store(c).get(n)
info = default_info_store(c).get(n)
snap = default_snapshot_store(c).get(n)
print("state:", rec and (rec.status.value, "v%s" % rec.version,
                         "entered_at=%s" % rec.entered_at))
print("info :", info and (info.author, info.discovery_method))
print("snap :", snap and ("ret=%s" % snap.ret, "snapshot_at=%s" % snap.snapshot_at))
EOF
```

## 阶段 1 · 准备

**1a. 放宽阈值的 check 专用 config**(金丝雀过不了真业绩门槛;只放宽 correlation
门槛,路径/PG 全部保持生产 —— 这是有意的,金丝雀要走真库。F5 修复后锁是固定命名
空间,不同 config 文件也互斥,安全):

```bash
uv run python - <<'EOF'
import yaml
raw = yaml.safe_load(open("config.yaml"))
raw["checker"]["correlation"].update({
    "ret%": 1.0, "shrp": 0.1, "tvr_d0%": 500.0, "tvr_d1%": 500.0,
    "corr_threshold": 1.01,   # >1 恒走"低相关直接通过"分支
})
open("config.verify.yaml", "w").write(yaml.safe_dump(raw, allow_unicode=True))
print("wrote config.verify.yaml")
EOF
```

**1b. 金丝雀因子放进 dropbox**(直接复用 e2e 已验证能过全 stage 的 `good` 模板,
不要手抄):

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

## 阶段 2 · L3 金丝雀写路径

每步执行后:记录命令的关键输出行 + 与预期比对。**不符即停**(红线 4)。

### L3-1 · submit

```bash
uv run ops submit -u wbai -s $CDATE -f $CANARY
```

预期:`✔ AlphaWbaiCanary001 → submitted (version=1)`。
【速查】:state=(submitted, v1, entered_at=None);info=(wbai, manual);snap=None。
`ls -d /tank/vault/alphalib/staging/$CANARY` 存在,内含 meta.json。

### L3-2 · 首次 check → ACTIVE

```bash
uv run ops check -f $CANARY -c config.verify.yaml
```

预期:6 个 stage 依次过,结局 `→ lib`,`✔ 通过 : 1`(~2-3 分钟)。
【速查】:state=(active, v1, entered_at 非空)→ **记录 entered_at#1**;
snap 非 None → **记录 snapshot_at#1**(应等于 entered_at#1)。
文件落点:

```bash
ls -d /tank/vault/alphalib/{alpha_src,alpha_dump}/$CANARY      # 都在
ls /tank/vault/alphalib/{alpha_pnl,pnl_manual}/$CANARY          # 都在(pnl 分流)
ls -d /tank/vault/alphalib/staging/$CANARY 2>/dev/null          # 应不存在
```

### L3-3 · R2:裸批量 restage 必须被拒绝

```bash
uv run ops restage -y
```

预期:**报错拒绝**,输出含"批量模式必须指定 -u 和/或 -s"字样,零因子被动。
⚠ 若出现因子清单/确认提示 → 红线 1,立即终止。

### L3-4 · restage 单因子 + R1 前半(离库删快照)

```bash
uv run ops restage $CANARY -y
```

预期:`✔ ... active → submitted`。
【速查】:state=(submitted, v1, **entered_at 仍为 #1 的值,非空**);**snap=None**
(R1:离库删快照)。
文件:alpha_src/$CANARY **消失**(move 不是 copy),staging/$CANARY **出现**,
alpha_pnl/$CANARY **保留**(ACTIVE restage 默认保留产物)。

### L3-5 · R3:曾入库因子 cancel 必须被拒绝(数据安全关键)

```bash
uv run ops cancel $CANARY
```

预期:**拒绝**,输出提及曾入库(entered_at)并指引用 `ops rm` 或 `ops check`。
**关键断言**:`ls -d /tank/vault/alphalib/staging/$CANARY` **仍然存在且内容完好**
(此刻它是源码的唯一副本;老代码在这里会 rmtree 掉它)。
⚠ 若 cancel 成功执行 → 这是数据丢失级 bug,最高优先级报告。

### L3-6 · 二次 check:R4(无 Errno 20)+ R1 后半(快照换新)

```bash
uv run ops check -f $CANARY -c config.verify.yaml
```

预期:再次全过 `→ lib`,**全程无 `NotADirectoryError` / `Errno 20`**(R4:对已
存在的单文件 pnl 走 unlink)。
【速查】:state=(active, v1, entered_at#2 > entered_at#1);snap 非 None 且
**snapshot_at#2 > snapshot_at#1**(R1:新入库事件拿到新快照)。
另:翻日志看是否出现 `stale snapshot exists, replacing` —— **不出现**说明 L3-4
的删除生效(正常);**出现**说明删除失败但自愈兜住了 —— 两种都算过,但要记录是哪种。

### L3-7 · rm 清理 + 级联验证

```bash
uv run ops rm $CANARY -y
```

预期:列出全部落点并删除成功。
【速查】:state / info / snap 全为 None(FK 级联)。
文件(应全部无输出):

```bash
ls -d /tank/vault/alphalib/{alpha_src,alpha_dump,staging}/$CANARY 2>/dev/null
ls /tank/vault/alphalib/alpha_pnl/$CANARY 2>/dev/null
ls /tank/vault/alphalib/alpha_feature/$CANARY.* 2>/dev/null
```

**已知泄漏(预期内,手动补)**:rm 不清 pnl 分流副本 ——

```bash
sudo rm -f /tank/vault/alphalib/pnl_manual/$CANARY
ls /tank/vault/alphalib/pnl_manual/$CANARY 2>/dev/null   # 应无
```

`uv run ops list | tail -1` 应回到阶段 0 的基线数。

## 阶段 3 · L4 并发锁

### L4-1 · 重新 submit 金丝雀(纯新 SUBMITTED,entered_at=None)

```bash
uv run ops submit -u wbai -s $CDATE -f $CANARY
```

【速查】:state=(submitted, v1, entered_at=None)。

### L4-2 · 锁互斥:持锁时 check 必须 locked 跳过

后台持锁 120 秒,立刻在同一 shell 里跑 check:

```bash
uv run python - <<'EOF' &
import os, time
from pathlib import Path
from ops.infra.config import Config
from ops.infra.lock import factor_lock
c = Config.load(Path("config.yaml"))
n = os.environ.get("CANARY", "AlphaWbaiCanary001")
with factor_lock(n, c):
    print("[lock-holder] holding advisory lock 120s", flush=True)
    time.sleep(120)
print("[lock-holder] released", flush=True)
EOF
sleep 5   # 等持锁方就位
uv run ops check -f $CANARY -c config.verify.yaml
wait
```

预期:check **立即**返回 `🔒 已被另一个进程占用` / `⚠ 占用 : 1`,不进任何 stage;
【速查】state 仍 (submitted, entered_at=None) 未被改动。120s 后持锁方自然退出。
(这是跨进程 PG advisory lock;当前只有 160 一台新版机器,跨机版等其余机器升级后
任选两台重复本步即可。)

### L4-3 · 正向 cancel(R3 守卫的另一面:从未入库的可以撤)

```bash
uv run ops cancel $CANARY -y
```

预期:**成功**删除 staging + state + info(它从未入库,entered_at=None)。
【速查】三者全 None;`staging/$CANARY` 消失。

## 阶段 4 · 清理清单(逐项确认)

```bash
rm -f config.verify.yaml
rm -rf /mnt/storage/dropbox/wbai/$CDATE/$CANARY          # dropbox 金丝雀源
rm -f docs/reports/check/check-$CANARY-*.json            # check 报告残留
git status -sb                                           # 工作树应干净
# 最后复查金丝雀零残留(全部无输出):
ls -d /tank/vault/alphalib/{alpha_src,alpha_dump,staging}/$CANARY 2>/dev/null
ls /tank/vault/alphalib/{alpha_pnl,pnl_manual,pnl_automated}/$CANARY 2>/dev/null
```

## 阶段 5 · 报告格式

将结果写入 `docs/remediation/VERIFY-L3-L4-RESULT.md`(**不要自行 commit**,留给
wbai 审阅)并在会话里汇报。逐步一行:

| 步骤 | 命令 | 预期 | 实际(关键输出原文) | 判定 |
|---|---|---|---|---|
| L3-1 | submit | submitted v1 | … | ✅/❌ |

外加:entered_at#1/#2 与 snapshot_at#1/#2 四个时间戳、L3-6 是否出现 stale 自愈
warn、基线因子数前后对比、任何非预期输出的完整原文。全部 ✅ 即 wave0-2 生产验证
完成,可进入多机升级窗口(见 JOURNAL 遗留决断:升级期间无 in-flight check、之后
手动跑 migrate_drop_derived.sql)。
