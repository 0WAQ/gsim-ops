---
name: compare-cc
description: Compare two cc roots end-to-end (fingerprint + diff). Use when user asks "比对 cc_all 和新 cc_2024 一不一致", "看 160 vs 147 是不是同步" etc. Handles both same-host and cross-host (via rclone) comparisons.
---

# Compare CC Roots

跨 cc root 一致性比对, 自动跑 fingerprint + diff + 解读。

## 调用

```
/compare-cc <root_a> <root_b>
/compare-cc /datasvc/data/cc_all /tank/vault/datasvc/data/cc_2024
/compare-cc /datasvc/data/cc_all 147:/datasvc/data/cc_all       # 跨机器, 走 rclone
```

如果 root_b 形如 `<host>:<path>`, 说明跨服务器, 触发 rclone 流程。

## 步骤 (同机)

1. **生成两份指纹** (并行):

   ```bash
   cd /home/wbai/gsim-ops/scripts/data-audit
   python3 cc_fingerprint.py --root <ROOT_A> --out /tmp/fp_a.npz > /tmp/fp_a.log 2>&1 &
   python3 cc_fingerprint.py --root <ROOT_B> --out /tmp/fp_b.npz > /tmp/fp_b.log 2>&1 &
   wait
   ```

   各 ~15-30 min, 取决于盘速。

2. **跑 diff**:

   ```bash
   python3 cc_fingerprint_diff.py /tmp/fp_a.npz /tmp/fp_b.npz \
       --out /tmp/cc_diff.json --trim-last 1
   ```

3. **调 `cc-data-auditor` subagent** 解读 `/tmp/cc_diff.json`:

   - **真问题 vs 已知 incident**:
     - pwang industry 缺 487 天 (cc_all only, 已知)
     - Basedata/st.npy dtype 漂移 (160 vs 147, 已知)
     - 异步 build forecast 类 nan_diff (是设计, 不是 bug)
   - **新发现** (没列入已知 incident 的): flag 出来
   - **only_A / only_B**: 解释为啥多 / 少 (e.g. `signal_rsh` cc_all 独有 = 已弃用)

4. **输出**:

```
## CC 一致性: <ROOT_A> vs <ROOT_B>

总览:
  共名 N 文件, match X (Y%), sum_diff Z, nan_diff W
  only_A=M, only_B=K, shape/dtype diff=0

### 真差异 (X)
- file: <一句话原因>

### 已知 incident (X) — 不展开
- pwang 487 天缺口 (link)
- ...

### 设计行为 (X) — 可接受
- forecast 异步 build (137 文件)
- enddate 占位 NaN (1 行/文件)

### only_A (M, ROOT_A 独有)
- signal_rsh: 已弃用
- Dmgr_MktRet: 待确认

### only_B (K, ROOT_B 独有)
- 派生 Dipv/Dpv 系列: 新 build 加的

结论: <一句话, 一致 / 差异在已知 / 有新真问题>
```

## 步骤 (跨服务器)

若 `root_b` 形如 `147:/datasvc/data/cc_all`:

1. **本地跑 root_a 指纹**:
   ```bash
   python3 cc_fingerprint.py --root <ROOT_A> --out /tmp/fp_a.npz
   ```

2. **推脚本到 rclone bridge**, 提醒用户在 147 上跑:
   ```bash
   rclone copy /home/wbai/gsim-ops/scripts/data-audit/cc_fingerprint.py 39000:external-sync/scripts/
   ```
   输出给用户跑的命令:
   ```
   # 在 147 上执行:
   rclone copy 39000:external-sync/scripts/cc_fingerprint.py /tmp/
   python3 /tmp/cc_fingerprint.py --root <ROOT_B_PATH> --out /tmp/fp_b.npz
   rclone copy /tmp/fp_b.npz 39000:external-sync/fingerprints/
   ```

3. **等用户说"跑完了"**, 然后:
   ```bash
   rclone copy 39000:external-sync/fingerprints/fp_b.npz /tmp/
   python3 cc_fingerprint_diff.py /tmp/fp_a.npz /tmp/fp_b.npz --out /tmp/cc_diff.json
   ```

4. 同 (同机) 步骤 3-4。

## 何时不该用

- 想看单 root 质量 → `/audit-cc`
- 反馈具体字段 → `/verify-data-claim`
- 已经有指纹文件 → 直接跑 `cc_fingerprint_diff.py`, 不用 skill
