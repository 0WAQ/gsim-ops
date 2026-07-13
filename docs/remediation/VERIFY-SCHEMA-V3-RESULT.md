# schema v3 生产迁移执行结果(2026-07-13,执行者于 160;判读方收录)

四阶段完成,验收标准达成:**zxu correlation-rejected 行出指标+delay**、
Total 8252 不变、doctor exit=0、created_at 违反行归零。

| 项 | 结果 |
|---|---|
| 门禁 | 174 passed, 0 skipped |
| 备份 | 4.2M 三表(info/snapshot/history),未动用 |
| [A] created_at 修正 | 730 行(分桶:07-09 542 + 07-10 168 = 97% fguo 批,06-18/26 零星 20)+ 合成 submit 事件同步 730 行 |
| [B] 测得快照回填 | 738 条(snapshot 总数 7472 → 8210);fitness 日期脏值隔离 39 |
| doctor | exit=0;回填 738 条 738 matched / 0 mismatch;新判据抓出 472 条**历史存量**漂移(旧批量入库 snapshot_at 偏差:fguo 统一 16:38:27、lhw 差 8h)→ 挂 legacy 清理批 |

## 两个执行期缺陷(均我方脚本,执行者按红线停贴抓获)

1. **fitness 日期错位脏值**(dry-run 抓获):老 fail_reason 携带
   fitness=20150115 这类值(疑似当年 simsummary 负收益行列错位)——
   84e89b5 加字段级合理域,越界字段隔离置 None(39 个,全在 fitness);
2. **apply 零持久化**(首跑 apply 抓获):psycopg3 非 autocommit 下首个
   execute 隐式开外层事务 → `with conn.transaction()` 退化 savepoint →
   close() 整体回滚,**打印行数正确但数据全丢** —— 9476150 改
   autocommit=True + 本地沙盘补测 apply 全路径(新连接验证持久化 + 幂等)。

## 纪律沉淀

- **迁移脚本的 apply 验证必须用新连接查库,不信打印**(执行者以此抓获缺陷 2);
- Python 迁移脚本与 SQL 脚本同等对待:**apply 路径必须沙盘实测**
  (v3 首版只测了解析器,是流程缺陷);
- 手册锚点须注明统计范围(730 vs 81 的虚惊 = 全库计数 vs 近期批计数)。

## 遗留(挂 legacy 清理批)

- 472 条历史 snapshot_at 漂移(新判据的存量发现,report-only);
- compliance 22 条可选补跑 simsummary 回填;
- fguo 07-10 批量写作用面实为 ~710(非 81),成因仍不追。
