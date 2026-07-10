# 验证结果 · Factor 聚合阶段 2/3(160 单机)

**分支**:`claude/factor-aggregate-phase3`
**160 rev**:`90bfd9e docs: Factor 聚合阶段 2/3 的 160 验证执行手册`(在手册要求的 `34e7aee` 之后)
**执行日期**:2026-07-10
**结论**:**阶段 1 fast suite 出现 2 个 FAILED(预期 0 failed),按红线停在阶段 1,未自行修复。** 阶段 2/3/4 未执行。

---

## 阶段 0 · 部署 + 静态门禁 —— 通过

`git status -sb`(tracked 干净;untracked 均为会话开始即存在、与本分支无关的文件):

```
## claude/factor-aggregate-phase3...origin/claude/factor-aggregate-phase3
?? docs/reports/check/check-fguo-20260709-235302.json
?? docs/reports/check/check-lhw-20260710-031451.json
?? docs/reports/check/check-xmf-20260710-122605.json
?? docs/reports/check/check-zxu-20260710-192411.json
?? pgreadonlysetup.sql
```

`git log --oneline -3`:

```
90bfd9e docs: Factor 聚合阶段 2/3 的 160 验证执行手册(PG 组 + e2e + 金丝雀环路)
34e7aee feat(cli): cli/common 接缝 + 7/7 契约 enforcing,ratchet 退役;status/cancel/pack 塌缩(阶段 3 第一批)
9b74816 feat(repository): Factor 聚合 + FactorRepository 落地,C3 清零转 enforcing(阶段 2)
```

`uv sync --group dev`:Resolved 29 packages,新装 click 8.4.2 / grimp 3.15 / import-linter 2.13。

`uv run ruff check ops tests`:

```
All checks passed!
```

`uv run pyright ops`:

```
0 errors, 0 warnings, 0 informations
```

`uv run lint-imports`(本次新验收):

```
C1 layers: cli -> services -> infra -> core -> utils KEPT
C2 cli must not import infra or core (directly) KEPT
C3 service packages are independent KEPT
C5 utils is a leaf KEPT
C6 infra must not import presentation KEPT
C7 services use store factories, not concrete backends KEPT
C8 db drivers only in infra KEPT

Contracts: 7 kept, 0 broken.
```

`ls scripts/ci/ contracts-baseline.toml`(预期都不存在):

```
ls: cannot access 'scripts/ci/': No such file or directory
ls: cannot access 'contracts-baseline.toml': No such file or directory
```

阶段 0 全项符合预期。

---

## 阶段 1 · fast suite 含 PG 组 —— **失败,停在此步**

`uv run pytest -m "not slow" -q` 汇总行:

```
2 failed, 99 passed, 8 skipped, 6 deselected in 3.09s
```

失败用例(稳定复现,复跑一致):

```
FAILED tests/test_check_scan.py::test_ensure_record_creates_submitted - AttributeError
FAILED tests/test_check_scan.py::test_ensure_record_does_not_overwrite - AttributeError
```

失败 traceback(`test_ensure_record_creates_submitted`):

```
    def test_ensure_record_creates_submitted(test_config, make_factor):
        cfg_path, config = test_config
        make_factor(name="AlphaEnsure", author="wbai")
        pipe = _pipeline(cfg_path, checkers={})
        factor = pipe.metadatas[0]
        store = _store(config)
        assert store.get("AlphaEnsure") is None
>       pipe._ensure_record(factor, store)

tests/test_check_scan.py:78:
...
self = <ops.services.check.check.CheckerPipeline object at 0x705dea5c4f70>
factor = <ops.core.alpha.metadata.AlphaMetadata object at 0x705de837e770>
repo = <ops.infra.store.pg_store.PostgresStateStore object at 0x705dea5c75b0>

    def _ensure_record(self, factor: AlphaMetadata, repo: FactorRepository) -> None:
>       if repo.record(factor.name) is not None:
E       AttributeError: 'PostgresStateStore' object has no attribute 'record'

ops/services/check/check.py:256: AttributeError
```

### 根因(只读排查,未修复)

- `_ensure_record` 在阶段 2/3 重构后签名改为 `(factor, repo: FactorRepository)`,函数体 `check.py:256` 调用 `repo.record(...)`。
- `FactorRepository.record` 确实存在(`ops/infra/repository.py:134`)。
- **生产路径无问题**:`check.py:324-325` 调用处传的是 `self._repo()`(即 `FactorRepository`),`repo.record()` 有效。
- **失败仅限 `tests/test_check_scan.py` 两个用例的陈旧夹具**:它们把 `_store(config)`(经 `default_store` → `PostgresStateStore`)当作第二参传给 `pipe._ensure_record(factor, store)`,而 `PostgresStateStore` 没有 `record` 方法。这两个测试未随阶段 2/3 的 store→repository 迁移同步更新。
- 判定:**测试夹具滞后,非生产 bug**。但手册阶段 1 预期"0 failed",实际 2 failed,按红线立即停止、不自行修复。

### 手册点名用例结果(全部 PASSED)

`tests/test_repository.py`(PG 组,ops_test 可达真跑,14 个全过,含手册点名的 register 原子 / find 因子集与过滤 / include_submitted / info 孤儿现形 / attach_snapshot 强制 entered_at + stale 自愈 / attach 无 entered_at 拒绝 / delete 级联):

```
tests/test_repository.py::test_purge_check_scope_only PASSED
tests/test_repository.py::test_purge_serving_scope_only PASSED
tests/test_repository.py::test_purge_all_scope PASSED
tests/test_repository.py::test_json_register_get_delete PASSED
tests/test_repository.py::test_json_find_unsupported PASSED
tests/test_repository.py::test_lock_facade PASSED
tests/test_repository.py::test_factor_aggregate_soft_invariant PASSED
tests/test_repository.py::test_pg_register_atomic_two_tables PASSED
tests/test_repository.py::test_pg_find_factor_set_and_filters PASSED
tests/test_repository.py::test_pg_attach_snapshot_stamps_entered_at PASSED
tests/test_repository.py::test_pg_attach_snapshot_requires_entered_at PASSED
tests/test_repository.py::test_pg_delete_cascades PASSED
tests/test_repository.py::test_pg_find_include_submitted PASSED
tests/test_repository.py::test_pg_find_surfaces_info_orphans PASSED
```

`tests/test_check_routing_json.py` 点名 4 个:

```
tests/test_check_routing_json.py::test_identity_divergence_refused_before_state PASSED
tests/test_check_routing_json.py::test_ensure_record_works_without_seed PASSED
tests/test_check_routing_json.py::test_preamble_crash_emits_done PASSED
tests/test_check_routing_json.py::test_watch_futures_unblocks_on_all_pending PASSED
```

`tests/test_repository.py tests/test_check_routing_json.py tests/test_lifecycle_cmds.py tests/test_factor_paths.py` 合跑:

```
44 passed in 1.23s
```

即:手册点名的 4 个文件全绿(含 test_lifecycle_cmds 的 PG 组 cancel 守卫 / rm staging、test_factor_paths 布局契约)。唯一失败落在**未被点名**的 `test_check_scan.py` 两个 `_ensure_record` 用例。

---

## 阶段 2 · e2e —— 未执行(阶段 1 已停)

## 阶段 3 · 只读冒烟 —— 未执行

## 阶段 4 · 金丝雀行为环路 —— 未执行

---

## 待决

阶段 1 的 2 个 FAILED 是 `test_check_scan.py` 夹具未随 store→repository 迁移更新所致,生产路径无影响。是否:
1. 由分支作者修正这两个测试夹具(传 `_repo()` 而非 `_store()`)后重跑,或
2. 明确判定为"已知陈旧测试、不阻塞",授权继续阶段 2 起后续验证。

按红线未自行修复,等待指示。
