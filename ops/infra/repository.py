"""FactorRepository —— 因子的存储门面(factor-aggregate-plan §3.2,full-review D1)。

service 层与"因子的持久形态"之间的唯一通道:记录面(三表读写)+ 产物面(盘面
文件)。原先 16 个命令各自手工构造 store、手工 join、手工拼路径 —— 现在:

**记录面**
  - `get(name)` / `find(...)` —— 组装 `Factor` 聚合(core/factor.py);find 是
    单条三表 LEFT JOIN(退役 query_factors 的三次查 + 内存合并)。
  - `register(identity, ...)` —— 原子写 info+state(一个 PG 事务;收编
    submit/backfill/check 三份手抄的双表编排)。
  - `record(name)` / `transition(...)` / `append_check(...)` —— state 轻量读写
    (委托 StateStore;transition 的 CAS 语义原样透传)。
  - `attach_snapshot(snapshot)` —— 入库快照落库:强制 snapshot_at = entered_at,
    含 stale 自愈(原 check._persist_derived 的落库半边)。
  - `discard_snapshot(name)` —— 离库时快照失效(restage / submit --overwrite)。
  - `delete(name)` —— 删 factor_info,FK 级联 state+snapshot(ops rm)。
  - `exists(name)` —— 一种语义:factor_info 有行(消灭"问 state 删 info")。
  - `lock(name)` —— factor_lock 门面。

**产物面**(PV7 两面模型进类型)
  - `paths(name)` —— FactorPaths(S4 布局正主)。
  - `purge_artifacts(name, scope)` —— 按 ArtifactScope 清产物(收编原
    services/rm 的 _purge_artifacts/_recycle_check_artifacts 跨包 helper)。

**后端语义**:postgres = 生产全功能。json = 单机 dev/test,只有 state ——
identity/snapshot 操作按"尽力而为"降级(register 只写 state、get 合成仅含
name 的 identity、discard_snapshot no-op、find 不支持),使 check/批量命令的
控制流测试无需 PG 即可跑。
"""
from __future__ import annotations

import shutil
from dataclasses import replace
from enum import Flag, auto
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from ops.core.factor import Factor, FactorIdentity, FactorSnapshot
from ops.core.paths import FactorPaths
from ops.core.state import CheckRecord, FactorRecord, FactorStatus, HistoryEvent
from ops.infra.info import default_info_store
from ops.infra.lock import factor_lock
from ops.infra.pg import get_pool
from ops.infra.pg import ts_out as _ts_out
from ops.infra.schema import ensure_schemas
from ops.infra.snapshot import default_snapshot_store
from ops.infra.snapshot.pg_store import metric_order_expr, snapshot_where
from ops.infra.store import default_store
from ops.utils.actor import current_actor
from ops.utils.clock import now_iso
from ops.utils.factor_dir import clean_pycache, rewrite_module_path
from ops.utils.log import logger

if TYPE_CHECKING:
    from ops.infra.config import Config
    from ops.infra.info.base import InfoStore
    from ops.infra.snapshot.base import SnapshotStore
    from ops.infra.store.base import StateStore


class ArtifactScope(Flag):
    """因子产物的两个面(PV7):

    - CHECK:alpha_pnl + bcorr 池副本 —— 喂 correlation 对比池,**离库即失效**,
      一律回收(否则重检时新 pnl 对自己旧 pnl corr≈1,"自鬼影"必拒)。
    - SERVING:alpha_dump + alpha_feature —— 最后一次入库版本的 last-known-good,
      生产 combo 在重检窗口继续消费;默认保留,--purge / REJECTED 召回才清。
    """
    CHECK = auto()
    SERVING = auto()
    ALL = CHECK | SERVING


# find() 的 SELECT 列(三表 LEFT JOIN + 最近失败 LATERAL)。check 全史
# 不在 record 上(v2c 剥离):按需 store.checks() / repo.history()。
# lf.* 来自 factor_history 派生(v2b:state 的 rejected_at/last_fail_* 已删列)。
_FIND_COLS = (
    "i.name, i.author, i.discovery_method, i.created_at, "
    "s.status, s.version, s.submitted_at, s.entered_at, s.updated_at, "
    "n.ret, n.shrp, n.mdd, n.tvr, n.fitness, n.fields, n.tables, n.delay, "
    "n.max_bcorr, n.max_bcorr_factor, n.snapshot_at, "
    "lf.at, lf.failed_stage, lf.fail_reason"
)

# 最近一次 check 失败(与 StateStore.last_fail 同语义的 SQL 形态)。
# 8k 因子 × 每因子个位数事件 + ix_fh_name_at 索引,LATERAL 成本可忽略。
_LAST_FAIL_LATERAL = """
LEFT JOIN LATERAL (
    SELECT h.at, h.failed_stage, h.fail_reason
    FROM factor_history h
    WHERE h.name = i.name AND h.op = 'check' AND h.passed = FALSE
    ORDER BY h.at DESC, h.id DESC LIMIT 1
) lf ON TRUE
"""


class FactorRepository:
    """构造便宜(store 全懒加载);跨命令直接 `FactorRepository(config)`。"""

    def __init__(self, config: Config):
        self.config = config

    # ------------------------------------------------------------------ 后端
    @property
    def _is_pg(self) -> bool:
        backend = (getattr(self.config, "state_backend", None) or "").lower()
        return backend == "postgres" and bool(
            getattr(self.config, "state_postgres_conninfo", None))

    @cached_property
    def _conninfo(self) -> str:
        conninfo = getattr(self.config, "state_postgres_conninfo", None)
        if not conninfo:
            raise ValueError("此操作需要 Postgres 后端(state.postgres 未配置)")
        # 首次触达 PG 时引导三表(FK 依赖序;幂等,每进程一次)。生产表已存在,
        # 这里兜的是 dev/test/新环境 —— DDL 已滚出 store __init__。
        ensure_schemas(conninfo)
        return conninfo

    @cached_property
    def _state(self) -> StateStore:
        if self._is_pg:
            # 触发 schema 引导:record/transition 等 state-only 方法常是命令的
            # 第一个 PG 触点(submit 预过滤/check._ensure_record),不触发则
            # "首次触达 PG 自动引导三表"的承诺对这些路径为假 —— 空库上
            # SELECT factor_state 直接 UndefinedTable(对抗评审确认)。
            self._conninfo  # noqa: B018
        return default_store(self.config)

    @cached_property
    def _info(self) -> InfoStore:
        self._conninfo  # noqa: B018 — 触发 schema 引导(info store 是 PG-only)
        return default_info_store(self.config)

    @cached_property
    def _snapshot(self) -> SnapshotStore:
        self._conninfo  # noqa: B018 — 同上
        return default_snapshot_store(self.config)

    # ------------------------------------------------------------ 记录面:读
    def exists(self, name: str) -> bool:
        """因子是否存在 —— 一种语义:factor_info 有行(三表之根)。
        json dev/test 后端无 info 表,退化为 state 有记录。"""
        if not self._is_pg:
            return self._state.get(name) is not None
        return self._info.get(name) is not None

    def record(self, name: str) -> FactorRecord | None:
        """state 轻量读(纯状态机快照,v2c 起不含 check 全史),不拼 Factor。"""
        return self._state.get(name)

    def get(self, name: str) -> Factor | None:
        """单因子全景(identity + state + snapshot + last_fail 派生)。
        None = factor_info 无行。"""
        if not self._is_pg:
            rec = self._state.get(name)
            if rec is None:
                return None
            return Factor(identity=FactorIdentity(name=name), state=rec,
                          last_fail=self._state.last_fail(name))
        identity = self._info.get(name)
        if identity is None:
            return None
        return Factor(
            identity=identity,
            state=self._state.get(name),
            snapshot=self._snapshot.get(name),
            last_fail=self._state.last_fail(name),
        )

    def latest_check_ats(self) -> dict[str, str]:
        """全库 name → 最近一次 check 事件 at(doctor 测得快照对账,v3)。"""
        return self._state.latest_check_ats()

    def history(self, name: str) -> list[HistoryEvent]:
        """完整生命周期事件时间线(status 详情;json dev/test 后端合成
        check 事件,生命周期 op 缺席)。"""
        return self._state.history(name)

    def find(
        self,
        *,
        author: str | None = None,
        status: str | FactorStatus | None = None,
        fail_stage: str | None = None,
        field: str | None = None,
        table_glob: str | None = None,
        metrics: list[tuple[str, str, float]] | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
        include_submitted: bool = False,
    ) -> list[Factor]:
        """库内因子目录查询 —— 单条三表 LEFT JOIN(退役 query_factors)。

        **因子集判据**(2026-07-07 Wave 2,JOURNAL V1):库内因子 =
        `factor_state.status != 'submitted'`;`status` 给定时按其精确过滤
        (包括显式查 submitted);`include_submitted=True` 且 status 未给时
        返回全状态(status 命令的"任何记录"语义,2026-07-09 阶段 3)。
        snapshot 侧条件(field/tables/metrics)沿用 GIN/LIKE 下推,无快照的
        因子在这些条件下自然落选(与旧内存合并一致)。

        返回的 Factor.state 是纯状态机快照(check 全史 v2c 已剥离);
        排序:sort_by 命中白名单时按其 DESC NULLS LAST,并以 name 兜底稳定。
        limit 仅在给定时下推(ORDER BY 恒定,结果确定)。
        """
        if not self._is_pg:
            raise NotImplementedError("find 只支持 Postgres 后端(单库永远 PG)")
        self._conninfo  # noqa: B018 — schema 引导

        where = ["TRUE"]
        params: list = []
        if status is not None:
            status_value = status.value if isinstance(status, FactorStatus) else status
            where.append("s.status = %s")
            params.append(status_value)
        elif not include_submitted:
            where.append("s.status != 'submitted'")
        if author:
            where.append("i.author = %s")
            params.append(author)
        if fail_stage:
            where.append("lf.failed_stage = %s")
            params.append(fail_stage)

        snap_clauses, snap_params = snapshot_where(
            field, table_glob, metrics, prefix="n.")
        where += snap_clauses
        params += snap_params

        order_expr = metric_order_expr(sort_by, prefix="n.")
        order_sql = (f"ORDER BY {order_expr} DESC NULLS LAST, i.name"
                     if order_expr else "ORDER BY i.name")

        limit_sql = ""
        if limit:
            limit_sql = "LIMIT %s"
            params.append(limit)

        # factor_state 也是 LEFT JOIN(2026-07-09 阶段 3 评审修正,原 INNER):
        # info 有行、state 无行的孤儿(register 事务化前的半截写入 / 手工残留,
        # 7-06 迁移实测过 20 个)在 include_submitted=True 的"任何记录"语义下
        # 必须现形(status 全列表 / cancel 批量的"需对账"提示),否则对账线索
        # 为零。缺省因子集判据 `s.status != 'submitted'` 对 NULL 求值为假 ——
        # 孤儿天然不算库内因子,ops list 不受影响。
        query = f"""
            SELECT {_FIND_COLS}
            FROM factor_info i
            LEFT JOIN factor_state s ON s.name = i.name
            LEFT JOIN factor_snapshot n ON n.name = i.name
            {_LAST_FAIL_LATERAL}
            WHERE {" AND ".join(where)}
            {order_sql}
            {limit_sql}
        """
        pool = get_pool(self._conninfo)
        with pool.connection() as conn:
            # 动态片段全部来自白名单表达式,值走参数;psycopg stub 要求
            # LiteralString,结构安全,定点豁免(与 snapshot_store.list 同款)。
            rows = conn.execute(query, params).fetchall()  # pyright: ignore[reportArgumentType]
        return [self._row_to_factor(r) for r in rows]

    @staticmethod
    def _row_to_factor(row) -> Factor:
        (name, author, discovery_method, created_at,
         status, version, submitted_at, entered_at, updated_at,
         ret, shrp, mdd, tvr, fitness, fields, tables, delay,
         max_bcorr, max_bcorr_factor, snapshot_at,
         lf_at, lf_stage, lf_reason) = row
        identity = FactorIdentity(
            name=name,
            author=author,
            discovery_method=discovery_method,
            created_at=_ts_out(created_at),
        )
        state = None
        if status is not None:  # LEFT JOIN:state 行缺失(info 孤儿)时全列 NULL
            state = FactorRecord(
                name=name,
                status=FactorStatus(status),
                updated_at=_ts_out(updated_at),
                submitted_at=_ts_out(submitted_at),
                entered_at=_ts_out(entered_at),
                version=version,
            )
        snapshot = None
        if snapshot_at is not None:  # snapshot_at 列 NOT NULL → 行存在的判据
            snapshot = FactorSnapshot(
                name=name,
                ret=ret, shrp=shrp, mdd=mdd, tvr=tvr, fitness=fitness,
                fields=fields if fields else None,
                tables=tables if tables else None,
                delay=delay,
                max_bcorr=max_bcorr,
                max_bcorr_factor=max_bcorr_factor,
                snapshot_at=_ts_out(snapshot_at),
            )
        last_fail = None
        if lf_stage is not None:  # chk_fail_has_stage:失败事件必有 stage
            last_fail = HistoryEvent(
                name=name, op="check", at=_ts_out(lf_at) or "",
                passed=False, failed_stage=lf_stage, fail_reason=lf_reason,
            )
        return Factor(identity=identity, state=state, snapshot=snapshot,
                      last_fail=last_fail)

    # ------------------------------------------------------------ 记录面:写
    def register(
        self,
        identity: FactorIdentity,
        *,
        status: FactorStatus = FactorStatus.SUBMITTED,
        submitted_at: str | None = None,
        entered_at: str | None = None,
        version: int = 1,
        op: str | None = None,
    ) -> None:
        """登记因子:info(身份)+ state(状态)+ 事件一个事务原子写。

        调用方(submit/backfill/check._ensure_record)在 factor_lock 内先判
        不存在再调;info 是 upsert、state 是 put(upsert),崩溃重跑幂等。
        op(submit/backfill)发射命令事件;status=ACTIVE(backfill)另发
        'entered'(入库统一标记,与 transition 的自动发射同规)。
        json dev/test 后端只有 state,identity/事件落不了库(尽力而为语义)。
        """
        record = FactorRecord(
            name=identity.name,
            status=status,
            updated_at=now_iso(),
            submitted_at=submitted_at,
            entered_at=entered_at,
            version=version,
        )
        if not self._is_pg:
            self._state.put(record)
            return
        # 单事务:两表要么都写、要么都不写(原先 submit/backfill/check 各自
        # 顺序两次调用,崩在中间留"有 info 无 state"的半截因子)。
        from ops.infra.info.pg_store import PostgresInfoStore
        from ops.infra.store.pg_store import PostgresStateStore, emit_on

        actor = current_actor()
        pool = get_pool(self._conninfo)
        with pool.connection() as conn, conn.transaction():
            PostgresInfoStore.upsert_on(conn, identity)
            PostgresStateStore.put_on(conn, record)
            if op is not None:
                emit_on(conn, HistoryEvent(
                    name=identity.name, op=op,
                    at=record.updated_at or now_iso(), actor=actor))
            if status == FactorStatus.ACTIVE:
                emit_on(conn, HistoryEvent(
                    name=identity.name, op="entered",
                    at=record.updated_at or now_iso(), actor=actor))

    def transition(self, name: str, to_status: FactorStatus,
                   expect: FactorStatus | None = None,
                   op: str | None = None, **updates) -> FactorRecord:
        """状态转移(CAS 语义透传 StateStore.transition)。op 非 None 时同
        事务发射事件;置 ACTIVE 自动发射 'entered'(store 层保证)。"""
        return self._state.transition(name, to_status, expect=expect,
                                      op=op, actor=current_actor(), **updates)

    def append_check(self, name: str, check: CheckRecord) -> None:
        self._state.append_check(name, check, actor=current_actor())

    def attach_snapshot(self, snapshot: FactorSnapshot,
                        measured_at: str | None = None) -> None:
        """测得快照落库(schema v3:factor_snapshot = 最近一次 check 测得的
        表现,被拒也写)。snapshot_at = measured_at(该次 check 事件的时刻,
        由 check 流水线传入);入库见证已全权归 entered_at/entered 事件,
        原 entered_at 硬闸删除 —— 写快照 ≠ 入库(词汇表)。

        每次测量原子替换(原 stale 自愈,v3 起是常规路径):旧行让位,否则
        insert 撞 name UNIQUE 被吞,读侧永远停在上一次测量(full-review P0-1)。
        json dev/test 后端无快照表,尽力而为 no-op。
        """
        if not self._is_pg:
            return
        if not measured_at:
            raise ValueError(f"attach_snapshot: factor {snapshot.name} "
                             "缺 measured_at(测得时刻,= 该次 check 事件的 at)")
        snap = replace(snapshot, snapshot_at=measured_at)
        if self._snapshot.get(snap.name) is not None:
            logger.info("replacing snapshot with new measurement factor={}", snap.name)
            self._snapshot.delete(snap.name)
        self._snapshot.insert(snap)

    def discard_snapshot(self, name: str) -> bool:
        """离库 → 旧快照失效(restage / submit --overwrite)。返回是否真删了行。
        json dev/test 后端无快照表,no-op(使控制流测试无需 PG)。"""
        if not self._is_pg:
            return False
        return self._snapshot.delete(name)

    def delete(self, name: str, op: str | None = None) -> bool:
        """删 factor_info,FK 级联删 state + snapshot(ops rm / cancel)。
        返回行是否存在。op(rm/cancel)与 DELETE 同事务发射事件 ——
        factor_history 无 FK,是唯一活过删除的痕迹(v2b 的立项动机)。
        json dev/test 后端退化为删 state 记录。"""
        if not self._is_pg:
            return self._state.delete(name, op=op)
        if op is None:
            return self._info.delete(name)
        from ops.infra.store.pg_store import emit_on

        pool = get_pool(self._conninfo)
        with pool.connection() as conn, conn.transaction():
            cur = conn.execute("DELETE FROM factor_info WHERE name = %s", (name,))
            if cur.rowcount > 0:
                emit_on(conn, HistoryEvent(
                    name=name, op=op, at=now_iso(), actor=current_actor()))
            return cur.rowcount > 0

    def lock(self, name: str):
        """per-factor advisory lock 门面(with repo.lock(name): ...)。"""
        return factor_lock(name, self.config)

    # ---------------------------------------------------------------- 产物面
    def paths(self, name: str) -> FactorPaths:
        return FactorPaths.of(name, self.config)

    def archive(self, name: str, *, src_dir: Path, dump_dir: Path,
                pnl_file: Path, discovery_method: str | None) -> None:
        """staging/工作区产物归档入库(收编原 check.to_lib,2026-07-10):
        src → alpha_src(+@module 重指)、dump → alpha_dump、pnl → alpha_pnl,
        并按因子来源把 pnl 分流一份到 pnl_automated / pnl_manual 池。

        身份兜底断言(第一道闸在 check.run_one 入口):下方 rmtree/move/rewrite
        三步共用 paths.src(键=name/@id)锚点,其正确性依赖 src_dir.name == name。
        发散时 rmtree 删的是"另一个因子"的唯一源码,绝不能带病归档 —— 抛错由
        调用方的 unexpected 臂接住(check:revert SUBMITTED)。
        """
        paths = self.paths(name)
        if src_dir.name != name:
            raise RuntimeError(
                f"identity divergence: staging dir {src_dir.name!r}"
                f" != factor name {name!r}, refuse to archive")

        clean_pycache(src_dir)
        if paths.src.exists():
            shutil.rmtree(paths.src)
        shutil.move(src_dir, paths.src)
        rewrite_module_path(paths.src)

        if paths.dump.exists():
            shutil.rmtree(paths.dump)
        shutil.move(dump_dir, paths.dump)

        # alpha_pnl/<name> 是单文件(FactorPaths 布局事实):restage 保留 pnl →
        # re-archive 时此处必有旧文件,rmtree 对文件抛 NotADirectoryError
        # (full-review 第一部分 1.2)。目录形态只可能是远古残留。
        if paths.pnl.is_dir():
            shutil.rmtree(paths.pnl)
        elif paths.pnl.exists():
            paths.pnl.unlink()
        shutil.move(pnl_file, paths.pnl)

        # 按因子来源 (discovery_method) 把 pnl 额外分流一份到对应池。
        # pnl_file 此时已被 move 走,从入库后的 paths.pnl 拷。pnl 是单文件,copy2。
        bucket = {"automated": paths.pool_automated,
                  "manual": paths.pool_manual}.get(discovery_method or "")
        if bucket is not None:
            bucket.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(paths.pnl, bucket)
        else:
            logger.warning("discovery_method 缺失/非法, 跳过 pnl 分流 factor={} value={}",
                           name, discovery_method)

    def recall(self, name: str) -> None:
        """alpha_src/<name> → staging/<name>(收编原 restage 的搬运半边,
        2026-07-10):move + @module 重指。**move 不是 copy** —— 召回后 staging
        是源码唯一副本(cancel 的 entered_at 守卫由此而来)。资格校验/产物回收/
        状态转移仍是 restage 的政策,不在此处。
        """
        paths = self.paths(name)
        if not paths.src.exists():
            raise FileNotFoundError(f"{paths.src} 不存在")
        if paths.staging.exists():
            raise FileExistsError(f"{paths.staging} 已存在,拒绝覆盖")

        paths.staging.parent.mkdir(parents=True, exist_ok=True)
        clean_pycache(paths.src)
        shutil.move(str(paths.src), str(paths.staging))
        rewrite_module_path(paths.staging)

    def unstage(self, name: str) -> bool:
        """删 staging/<name> 整个目录(cancel / clear / rm 共用)。
        返回 True 表示目录存在且已删。"""
        d = self.paths(name).staging
        if not d.exists():
            return False
        shutil.rmtree(d)
        return True

    def purge_artifacts(self, name: str, scope: ArtifactScope) -> list[str]:
        """按面清产物,返回已删项标签(调用方负责打印/记账)。

        CHECK 面(pnl + bcorr 池副本,均单文件):离库即失效一律回收 ——
        旧 pnl 留在池里是"自鬼影"(重检时新 pnl 对自己旧 pnl corr≈1,高相关
        分支要求打败几乎相同的自己 → 必拒),也让别的新因子撞上已离库因子的
        旧 pnl(JOURNAL PV7)。两个池都查 —— 因子来源可能在历史上变过。

        SERVING 面(dump 目录 + feature 单文件):last-known-good,rm /
        restage --purge / REJECTED 召回才清。
        """
        removed: list[str] = []
        p = self.paths(name)
        if ArtifactScope.SERVING in scope:
            if p.dump.exists():
                shutil.rmtree(p.dump)
                removed.append(f"alpha_dump/{name}")
            for f in p.features:
                if f.exists():
                    f.unlink()
                    removed.append(f"alpha_feature/{f.name}")
        if ArtifactScope.CHECK in scope:
            if p.pnl.exists():
                p.pnl.unlink()
                removed.append(f"alpha_pnl/{name}")
            for pool_copy in p.pools:
                if pool_copy.exists():
                    pool_copy.unlink()
                    removed.append(f"{pool_copy.parent.name}/{name}")
        return removed
