# 论文工作区 AGENTS.md

本目录是 TokenShare 论文写作工作区。论文写作者的目标是依据当前实现和有效实验事实形成清楚、可辩护的论文，而不是管理代码仓库或维护工程证据数据库。

## 统一交接体系

论文侧和仓库侧使用同一套两级交接：

1. **默认共同基线**：`PAPER_WRITING_BRIEF.md`。它解释研究对象、系统与实验事实、未实现边界、写作目标和教授准则。
2. **按需下钻**：
   - `IMPLEMENTATION_DETAILS.md` 保存已经核验的代码级实现参考，只在具体段落需要更细颗粒度时读取。
   - `DETAIL_REQUESTS.md` 处理简报和现有细节参考仍无法回答的局部问题。问题与实现侧回答写在同一条目中，不使用数字编号体系。

`PAPER_WRITING_BRIEF.md` 是唯一默认交接入口，但不是底层事实的替代品。当前代码、实际实验输出和权威指标口径高于任何交接文档。

## 论文写作 Agent 启动流程

1. 确认当前工作目录是 `paper/`。
2. 完整阅读本文件、`PAPER_WRITING_BRIEF.md` 和 `PAPER_TERMINOLOGY_MAPPING.md`。前者提供系统与实验事实基线，后者只负责把内部项目用语转换为论文读者能够理解的表达，不能改变事实含义。
3. 定位本轮需要处理的 `paper.tex` 章节；不要因为其他章节存在问题就自动扩大任务。
4. 只有当前段落确实需要代码级依据时，才读取 `IMPLEMENTATION_DETAILS.md` 对应部分。
5. 如果仍缺少会改变论文表达的事实，在 `DETAIL_REQUESTS.md` 新增一个描述性问题；不要自行递归扫描父目录、源码树、历史实验或旧 paper/formal pipeline。

不把根目录 `AGENTS.md`、完整 Slim V2 设计文档或源码树作为论文写作启动必读项。

## 论文写作 Agent 的权限与职责

- 可修改 `paper.tex`、`reference.bib` 和本目录中明确属于论文的源文件。
- 默认不得修改 `paper/` 之外的代码、配置、实验设施或状态文件。
- 默认不得运行真实 API、Representative、Full、训练或其他有成本实验。
- `paper.bbl` 和 `out/` 是生成物，除非用户明确要求，否则不要手工修改。
- 先交付正文或可供作者质疑的写作版本，不用泛化建议替代写作任务。
- 不在正文中保留内部请求编号；`DETAIL_REQUESTS.md` 只是两个工作区之间的临时交接。

## 事实来源优先级

1. 实验数值和结论：当前有效 run 的原始输出、run metadata 和 reducer 产物。
2. 指标和统计口径：`Doc/SlimV2/slim_v2_experiment_metrics_authority.md`。
3. 实际系统行为：当前工作树实现、配置和必要的 focused verification。
4. 设计范围和非目标：Slim V2 设计宪章与系统接线合同。
5. `PAPER_WRITING_BRIEF.md` 和 `IMPLEMENTATION_DETAILS.md`：实现侧核验后的压缩解释。
6. `paper.tex` 当前文字：待核验表达，不是事实来源。

发生冲突时，不自行选择方便的版本。若冲突会改变当前段落，在 `DETAIL_REQUESTS.md` 提出一个具体问题。

## 按需下钻规则

- 问题必须对应具体论文位置和一个可核验事实。
- 实现侧回答应说明行为、必要代码或输出位置、验证、限制和核验日期。
- 如果答案改变系统或实验的总体理解，同步更新 `PAPER_WRITING_BRIEF.md`。
- 如果答案只服务某个局部段落，保留在 `DETAIL_REQUESTS.md`，不要让简报无限膨胀。
- 代码、配置、实验输出、模型、pricing 或 reducer 变化后，受影响的局部答案标记为 `stale`。

## 写作边界

- 当前正文不能反向证明实现或实验事实。
- 设计计划不能证明实验已经完成。
- 代码支持、Representative 观察和 Full 结论必须使用不同语气。
- 从交接文档向论文正文转写时，必须遵守 `PAPER_TERMINOLOGY_MAPPING.md`；不得把为对应代码而保留的内部项目术语直接写入论文。
- 不能把本地原型写成真实区块链、开放分布式网络、真实 token 支付或 Byzantine fault-tolerant production system。
- 本地与 Overleaf 不假定自动同步；任何同步前先确认哪一侧最新，避免覆盖。
