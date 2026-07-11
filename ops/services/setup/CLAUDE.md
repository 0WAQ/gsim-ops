# Setup

`ops setup` —— 声明式管理本机 alphalib 部署(2026-07-11 立项;像 uv 管 python
项目:声明在 config,一条命令让本机就绪,之后 ops 开箱即用)。

## 形态

- `ops setup`(缺省)= **幂等补建**:对不达标且可补建的项 mkdir/symlink/groupadd,
  补完复检。写命令(sudo self-elevate);
- `ops setup --check` = **只读体检**:✔/✘/⚠ 清单;`--check` 经 cli 的
  `_CheckAction` 同时撤销写声明(不为看清单 sudo)。退出码:有 FAIL → 1。

**补建铁律:缺省 setup 只创建缺失,绝不改动已存在的东西**(软链指错 / gid
被占只报告;顶层权限对齐除外,照 02-layout.sh 模型只动顶层)。**声明变更的
收敛动作全部藏在显式 flag 后**:

- `ops setup --migrate-mount`(2026-07-11,170 /ext4→/nvme125 立项):把本机
  JFS 挂载点迁到 hosts 声明位置。编排在 `jfs.py::migrate_mount`(注入式
  MigrateIO,控制流测试无需 root):前置守卫(目标盘在位 / writeback 排干
  `.stats` staging_blocks=0 / env+unit 在位,违反即拒零改动)→ 备份 env+unit
  → systemctl stop → 改写 `/etc/juicefs-poc.env` 三键(MOUNT/CACHE_DIR/
  LOCAL_DIR,cache 同盘沿旧名,其余键原样)→ **重渲染 unit**(采集实证
  ExecStart 硬编码,改 env 不够;模板正主 `jfs.py::render_unit`,golden test
  用 170 现役 unit 原文钉住)→ daemon-reload → 搬 sidecar 存量 → start →
  验证新挂载 + alpha_src 非空 → 兼容软链原子重指(migrate 语义允许改已存在
  软链)。**旧址(旧挂载点目录/旧 cache/搬空 sidecar)报告不删**;任一步失败
  恢复备份 + 重启旧配置。CLI 有交互确认(`-y` 跳过),须在目标机 TTY 跑
  (sudo 自提权要密码)。

JFS 首次接入不归本命令(join.sh);数据对账(盘 ↔ PG)留给未来 ops doctor。

**170 迁移实战回填的三个坑(2026-07-11,均已修)**:
1. uv tool 二进制找 config 靠 cwd —— `$HOME` 下裸跑报 FileNotFoundError,须
   `cd <repo>` 或设 OPS_CONFIG;
2. 残留旧机制的 OPS_ALPHALIB_ROOT 会静默压过 hosts 声明(env 优先是刻意逃生
   口)→ setup 表头/迁移计划现在**显性打印 ⚠ env 覆盖提示**(同值不告警);
3. sudo 自提权判据(alpha_src 存在且 root-owned)在迁移场景失效(声明位置迁移
   前不存在)→ migrate 入口显式查 euid,非 root 直接指引 `sudo ops setup
   --migrate-mount`。

## 结构

- `checks.py` —— **项目注册表(部署应然形态的 SSOT)**:`SetupCheck(check_id,
  title, severity, check, fix|None)` 一项一行,新增检查 = 加一行(与 check
  流水线 PIPELINE 同款模式)。severity:FAIL = 存储部署错误(任何节点必须绿),
  WARN = 角色相关(worker 无 dropbox、纯投递机无 gsim 属正常)。
  `STAGING_IS_SHARED = True`(2026-07-11 共享 staging 部署同批翻转):staging
  应然 = JFS 实目录;False 分支保留历史语义(测试双模式钉住,防误翻)。
- `engine.py` —— `run_setup(config, apply=True, ctx=None)`:逐项 check →
  (apply 且可补建时)fix → 复检;单项崩溃不拖垮清单;返回 `CheckResult` 列表,
  **零展示**。
- 渲染在 `ops/cli/setup.py`(展示层上收示范件,勿回搬 services);cli 侧经
  `ops/cli/common.load_config` 拿 Config(C2 接缝)。

## hosts 声明(配套,ops/infra/config.py)

config.yaml `hosts:` 块按本机 hostname 精确匹配,覆盖 vars 同名项;优先级
**OPS_* env > hosts > vars**。命中情况回填 `config.hostname` /
`config.host_declared`(None=无块 / False=未命中 / True=命中),setup 的
host-declared 项消费。四机(160/150/144/170)挂载点差异全部进声明,
不再手工 export OPS_ALPHALIB_ROOT。

## PG 探测

经 `ops/infra/pg.probe`(5s 有界直连,不走 get_pool —— 池注册表不该被探测
污染,且池重试会让"PG 不可达"挂起半分钟以上)。锁检查先 probe 再进
factor_lock,避免不可达时双份长等。

测试:`tests/test_setup.py`(GROUPS 清空防 groupadd 容器副作用;mounts/
legacy_link 注入;应然全绿 / 补建幂等 / --check 零写 / 指错软链不动 /
Config hosts 优先级)。
