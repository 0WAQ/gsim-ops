# legacy 清理批执行结果(2026-07-13,160;手册 VERIFY-LEGACY-CLEANUP.md)

**结论:全绿收单。** 三迁移脚本落生产 + 池补账 31 条 + 全量 doctor 8 族
0 fail(exit=0)。

## 各阶段结果

| 阶段 | 结果 |
|---|---|
| 1 同步+门禁 | 174 passed, 0 skipped;分支代码 backfill 确认已退役 |
| 2 名单 | /tmp/dm-assign.txt 121 行(119 映射 + fguo 2 点名),缺映射 0 |
| 3 备份 | backup_legacy_202607131640.sql,5.1M,四表,dump complete ✓ |
| 4 dry-run | ①预览 472 ②候选 22/可补 22/skip 0 ③候选 129/可判定 129/unresolved 0/零冲突 —— 锚点全中 |
| 5 apply ① | UPDATE 472,后置断言残余 0,COMMIT |
| 5 apply ② | 落库 22 = 计划 22(新直连复核) |
| 5 apply ③ | UPDATE 129,残余 0,chk_discovery 收窄 + SET NOT NULL,收口完成 ✅ |
| 6 池补账 | 31 条(zxu 28 → pnl_manual,hwang 1 + fguo 2 → pnl_automated)。执行者会话 sudo 无 TTY 首跑 0/31,其新直连核对抓获停机;用户 sudo 终端补跑落地 |
| 7 复验 | 下表 |

## 复验锚点(全中)

- doctor 全量:8 族 0 fail,exit=0;snapshot-stale 0 / timeline-drift 0
- pool-ghost missing 39 → 8(降幅恰 31;余 8 条 approve 豁免合法,sli 1 /
  xmf 2 / ybai 5,只报不动)
- discovery_method:automated 8035(=8015+20)/ manual 384(=275+109),
  NULL 0;`\d factor_info` 确认 not null + CHECK IN ('automated','manual')
- 两池计数:pnl_manual 251 / pnl_automated 7213(补入 28 + 3)
- `ops list` Total 8252 不变;zxu 混排出现 status 列

## 执行期证伪与新知

1. **发现①不成立**:129/129 NULL-dm 的 submit 事件 actor=migration ——
   v2b 迁移合成存量,非旧部署机器提交;submit 硬校验一直有效,不存在
   正在产 NULL 的部署漂移。
2. **全局 shim 坑**:项目 not packaged,`uv run ops` fall through 到 PATH
   上的全局 uv-tool 旧命令 —— 验证一律 `uv run python -m ops.main`;
   **未合并分支绝不 reinstall 成全局命令**(生产工具跟 main 走,滚存在
   PR 合并后)。
3. **sudo 无 TTY**:执行者会话跑不了 sudo cp;`&&` 链里 echo 先行导致
   清单看似执行 —— 其落地核对(landed=0)是停机的关键一步,
   "打印 ≠ 落地"再次成立(v3 autocommit 教训的文件系统版)。

## 收尾

PR 合并后四机 `git pull` + `uv tool install --reinstall .`(160 顺带刷掉
暴露的旧版全局 shim);台账三行已 ⬜→✅。
