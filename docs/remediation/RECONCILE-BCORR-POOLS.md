# bcorr 池存量鬼影清理执行手册(160,两阶段)

**背景**:池副本政策(池里有副本 ⇔ 因子 ACTIVE 在库,`purge_artifacts` CHECK 面)
2026-07-08 才上线,之前离库的因子在 `pnl_automated/` / `pnl_manual/` 留下了存量
残留("鬼影")。已实证一例:AlphaWbaiReversal(rejected)仍在 pnl_manual
(VERIFY-AGGREGATE-P2P3-RESULT 侧记)。鬼影让重检/新因子在 bcorr 撞上已离库
对手 → 误拒。工具:`scripts/reconcile_bcorr_pools.py`(判定表见脚本 docstring)。

**红线**:
1. 阶段 1(dry-run)**只读**,随时可跑;阶段 2(--apply)是**生产删除**,必须
   等 dry-run 报告判读 + 用户确认后才执行;
2. --apply 只删两个池目录内被判 ghost 的**文件**;alpha_pnl / dump / PG 一概不碰;
3. `wrong-pool` / `missing` 两类只报告,**不做任何处置**(归未来 ops doctor);
4. 报告贴命令原文;与预期不符立即停。

## 阶段 1 · dry-run 对账(只读)

```bash
cd ~/gsim-ops && git fetch origin claude/ops-rotate-and-reconcile
git checkout claude/ops-rotate-and-reconcile && git pull origin claude/ops-rotate-and-reconcile
git log --oneline -1                     # 记录 rev
uv run python scripts/reconcile_bcorr_pools.py | tee /tmp/bcorr-reconcile-dryrun.txt
```

预期:`PG 因子记录` ≈ 库内规模(~8k+);ghost 列表里应能看到
`manual/AlphaWbaiReversal`(已知实例,不在则停 —— 说明判定或环境有问题);
`dry-run 结束(未删除任何文件…)` 收尾。

把 `/tmp/bcorr-reconcile-dryrun.txt` **全文**(鬼影几百行也全贴)写进报告
`docs/remediation/RECONCILE-BCORR-POOLS-RESULT.md`,push 到本分支。**到此为止,
等判读**。

## 阶段 2 · apply(判读通过 + 用户确认后)

```bash
sudo $(command -v uv) run python scripts/reconcile_bcorr_pools.py --apply | tee /tmp/bcorr-reconcile-apply.txt
# (sudo 下 uv/PG 凭证不可用时按本机习惯以 root 跑同一脚本,报告注明方式)
```

复验:

```bash
uv run python scripts/reconcile_bcorr_pools.py | tail -5    # 预期 ghost: 0
ls /tank/vault/alphalib/pnl_manual/AlphaWbaiReversal 2>&1   # 预期 No such file
uv run ops list 2>/dev/null | tail -1                       # Total 不变(池副本不影响因子集)
```

apply 输出 + 复验三条追加进 RESULT,push。任何 `删除失败`:停,附原文。
