# 论文按需细节请求

本文件只处理 `PAPER_WRITING_BRIEF.md` 无法回答的局部问题。它不是论文写作 Agent 或实现 Agent 的启动必读文件，也不是完整 code map、实验状态数据库或论文目录。

## 使用规则

1. 论文写作者只在某个具体段落确实需要更细的实现、运行或统计事实时新增一个描述性标题，不使用数字编号。
2. 每个请求只问一个会影响当前论文表达的问题，写清论文位置、所需颗粒度和为什么简报不足。
3. 实现侧在同一条目下直接回答；只定点读取必要代码、测试或实际输出，不展开无关项目历史。
4. 回答包含行为解释、必要代码位置、必要验证、限制和核验日期。只有在确实帮助论文解释时才给短代码片段或伪代码。
5. 如果回答改变了系统或实验的总体理解，同时更新 `PAPER_WRITING_BRIEF.md`；如果只是局部细节，不把它扩写进简报。
6. 状态只使用 `open`、`answered`、`integrated`、`closed`、`stale` 或 `blocked`。当共同简报已经完整回答请求时可标记为 `closed`；代码或实验事实变化后，受影响的旧回答标记为 `stale`。
7. 在此提交请求只授权只读取证；不自动授权修改代码、调用真实 API、启动 Representative/Full 或改变实验设施。

## 已关闭问题

### 最终 Slim V2 实验结果与可进入论文的表格

- 状态：`closed`
- 关闭依据：`PAPER_WRITING_BRIEF.md` 第 7 节已记录 final run `slim-v2-full-flash-20260823-233000-b4c8e951`、正式 reducer 产物、7,017 个论文分母 root、106 个 Experiment 3 auxiliary references、五组实验表格规模、provider-call 边界及缺失值限制；该简报继续作为论文写作的默认事实入口。
- 论文位置：Experimental Tasks and Evaluation Design、Scaling、Robust Testing、Experimental Analysis、Artifact Availability。
- 问题：当前工作树对应的有效 Slim V2 run、冻结表格以及可进入正文或 supplemental 的结果分别是什么？
- 需要的颗粒度：
  - Experiment 1–5 各自的实际运行状态；
  - 实际 run ID、profile/condition、输出目录和 reducer 产物；
  - 候选表格的指标口径、分母、单位和有效性；
  - 正文结果、supplemental、provisional、stale、invalid 和尚未运行的明确区分；
  - 实际 provider、模型、pricing version 和排除项。
- 当前限制：只允许读取已有结果；不得启动真实 API、重跑 Representative/Full 或修改实验设施。旧 Rxx、legacy paper/formal 输出和设计计划不能冒充当前结果。

## 新问题模板

```markdown
### 用一句话描述需要确认的细节

- 状态：`open`
- 论文位置：具体 section、段落、表格或公式。
- 问题：一个可核验、会影响论文表达的问题。
- 为什么简报不足：缺少的具体信息。
- 需要的颗粒度：行为逻辑、代码位置、短伪代码、测试、run 或指标。
- 授权边界：默认只读；任何额外动作需单独说明。

#### 实现侧回答

- 核验日期：
- 行为或实验事实：
- 代码或输出位置：
- 必要验证：
- 限制与失效条件：
```
