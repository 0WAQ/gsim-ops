# AI 协作功能路线图

## 概述
为 gsim-ops 项目构建 AI 协作功能，自动化因子开发、审查和维护流程。

## 已实现功能(2026-07-20 更新)

技能本体在 `.claude/skills/`(kimi 经 `.kimi-code/skills` 软链接入,双兼容):

- **工作流技能 ×10**:`review-staging`(staging 审查)/ `check-factor`(因子体检)/
  `analyze-failure`(失败归因)/ `analyze-report`(check 报告汇总)/ `audit-cc`
  (cc 质量审计)/ `compare-cc`(cc 跨根比对)/ `verify-data-claim`(数据反馈核实)/
  `audit-docs` / `sync-docs` / `commit`
- **角色技能 ×6**(claude agent 转 skill):factor-analyst(因子深析)/
  cc-data-auditor / report-analyst / pipeline-debugger / docs-auditor / docs-updater

计划功能与本清单的覆盖关系在各条目标注。

## 计划功能

### 1. 自动化技能

#### `/auto-check` - 完整检查流程
- 自动运行 `ops check` 对 staging 中的所有因子
- 并行处理多个因子以提高效率
- 生成汇总报告，包括：
  - 通过/失败的因子列表
  - 性能指标（IR, turnover, bcorr 等）
  - 潜在问题警告

#### `/analyze-factor` - 深度因子分析(已大部覆盖:`factor-analyst` / `check-factor` 两技能)
- 输入：因子名称
- 分析内容：
  - 历史表现趋势
  - 与其他因子的相关性
  - 特征分布和异常值
  - 优化建议
- 输出：详细的分析报告

#### `/compare-factors` - 因子对比(cc 数据域已覆盖:`compare-cc`;因子间对比仍待做)
- 输入：多个因子名称
- 对比维度：
  - 性能指标（IR, Sharpe, turnover）
  - 相关性矩阵
  - 特征重叠度
  - 适用场景
- 帮助决策：保留哪些因子，淘汰哪些

### 2. 智能审查功能

#### 自动问题检测
- **Pack offset bug 检测**
  - 检查因子是否使用了 delay 参数
  - 如果 delay > 0，警告可能的特征错位问题
  - 参考：commit e429e11 中记录的 bug
  
- **数据质量检查**(cc 域已覆盖:`audit-cc` 全字段扫描 + `verify-data-claim` 单点核实)
  - 缺失值比例
  - 异常值检测
  - 时间序列连续性
  
- **性能异常检测**
  - IR 突然下降
  - Turnover 异常升高
  - 与历史表现的偏离

#### 优化建议生成
- 基于因子表现提供具体建议：
  - 参数调优方向
  - 特征工程改进
  - 组合策略建议

#### 审查报告生成(已覆盖:`analyze-report` 出批量汇总 + QR 反馈稿)
- 自动生成结构化的审查报告
- 包含：问题列表、风险评估、改进建议
- 支持导出为 markdown 或 PDF

### 3. 工作流自动化

#### 端到端流程自动化
```
开发 → 提交 → 审查 → 检查 → 上线
  ↓      ↓      ↓      ↓      ↓
 AI辅助  自动   智能   并行   监控
       staging 审查   验证   报告
```

#### 批量处理
- 批量提交多个因子
- 批量运行检查
- 批量更新因子参数

#### 定期维护任务
- 每日/每周自动检查所有 active 因子
- 性能退化预警
- 自动生成维护报告

### 4. 协作增强功能

#### 因子知识库
- 自动记录每个因子的：
  - 设计思路和假设
  - 历史修改记录
  - 性能变化趋势
  - 相关讨论和决策
  
#### 智能问答
- 基于项目历史和文档回答问题
- 例如："为什么 factor_xyz 的 IR 下降了？"
- 例如："哪些因子使用了 momentum 特征？"

#### 代码审查助手
- 审查因子代码的：
  - 代码质量和风格
  - 潜在的性能问题
  - 最佳实践建议

## 实现优先级

### P0 (高优先级)
1. `/auto-check` - 最常用的功能
2. Pack offset bug 自动检测 - 已知的关键问题

### P1 (中优先级)
3. `/analyze-factor` - 深度分析单个因子
4. 性能异常检测
5. 审查报告生成

### P2 (低优先级)
6. `/compare-factors` - 因子对比
7. 批量处理功能
8. 因子知识库
9. 智能问答

## 技术考虑

### 性能优化
- 使用并行处理（ProcessPoolExecutor）处理多个因子
- 缓存计算结果（如 bcorr）避免重复计算
- 增量更新而非全量重算

### 数据存储
- 分析结果若需持久化走 Postgres(PG 是唯一真相源,项目无 SQLite;先文件后评估入库,参照 compliance 测量'先文件不进 PG'前例)
- 考虑添加表：
  - `factor_analysis` - 分析历史
  - `factor_issues` - 检测到的问题
  - `factor_suggestions` - 优化建议

### 集成方式
- 通过 skills 系统集成(Claude Code / Kimi Code 双兼容,kimi 经 `.kimi-code/skills` 软链)
- 保持与现有 `ops` 命令行工具的兼容性
- 可以独立使用，也可以组合使用

## 下一步行动

当需要实现某个功能时：
1. 从路线图中选择一个功能
2. 创建详细的实现计划
3. 开发和测试
4. 更新文档
5. 标记为已完成

---

*最后更新：2026-07-20*
*维护者：wbai*
