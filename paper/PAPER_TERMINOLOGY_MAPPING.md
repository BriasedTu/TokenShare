# 论文术语转换表

本表供论文写作 AI 使用。`PAPER_WRITING_BRIEF.md`、`IMPLEMENTATION_DETAILS.md` 和 `DETAIL_REQUESTS.md` 为了准确对应代码、实验和内部文档，会保留项目内部术语；论文正文不应机械照搬这些表达，而应按照本表转换为普通学术读者能够直接理解的语言。

本表只转换表达，不改变原文档所陈述的行为、范围、证据或限制。某个词被收录在表中，也不表示相应内容一定应写入论文；是否保留该内容仍由论文的研究问题和证据需要决定。表中用 `/` 分隔的表达是按语境选择的备选写法，不应全部堆在同一句中。

## 可以直接用于论文的通用术语

以下术语属于常见系统术语，且对准确说明本文机制有必要，可以直接保留：

| 术语 | 论文中的含义 |
|---|---|
| task graph / DAG | 任务及其依赖关系构成的有向无环图 |
| worker | 承担一次任务执行的工作节点或执行资源 |
| executor | 实际调用模型、工具或本地程序完成任务的执行组件 |
| attempt | 对某项任务进行的一次具体执行 |
| lease | 在限定时间内授予某次 attempt 的执行资格 |
| parser | 将 executor 输出转换为可检查结构的解析组件 |
| verifier | 判断候选结果是否满足任务要求的验证组件 |
| retry | 在失败、超时或结果无效后重新执行任务 |

## 内部表达与论文统一表达

| 内部/偏抽象表达 | 论文统一表达 |
|---|---|
| protocol lifecycle | execution flow / protocol flow |
| protocol kernel | protocol coordinator |
| canonical result / canonical output | accepted result |
| canonical binding | accept the result / record it as the task result |
| canonicalization | result acceptance |
| canonical state | accepted result record / recorded accepted result |
| canonical pollution | acceptance of an invalid result / contamination of accepted results |
| execution authority | valid attempt / valid lease |
| close old authority | invalidate the old attempt |
| fresh authority | new attempt with a new lease |
| composition | merge / combine results |
| composition readiness | required results are available for merging |
| evidence gate | verification step |
| evidence layer | execution records / audit records |
| root check | final verification |
| task unit | task / subtask |
| root task / root unit | top-level task |
| root result / root output | final result / final output |
| artifact | output / stored result |
| trace / unit trace | execution record |
| fixed trace | replayed execution record |
| required slot / slot | required child result |
| terminal state | final state / completed or failed state |
| task plugin / plugin | task-specific module |
| provider | model service / API service |
| provider call | model call / API call |
| event ledger / ledger | append-only event log / execution log |
| projector | state-reconstruction logic |
| projection | reconstructed state |
| reducer | metric calculation / result aggregation |
| merge gate | check that all required results are available before merging |
| coverage tail | additional executions needed by later experiments |
| source closure | complete set of required source results |
| publication gate | completeness check before reporting results |
| paper-eligible | included in the reported analysis |
| schema | specified data format / input and output format |
| contract | interface rules / input and output requirements |

## 使用原则

1. 先依据交接文档理解真实行为，再转换用语；不能为了表达通俗而改变事实。
2. 优先使用描述动作的普通词，如 `accept`、`record`、`invalidate`、`merge` 和 `verify`，不要把内部名词扩写成论文中的新理论概念。
3. 同一概念在全文使用同一表达。若表中提供多个备选词，应根据句子含义选择其中一个，并在后文保持一致。
4. 遇到本表未收录的内部术语时，应说明它实际执行的行为，而不是直接复制内部名称或自行创造新的抽象名词。
