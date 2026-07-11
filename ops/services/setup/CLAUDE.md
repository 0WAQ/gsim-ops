# Setup

`ops setup` —— 声明式管理本机 alphalib 部署(2026-07-11 立项;像 uv 管 python
项目:声明在 config,一条命令让本机就绪,之后 ops 开箱即用)。

## 形态

- `ops setup`(缺省)= **幂等补建**:对不达标且可补建的项 mkdir/symlink/groupadd,
  补完复检。写命令(sudo self-elevate);
- `ops setup --check` = **只读体检**:✔/✘/⚠ 清单;`--check` 经 cli 的
  `_CheckAction` 同时撤销写声明(不为看清单 sudo)。退出码:有 FAIL → 1。

**补建铁律:只创建缺失,绝不改动已存在的东西**(软链指错 / gid 被占只报告)。
唯一例外:顶层目录权限对齐(chown/chmod,照抄 scripts/juicefs-poc/02-layout.sh
模型,只动顶层不递归)。JFS 挂载本身不归本命令(join.sh);数据对账(盘 ↔ PG)
留给未来 ops doctor。

## 结构

- `checks.py` —— **项目注册表(部署应然形态的 SSOT)**:`SetupCheck(check_id,
  title, severity, check, fix|None)` 一项一行,新增检查 = 加一行(与 check
  流水线 PIPELINE 同款模式)。severity:FAIL = 存储部署错误(任何节点必须绿),
  WARN = 角色相关(worker 无 dropbox、纯投递机无 gsim 属正常)。
  `STAGING_IS_SHARED` 常量:共享 staging 部署(DEPLOY-SHARED-STAGING)落地时
  翻 True **与部署同批提交**,staging 的应然随之从"软链 → sidecar"变为
  "JFS 实目录"。
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
