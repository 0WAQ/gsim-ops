# VERIFY: ops setup 三机验证结果

分支 `claude/ops-setup` @ `b715a1b`("feat: ops setup —— 声明式管理本机 alphalib 部署")。
验证日期 2026-07-11,执行机 server-160(GitHub 出口),150 经 160 SSH 直推分支。

## 结论

**160 / 150 全绿**:fast 套件 115 passed,`ops setup --check` FAIL 0 / WARN 1 / 退出码 0
—— 配置声明(`config.yaml` hosts 块)与生产部署一致,实证通过。

唯一 WARN 是顶层目录权限模型(owner/group 呈 `0:0` 而声明期望 `root:alpha-data`),
mode 位 `0o2755` 正确;属 cosmetic owner/group 差异,非阻断,按本机职责判断可接受。

## hostname 校准(config 猜测 vs 实测)

| host | config 声明 | 实测 hostname | 结论 |
|---|---|---|---|
| server-160 | server-160 | `server-160` | ✔ |
| server-150 | server-150 | `server-150` | ✔ |
| 北京 145 | (无 hosts 行) | `server-145` | — |
| 170 | server-170(标注"部署时校准") | `server-170` | ✔ 猜测正确,无需改 |
| 144 | intel-workstation-144(标注"部署时校准") | `intel-workstation-144` | ✔ 猜测正确,无需改 |

**config.yaml hosts 块无需改动** —— 170/144 的猜测 hostname 与实测一致。

## 详细结果

### server-160(10.9.100.160)

```
uv run pytest -m "not slow" -q
  → 115 passed, 8 skipped, 6 deselected in 3.42s

uv run ops setup --check   (退出码 0)
  ✔ hosts 声明命中                     hosts.server-160 命中,路径按声明解析
  ✔ alphalib JFS 挂载                  /tank/vault/alphalib (fuse.juicefs)
  ✔ 共享目录 (src/pnl/feature)         全部为实目录
  ✔ bcorr 分流池 (automated/manual)    全部为实目录
  ✔ staging 形态                       staging → /tank/vault/alphalib.local/staging
  ✔ alpha_dump 软链 → sidecar          alpha_dump → /tank/vault/alphalib.local/alpha_dump
  ✔ /mnt/storage/alphalib 兼容软链     → /tank/vault/alphalib
  ✔ 权限组 alpha-core/alpha-data       在位
  ⚠ 顶层目录权限模型                   alphalib/pnl_automated/pnl_manual: 0:0 0o2755 != root:alpha-data 0o2755
  ✔ Postgres 连接 + 三表               连接 + 三表在位
  ✔ 跨机因子锁往返                     跨机 advisory lock 往返正常
  ✔ nio_data_path 数据                 在位
  ✔ dropbox 投递目录(submit 节点)      在位
  ✔ gsim 工具链(check 节点)            在位
  FAIL: 0  WARN: 1  已补建: 0  (共 14 项)

hostname: server-160
```

### server-150(10.9.100.150)

150 无 GitHub 出口(`git fetch` 报 GnuTLS `TLS connection non-properly terminated`,
IDC 网络隔离)。分支经 160 `git push ssh://10.9.100.150/home/wbai/gsim-ops` 直推。
`uv run ops` 在 150 上 PATH 未挂 entry point,改用已安装的 `~/.local/bin/ops`(uv tool)
—— 该 binary 已含 `setup` 子命令并正确识别 `server-150`。

```
uv run pytest -m "not slow" -q
  → 115 passed, 8 skipped, 6 deselected in 2.96s

~/.local/bin/ops setup --check   (退出码 0)
  ✔ hosts 声明命中                     hosts.server-150 命中,路径按声明解析
  ✔ alphalib JFS 挂载                  /tank/vault/alphalib (fuse.juicefs)
  ✔ 共享目录 (src/pnl/feature)         全部为实目录
  ✔ bcorr 分流池 (automated/manual)    全部为实目录
  ✔ staging 形态                       staging → /tank/vault/alphalib.local/staging
  ✔ alpha_dump 软链 → sidecar          alpha_dump → /tank/vault/alphalib.local/alpha_dump
  ✔ /mnt/storage/alphalib 兼容软链     → /tank/vault/alphalib
  ✔ 权限组 alpha-core/alpha-data       在位
  ⚠ 顶层目录权限模型                   同 160(0:0 0o2755 != root:alpha-data 0o2755)
  ✔ Postgres 连接 + 三表               连接 + 三表在位
  ✔ 跨机因子锁往返                     跨机 advisory lock 往返正常
  ✔ nio_data_path 数据                 在位
  ✔ dropbox 投递目录(submit 节点)      在位
  ✔ gsim 工具链(check 节点)            在位
  FAIL: 0  WARN: 1  已补建: 0  (共 14 项)

hostname: server-150
```

## 备注

- **150 无 GitHub 出口**:后续 150 更新分支需从 160 SSH 直推,或本机走内网 git mirror。
- **`uv run ops` 在 150 不可用**:150 的 ops 是 uv tool 独立环境(`~/.local/bin/ops`),
  `uv run` 的临时 venv 未装 `ops` 包。生产调用应走 `~/.local/bin/ops`;若要 `uv run ops`
  需 `uv tool install --reinstall`(见 memory uv-tool-env-deps)。
- WARN(顶层目录 owner/group)三机一致,非本次分支引入,后续权限正规化窗口统一处理。
- 144 / 170 未纳入本轮(144 冷副本跨段、170 无 submit/check 职责);hostname 已远程确认,
  config 声明正确。
