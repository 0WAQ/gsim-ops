# 存量归档 XML 生产化 —— 执行结果

日期 2026-07-18。分支 `claude/factor-production-features-pdnjdc` @ `35365d5`。
机器:server-170。脚本:`scripts/migrate_prod_xml.py`。

## TL;DR

- ✅ apply:总数 8297 | 将改/已改 8297 | 失败 0 | 占用 0,全量收敛,备份在位。
- ✅ 自验 dry-run:已生产态 8297 | 将改/已改 0 | 失败 0,幂等验证通过。

## 背景

入库时 `repo.productionize_src` 把归档 XML 改写为生产态(`core/prodxml.py` 三张规则表)。
该逻辑在本分支新增,存量已归档 XML 均为非生产态,需一次性收敛。

## 前置确认

- check 空档:`ops status --status checking` 为空,窗口干净。
- alpha_src 为 root-owned;用仓库 venv 直接执行绕开 sudo 下 uv PATH 问题:
  `sudo .venv/bin/python scripts/migrate_prod_xml.py --apply -y --report /tmp/migrate-apply.txt`

## Apply 结果

```
总数 8297 | 将改/已改 8297 | 已生产态 0 | 无XML 0 | 占用 0 | 失败 0 | apply=True
逐字段报告: /tmp/migrate-apply.txt
备份: /nvme125/alphalib/.migrate-prod-xml-bak-20260718-152239
```

- FAILED:0
- LOCKED(占用):0
- 备份目录:`/nvme125/alphalib/.migrate-prod-xml-bak-20260718-152239`(几十 MB)

## 自验(apply 后立即 dry-run)

```
总数 8297 | 将改/已改 0 | 已生产态 8297 | 无XML 0 | 占用 0 | 失败 0 | apply=False
逐字段报告: /tmp/migrate-verify.txt
```

将改/已改归零、已生产态 = 总数,幂等验证通过。
