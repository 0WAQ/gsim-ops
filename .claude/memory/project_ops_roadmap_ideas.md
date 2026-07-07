---
name: ops-roadmap-ideas
description: Future feature ideas for the factor Ops system from user's career planning doc (ops.md)
type: project
originSessionId: a8aa7342-1004-49e0-b73c-995062472e90
---
用户在 ops.md 中规划的因子 Ops 系统未来方向:

**因子质量监控** (最高优先级, 最容易出彩):
- Rolling IC / IC_IR: 20日/60日滚动 IC, 跌破阈值告警
- 因子自相关: 突然飙升 → 因子可能"死了"
- 截面分布: 偏度/峰度异常 → 数据有问题
- 覆盖度: 有多少股票有值, 突然下降 → 数据源挂了
- 因子相关性矩阵监控 (新因子是否真的"新")

**计算编排**:
- 因子计算 DAG (有依赖关系的因子)
- 增量更新 vs 全量重算
- 失败重试、告警 (类似 Airflow/Dagster)

**服务化**:
- 因子查询 API (FastAPI)
- 缓存层 (Redis)
- 权限管理

**可视化**:
- Grafana / Streamlit Dashboard
- 因子详情页、监控大盘

**Why:** 用户希望从"运维工具"发展成研究员离不开的内部产品, 提升工作价值和职业发展。
**How to apply:** 新功能开发时优先考虑质量监控方向; 架构设计考虑未来 API 化和可视化扩展。
