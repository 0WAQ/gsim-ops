# 多机升级窗口 · server-150 / intel-workstation-144 执行手册

> **已执行完毕(2026-07-08,RESULT 全绿)。实测勘误,供本手册复用时参考**:
> ① 150/144 生产入口是 uv tool install 的 `ops`(PATH),项目 venv 不生成
> console script,凡 `uv run ops xxx` 应作 `ops xxx`(pytest/python snippet
> 仍走 `uv run`);② 1-6 的 `ops list --author` 应为 `ops list -u`;
> ③ 144(WAN)`uv sync` 需 `UV_HTTP_TIMEOUT=180`。

**目标**:把 150(北京 IDC)和 144(本地办公网,WAN 节点)升级到与 160 一致的
`claude/remediation-wave0`(含 Waves 0-2 + 全部 R/W/T/F/V/PV 修复),验证跨机
一致性与**跨机 PG advisory 锁互斥**,最后在 160 上手动执行
`migrate_drop_derived.sql` 清理僵尸表。

**为什么要一个窗口**:锁键在 F5 修复中从 `hashtext(library_id)` 换成固定命名
空间 `hashtext('ops:factor_lock')` —— **新旧版本的 ops 互不互斥**。窗口从第一台
机器开始升级起,到阶段 3 锁验证通过止,期间任何机器都不得跑 ops 写命令。

**执行方式**:阶段 1/2 分别在 150/144 上执行(各机的执行者 Claude 或 wbai 本人),
阶段 3 需要 160 与 150/144 配合,阶段 4 只在 160。每步实际输出与预期不符
**立即停止并报告**,不要自行修复。

## 红线

1. **窗口内任何机器不得运行 ops 写命令**(submit/check/restage/rm/approve/
   cancel/clear/pack/backfill/run)。唯一例外:阶段 3 的锁验证 snippet(只拿
   advisory 锁,不写任何数据)。
2. **不把 `.env` 密码内容打印到会话或报告里**(文件级拷贝,不 cat)。
3. 阶段 4 执行 migration 前 **必须先 `backup.sh` 成功**。
4. 不碰 redis / sentinel(它是 JFS metadata 后端,停了 = 因子库挂掉)。Redis
   残留 state key 的清理**不在本窗口**(验稳后单独做,只 DEL state:*,绝不
   FLUSHDB)。
5. 144 是跨段 WAN 节点:命令比 IDC 慢是**预期**,记录耗时即可;不要因为慢就
   重试写操作或并行跑。

---

## 阶段 0 · 全局前置(窗口开启)

**0a. 确认无 in-flight ops**(三台机器各跑;150/144 上若有旧版 ops 同样适用):

```bash
pgrep -af "ops (check|submit|restage|rm|approve|cancel|clear|pack|backfill|run)" || echo NO-INFLIGHT
crontab -l 2>/dev/null | grep -i ops || echo NO-CRON
```

两条都干净才继续。若 crontab 有 ops 条目:先注释掉,报告里记录,窗口结束后恢复。

**0b. 160 基线**(读一次,后面各机对照):

```bash
cd ~/gsim-ops && git log --oneline -1      # 记录 160 当前部署 rev(应为 85b590e 或更新)
uv run ops list 2>/dev/null | tail -1      # 记录 Total(基线)
```

---

## 阶段 1 · server-150 升级

### 1-1 环境探测(只读,全部记入报告)

```bash
hostname && ip addr | grep 10.9.100.150
ls -d ~/gsim-ops 2>/dev/null && cd ~/gsim-ops && git log --oneline -1 && git remote -v  # 旧 clone 现状
uv --version || echo NO-UV
df -h | grep -E "vault|tank" ; ls /tank/vault/alphalib/ | head   # JFS 挂载
ls -d /usr/local/gsim 2>/dev/null || echo NO-GSIM                # 只记录,本窗口不需要
ls /datasvc/data/cc_2025 >/dev/null 2>&1 && echo DATASVC-OK
```

`NO-UV` 则停止报告(装 uv 由 wbai 决定)。**repo 必须位于 `/home/wbai/gsim-ops`**
(config.yaml 的 `password_file` 是绝对路径);不在则报告,不要自作主张挪。

### 1-2 代码升级

```bash
cd ~/gsim-ops
git status -sb                 # 有本地脏改动则停止报告
git fetch origin claude/remediation-wave0
git checkout claude/remediation-wave0
git pull origin claude/remediation-wave0
git log --oneline -1           # 必须与 160 基线 rev 一致
uv sync --group dev
```

(若机器上没有 clone:`git clone <与 160 相同的 origin url> ~/gsim-ops` 后同上。)

### 1-3 PG 密码分发

`scripts/postgres/.env` 是 gitignore 的,clone 里没有。从 160 拷贝(ssh 免密可
执行者自己跑,否则请 wbai 代跑):

```bash
scp wbai@10.9.100.160:/home/wbai/gsim-ops/scripts/postgres/.env ~/gsim-ops/scripts/postgres/.env
chmod 600 ~/gsim-ops/scripts/postgres/.env
ls -l ~/gsim-ops/scripts/postgres/.env    # 确认存在 + 600;不要 cat
```

### 1-4 安装 ops 入口 + sudoers

```bash
cd ~/gsim-ops && uv tool install --editable . --force
ls -l /home/wbai/.local/bin/ops           # sudoers 引用的就是这个路径
sudo -n /home/wbai/.local/bin/ops --help >/dev/null 2>&1 && echo NOPASSWD-OK || echo NEED-SETUP
```

`NEED-SETUP` 则由 **wbai 本人**执行(与 160 完全相同的两行;缺第二行 sudo 会
拒绝 `--preserve-env`,160 实测过):

```bash
sudo tee /etc/sudoers.d/wbai-ops >/dev/null <<'SUDOERS'
wbai ALL=(root) NOPASSWD: /home/wbai/.local/bin/ops
Defaults!/home/wbai/.local/bin/ops env_keep += "OPS_CONFIG OPS_GSIM_HOME OPS_STORAGE OPS_WORKSPACE OPS_ALPHALIB_ROOT"
SUDOERS
sudo chmod 440 /etc/sudoers.d/wbai-ops
sudo visudo -c
```

### 1-5 L1 自动化测试

⚠ PG 组测试打 160 的 `ops_test` 库,`wipe_test_db` 会清库 —— **不得与其它机器
同时跑测试**(与 160、与 144 都要错开)。

```bash
cd ~/gsim-ops && uv run pytest -m "not slow" -q
```

预期:与 160 相同量级(51+ passed / 少量 skip / 0 failed)。e2e **不跑**
(需要 gsim + cc 数据,不在本窗口范围)。

### 1-6 L2 只读冒烟

```bash
uv run ops list 2>/dev/null | tail -1        # Total 必须与 160 基线一致(同一 PG)
uv run ops list --author wbai | head -20
uv run ops status | head -20
uv run ops info <从上面 list 里任选一个真因子>
uv run ops list --format json 2>/dev/null | head -5   # 无 traceback(BrokenPipe 修复,exit 141 正常)
```

判定:Total 一致(state 单一真相源,各机读同一 PG —— 这正是验证点)+ 无异常输出。

---

## 阶段 2 · intel-workstation-144 升级(WAN 节点)

与阶段 1 相同流程,**外加两处差异**:

### 2-0 挂载点差异(先于一切)

144 的 JFS 挂载点是 `/storage/vault/alphalib`(不是 `/tank/vault`)。探测:

```bash
ls /storage/vault/alphalib/ | head
readlink -f /mnt/storage/alphalib 2>/dev/null   # 各机软链习惯,记录实际指向
```

配置覆盖走 `OPS_ALPHALIB_ROOT` 环境变量(`Config._resolve_vars` 会用
`OPS_<VAR>` 覆盖 config.yaml 的 `vars` 块;sudoers 第二行的 env_keep 已包含它):

```bash
grep -q OPS_ALPHALIB_ROOT ~/.bashrc || echo 'export OPS_ALPHALIB_ROOT=/storage/vault/alphalib' >> ~/.bashrc
export OPS_ALPHALIB_ROOT=/storage/vault/alphalib
```

之后 1-1 ~ 1-6 照做,凡 `/tank/vault/alphalib` 处换成 `/storage/vault/alphalib`。

### 2-x WAN 注意

- 1-5 测试与 1-6 list 耗时会明显高于 IDC(PG 与 JFS 都跨段)——记录耗时,
  慢不算失败;
- 若 PG 连接超时类报错:记录完整原文停止报告(可能要调连接超时,不要自行改代码)。

---

## 阶段 3 · 跨机锁验证(升级完成的判定性验证)

新锁键(F5)下三机第一次真正互斥。用金丝雀名字拿锁(advisory 锁按名字哈希,
不要求因子存在,不写任何数据)。

**3-1. 160 上持锁 120s**(窗口 A):

```bash
cd ~/gsim-ops && uv run python - <<'EOF'
import time
from pathlib import Path
from ops.infra.config import Config
from ops.infra.lock import factor_lock
c = Config.load(Path("config.yaml"))
with factor_lock("AlphaWbaiCanary001", c):
    print("LOCK HELD on 160, sleeping 120s", flush=True)
    time.sleep(120)
print("RELEASED", flush=True)
EOF
```

**3-2. 持锁期间,150 上尝试**(144 同款再来一遍):

```bash
cd ~/gsim-ops && uv run python - <<'EOF'
from pathlib import Path
from ops.infra.config import Config
from ops.infra.lock import factor_lock, FactorLocked
c = Config.load(Path("config.yaml"))
try:
    with factor_lock("AlphaWbaiCanary001", c):
        print("ACQUIRED — 不应该发生,跨机互斥失效!")
except FactorLocked:
    print("FactorLocked — 预期,跨机互斥生效")
EOF
```

预期:持锁期间 150/144 都打印 `FactorLocked`;160 打印 `RELEASED` **之后**
再跑一次 3-2,打印 `ACQUIRED`(锁正常释放,连接断开无残留)。

四个观测(150 held / 144 held / 150 after / 144 after)全符 → 窗口解除,
三机可正常使用 ops。

---

## 阶段 4 · migrate_drop_derived.sql(仅 160,手动)

前置全部满足才执行:

```bash
# a. 三机版本确认(三台各跑,rev 一致且 ≥ 85b590e):
cd ~/gsim-ops && git log --oneline -1
# b. spot-check(160 上):factor_snapshot 行数 ≈ 已入库因子数;僵尸表还在
docker exec -i ops-pg psql -U ops -d ops -c "SELECT count(*) FROM factor_snapshot;"
docker exec -i ops-pg psql -U ops -d ops -c "\dt" | grep -E "factor_derived|derived_meta"
# c. 备份(红线 3):
cd ~/gsim-ops/scripts/postgres && ./backup.sh && ls -lt dumps/ | head -3
```

执行与验证:

```bash
docker exec -i ops-pg psql -U ops -d ops < ~/gsim-ops/scripts/postgres/migrate_drop_derived.sql
docker exec -i ops-pg psql -U ops -d ops -c "\dt"    # factor_derived / derived_meta 应消失
# 三机各跑一次只读回归(Total 与基线一致):
uv run ops list 2>/dev/null | tail -1
uv run ops info <任选真因子>
```

收尾(三机各自,如存在):

```bash
rm -f ~/.cache/ops/lib/*/derived.json
```

---

## 阶段 5 · 报告

结果写入 `docs/remediation/VERIFY-UPGRADE-150-144-RESULT.md`(**不要自行
commit**,留给 wbai 审阅)。逐步一行(步骤/命令/预期/实际关键输出原文/判定),
外加:

- 150/144 升级前的旧 rev(阶段 1-1 / 2-0 探测结果,含 gsim/datasvc 有无);
- 三机 `ops list` Total 对照(应全等);
- 跨机锁四个观测的输出原文;
- 144 的 list / pytest 耗时(WAN 基线,留作以后对照);
- migration 前后 `\dt` 输出与 factor_snapshot 行数;
- 任何非预期输出的完整原文。

全部 ✅ 后的遗留(不在本窗口):Redis 残留 state key 清理(验稳后)、PG 密码
正规化(挪 /etc root-only)、wave3/stage-table 增量验证与滚存部署。
