# Phase 5 外部系统合并机制调研备忘

日期：2026-06-25

状态：Phase 5 brainstorm 调研材料。本文不是实现规格，只记录外部系统中和 TokenShare Phase 5 merge 主闭环相关的可借鉴边界。后续 Phase 5 字段规格如采用本文结论，应再把具体字段、事件、SQLite projection 和测试计划写入 Phase 5 专用规格。

## 1. 调研范围

本轮只关注以下问题：

- fan-out / fan-in 后何时触发合并。
- 合并是否应建模为普通任务，还是协调器特权步骤。
- 合并输入如何绑定上游正式输出或任务结果。
- 重试、重复执行和迟到输出如何避免重复计数或重复结算。
- 哪些复杂机制不应带入 TokenShare V1。

不研究真实分布式 runtime、生产 worker pool、真实链上结算、完整搜索引擎或生产级资源调度。

## 2. 本地已落库参考

`reference_repos/README.md` 已记录以下本地浅克隆 / sparse checkout：

- Temporal Python SDK：关注 workflow / client / worker / replay 边界。
- Luigi：关注 task / target / worker / scheduler 边界。
- cwltool：关注 typed workflow、executor、job、path mapping。
- Prefect：关注 events / workers / results / states 分层。
- Dagster：关注 core storage / events / execution / executor 内部边界。

这些仓库只作为结构和边界参考，不作为 TokenShare runtime dependency。

## 3. 联网资料摘要

### 3.1 Temporal

来源：

- Temporal Event History 文档，来源 URL：`https://docs.temporal.io/workflows`，访问日期：2026-06-25。
- Temporal Child Workflows 文档，来源 URL：`https://docs.temporal.io/child-workflows`，访问日期：2026-06-25。

观察：

- Temporal 把 Workflow Execution 的生命周期记录为完整 durable Event History，replay 依赖历史事件恢复进度。
- Child Workflow 会增加更多 history events；文档建议只有在确有 lifecycle / ownership / history size 需求时才使用 child workflow，而不是为了组织代码而拆。

对 TokenShare Phase 5 的影响：

- merge 如果作为普通 `TaskUnit`，会增加事件数量，但换来统一 lifecycle、retry、attempt、verification 和 canonical 语义。
- TokenShare V1 规模小且研究目标是验证协议闭环，接受更多事件是合理的；但 Phase 5 规格应明确 merge `TaskUnit` 的必要性，避免未来把每个内部函数都变成协议任务。
- replay 不能调用插件 merge 来补历史事实；必须从 `MERGE_RECORDED`、merge submission、verification、canonical 和 artifact 读取。

### 3.2 Celery

来源：

- Celery Canvas 文档，来源 URL：`https://docs.celeryq.dev/en/stable/userguide/canvas.html`，访问日期：2026-06-25。

观察：

- Celery chord 是典型 fan-in：header group 全部完成后，body callback 执行，并接收 header 结果列表。

对 TokenShare Phase 5 的影响：

- `MergePlan.required_slots` 可以类比 chord header，merge `TaskUnit` 可以类比 body callback。
- TokenShare 不能只检查“任务完成”，必须检查每个 required slot 绑定的是 canonical child output，并记录 child output hash。
- callback 输入顺序在 Celery 里是任务组顺序；TokenShare 应使用 stable `slot_key` / `source_child_logical_key`，不要依赖事件到达顺序。

### 3.3 Dask

来源：

- Dask Futures 文档，来源 URL：`https://docs.dask.org/en/stable/futures.html`，访问日期：2026-06-25。
- Dask Task Graphs / Delayed 文档，来源 URL：`https://docs.dask.org/en/latest/graphs.html`，访问日期：2026-06-25。
- Dask High Level Graphs 文档，来源 URL：`https://docs.dask.org/en/latest/high-level-graphs.html`，访问日期：2026-06-25。

观察：

- Dask 允许把 future 作为后续 task 输入；依赖完成后调度器再运行下游 task。
- Dask task graph 将函数与参数组织成图；Delayed 会先构图，后执行。

对 TokenShare Phase 5 的影响：

- `ExpectedOutputRef` 应作为 output future / resolution，不应藏在 `TaskUnit.metadata` 或插件 payload。
- merge readiness 应由 projection 从 authoritative events 重建：required slot 的 future 是否已经被 canonical output resolved。
- Phase 5 可增加 slot-level 查询表，但权威事实仍是 event + artifact。

### 3.4 Airflow

来源：

- Apache Airflow Tasks 文档，来源 URL：`https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html`，访问日期：2026-06-25。
- Apache Airflow Concepts / trigger rules 文档，访问日期：2026-06-25。
- Apache Airflow XCom / TaskFlow 概念文档，访问日期：2026-06-25。

观察：

- Airflow 的默认 trigger rule 是所有直接上游成功后再触发下游。
- Airflow 用 XCom 在任务间传小型输出，并可自动增加依赖以保证 XCom 可用。

对 TokenShare Phase 5 的影响：

- `MergePlan.required_slots` 第一版等价于非常保守的 `all_required_canonical` trigger rule。
- TokenShare 不应采用 Airflow 的多 trigger rule 复杂度；`optional_slots`、`all_done`、`one_success` 等留到未来。
- TokenShare 输出不是小型 XCom，而是 immutable artifact refs + bundle digest；merge 输入必须记录 hash。

### 3.5 MapReduce

来源：

- Google MapReduce OSDI 2004 paper / USENIX 页面，来源 URL：`https://research.google.com/archive/mapreduce-osdi04.pdf`，访问日期：2026-06-25。

观察：

- MapReduce 把 reduce 明确建模为收集同一 key 的中间结果后进行合并。
- 论文讨论 backup tasks / re-execution 导致 duplicate executions，因此聚合 counters 时 master 会消除重复执行影响，避免 double counting。

对 TokenShare Phase 5 的影响：

- Phase 5 settlement / metrics 必须以 canonical attempt 或 accepted contribution identity 为准，不能按所有 execution attempts 计奖励。
- shadow attempt、重试、迟到提交可以保留审计，但默认不能重复奖励。
- merge input bundle 应记录 child canonical output hash，用于解释父输出来自哪些正式结果。

### 3.6 Spark

来源：

- Apache Spark RDD Programming Guide，来源 URL：`https://spark.apache.org/docs/latest/rdd-programming-guide.html`，访问日期：2026-06-25。
- PySpark `RDD.reduceByKey` API 文档，访问日期：2026-06-25。

观察：

- Spark `reduceByKey` 要把相同 key 的值合并到一起，必要时跨分区移动数据。
- `reduceByKey` 要求 reduce function 具有关联和交换性质，并会先在 mapper 本地合并。

对 TokenShare Phase 5 的影响：

- TokenShare 的插件 `MergePolicy` 不一定具有关联/交换性质，尤其 structured report merge、Lean proof merge 可能顺序和 slot 语义敏感。
- Phase 5 第一版不应实现通用 combiner、partial merge 或 tree aggregation；只实现 `MergePlan.required_slots` 的一次性 deterministically ordered merge。
- 如果未来插件声明 merge policy 具有关联/交换/幂等属性，再引入优化；第一版只记录 policy identity 和 input hash。

## 4. 对 Phase 5 的初步设计倾向

以下倾向已经在后续 Phase 5 讨论中确认；确认记录见 `Doc/TechnicalDocument/2026-06-25-phase-5-merge-discussion-notes.md`。

1. `MergePlan.required_slots` 是 merge readiness 的唯一触发依据。
2. merge 是普通 `TaskUnit`，走 request / submission / verification / canonical 生命周期。
3. `MergeCoordinator` 只做 readiness、merge task 创建、input bundle artifact、slot/hash 审计和事件记录，不把领域 merge 算法写进协议核心。
4. `MergeRecord` 记录一次 merge 事实，但不替代 merge task 的 canonical output；后续已确认 `MERGE_RECORDED` 只作为 canonical-level merge commitment。
5. contribution / settlement 第一版只按 canonical / merge success / root completed 的权威事件生成最小可审计记录，避免 attempt 重试或 shadow execution 重复计奖；后续已确认 contribution 使用 canonical taxonomy，settlement 使用 root-level batch。
6. subtree pruning 只在父节点完成后取消未完成且不再需要的子树；已完成 canonical output 不回滚；后续已确认 early-success / partial merge / factorization early pruning 不进入 Phase 5 V1。

## 5. 已确认的创建时机

Phase 5 讨论已确认采用 A 方案：required slots 齐备后再创建 merge `TaskUnit`。

第一版不在 Phase 4 `expand` batch 中预创建 blocked merge `TaskUnit`。这样可以保持 Phase 4 已冻结的 `expand` batch 顺序不变，并让真实 merge work 只在所有 required child canonical outputs 齐备后出现。

## 6. 已确认但仍需字段化的问题

下列问题已在 `Doc/TechnicalDocument/2026-06-25-phase-5-merge-discussion-notes.md` 中确认；本备忘只保留外部系统经验索引，后续仍需在 Phase 5 字段规格中展开字段、事件、projection 和测试：

1. merge `TaskUnit` 与 parent unit 的关系类型：独立 `MergeTaskLink` / `merge_task_links` projection 为主，可选窄义 `TaskRelation(kind=merge_of)`。
2. `MergeRecord` 与 merge task canonical output 的关系：`MERGE_RECORDED` 是 canonical-level merge commitment，只在 merge `TaskUnit` 已有 `CANONICAL_OUTPUTS_BOUND` 后写入。
3. `ExpectedOutputRef.resolution_status` 的状态枚举和 resolved event：权威状态只用 `expected` / `resolved`，新增独立 `EXPECTED_OUTPUT_RESOLVED`，并与 `MERGE_RECORDED` 通过 `merge_resolution_batch:{merge_record_id}` 原子提交。
4. `ContributionRecord` 最小类型集合：`complete_canonical`、`expand_canonical`、`merge_canonical`；redundant verification 延期。
5. `SETTLEMENT_RECORDED` 事件形态：root-level `settlement_batch:{task_id}:{root_unit_id}:{root_completion_event_seq}`，先 settle contribution，最终 marker 为 `SETTLEMENT_RECORDED`。
6. subtree pruning authority：来自插件声明的 versioned pruning policy，优先挂在 `MergePolicy` / `MergePlan` 或未来 terminal-resolution policy；complete decision 只能引用已声明 policy，不能自由声明 prune scope。

## 7. 关系形态调研：merge `TaskUnit` 与 parent / child 如何关联

本节对应 Phase 5 当前讨论点：merge `TaskUnit` 与 parent / child 的关系形态应放在 `TaskRelation`、merge task payload / creation event，还是独立 merge link / projection。

来源：

- Apache Airflow Tasks 文档，来源 URL：`https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html`，访问日期：2026-06-25。
- Dagster Op Graphs 文档，来源 URL：`https://docs.dagster.io/guides/build/ops/graphs`，访问日期：2026-06-25。
- Luigi Tasks 文档，来源 URL：`https://luigi.readthedocs.io/en/latest/tasks.html`，访问日期：2026-06-25。
- Argo Workflows DAG 文档，来源 URL：`https://argo-workflows.readthedocs.io/en/latest/walk-through/dag/`，访问日期：2026-06-25。
- Argo Workflows Enhanced Depends 文档，来源 URL：`https://argo-workflows.readthedocs.io/en/latest/enhanced-depends-logic/`，访问日期：2026-06-25。
- Dask Task Graph Specification，来源 URL：`https://docs.dask.org/en/stable/spec.html`，访问日期：2026-06-25。
- Dask High Level Graphs 文档，来源 URL：`https://docs.dask.org/en/latest/high-level-graphs.html`，访问日期：2026-06-25。
- CWL Workflow Description v1.2.1，来源 URL：`https://www.commonwl.org/v1.2/Workflow.html`，访问日期：2026-06-25。
- CWL User Guide Workflows，来源 URL：`https://www.commonwl.org/user_guide/topics/workflows.html`，访问日期：2026-06-25。
- Prefect Tasks 文档，来源 URL：`https://docs.prefect.io/v3/concepts/tasks`，访问日期：2026-06-25。
- Prefect concurrent workflows 文档，来源 URL：`https://docs.prefect.io/v3/how-to-guides/workflows/run-work-concurrently`，访问日期：2026-06-25。

观察：

- Airflow 把 task 组织为 DAG 中的 upstream / downstream 依赖，并明确 task 默认不传递信息；如果要传信息，需要使用 XCom。经验：顺序依赖和数据传递不是同一个东西。
- Dagster op graph 用 op input 到其他 op output 的 dependency structure 表达依赖。经验：输入端口和上游输出的绑定是 first-class，而不是只有无语义的 task-to-task 边。
- Luigi 的 `requires()` 返回依赖 task，`input()` 将这些依赖转换成 `Target`，并支持 dict / list / nested 结构。经验：fan-in 输入最好保持结构化映射，不能只靠一组无名边。
- Argo DAG 允许 task 声明 dependencies；增强 `depends` 还能表达依赖某个 task 的具体结果状态。经验：如果 readiness 依赖 canonical / failed / skipped 等结果状态，普通 dependency edge 不够，需要有结果状态或结果事实的显式条件。
- Dask 低层 graph 通过 task 参数引用其他 key，高层 graph 又保存 layer dependency structure。经验：数据引用可以驱动执行依赖，高层分组关系可以作为查询和优化视图。
- CWL step input 的 `source` 把 workflow input 或上游 step output 连接到具体 step input；workflow output 通过 `outputSource` 连接到 step output。经验：input/output mapping 比父子边更适合表达数据 lineage。
- Prefect 自动根据 future / result 输入建立 state dependency，也支持 `wait_for` 只表达等待而不传数据。经验：数据依赖和纯状态依赖应分开。

对 TokenShare Phase 5 的影响：

- 不能只用 `TaskRelation` 承载所有 merge 语义。`TaskRelation` 适合表达图拓扑和调度依赖，但 required slot、child canonical output hash、canonical selection、merge input bundle 这些是数据 lineage / audit facts。
- 不能只放在 `TaskUnit.metadata` 或 plugin payload。TokenShare 已确认 metadata 不是 replay、merge readiness 或父节点完成判断的权威来源。
- Phase 5 应引入显式 merge link / merge task creation fact，用于绑定 `parent_unit_id`、`merge_plan_id`、`merge_unit_id`、`merge_input_bundle` 和 required slot bindings。
- SQLite 可以有 `merge_task_links` 或等价 index-only projection 表，供 readiness、parent resolution 和 audit 查询使用；权威事实仍来自 JSONL event 和 artifact。
- 如需图遍历可额外记录窄义 `TaskRelation(kind=merge_of)`，但它不应承载 slot coverage 或 canonical hash 细节。

本轮结论：

- 该 relation-shape 口径已在 2026-06-25 Phase 5 讨论中确认。采用以 C 为主的混合方案：新增独立 `MergeTaskLink` / `merge_task_links` projection，权威来源是 merge task 创建事件或 merge readiness / task-created batch；必要时再记录一个窄义 `TaskRelation(kind=merge_of)` 用于任务图遍历。
- 不采用单纯 A：仅扩展 `TaskRelation` 会把数据 lineage、slot coverage、canonical digest 和拓扑边混在一起。
- 不采用单纯 B：只放在 merge task payload / metadata / creation event，后续 projection 查询、parent resolution 和 replay audit 会缺少稳定关系对象。

## 8. 创建事件调研：ready fact 还是 merge task creation batch

本节对应 Phase 5 当前讨论点：`MergeCoordinator` 在 required slots 全部 canonical 后，是否应该先写独立 `MERGE_READY_RECORDED`，还是直接写 merge task creation 相关事件。

来源：

- Temporal Events and Event History 文档，来源 URL：`https://docs.temporal.io/workflow-execution/event`，访问日期：2026-06-25。
- Temporal Event History 文档，来源 URL：`https://docs.temporal.io/encyclopedia/event-history`，访问日期：2026-06-25。
- Apache Airflow Tasks 文档，来源 URL：`https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html`，访问日期：2026-06-25。
- Celery Canvas / chord 文档，来源 URL：`https://docs.celeryq.dev/en/stable/userguide/canvas.html`，访问日期：2026-06-25。
- Argo Workflows DAG 文档，来源 URL：`https://argo-workflows.readthedocs.io/en/latest/walk-through/dag/`，访问日期：2026-06-25。
- Argo Workflows Enhanced Depends 文档，来源 URL：`https://argo-workflows.readthedocs.io/en/latest/enhanced-depends-logic/`，访问日期：2026-06-25。
- Dask Distributed Scheduler State Machine 文档，来源 URL：`https://distributed.dask.org/en/latest/scheduling-state.html`，访问日期：2026-06-25。
- Prefect States / Task Run Events 文档，来源 URL：`https://docs.prefect.io/v3/concepts/states` 和 `https://docs.prefect.io/v3/api-ref/events/task-run-events`，访问日期：2026-06-25。

观察：

- Temporal 把 Workflow lifecycle 记录为 durable event history；当 workflow 发出执行 Activity 的 command 后，服务端追加 `ActivityTaskScheduled`，该 scheduled event 同时导致对应 task 进入 task queue。经验：对 event-sourced 系统而言，真正重要的是“work 已被创建/调度”的 durable commitment，而不是单独记录一个可由依赖推导的 ready hint。
- Airflow 和 Prefect 都有 task run state / task instance state，用 `scheduled`、`queued`、`pending`、`running`、`completed` 等状态增强观测和调度可见性。经验：运行态系统可以显式记录 scheduled/pending，但这些状态服务 runtime observability，不等同于协议级输入 lineage。
- Celery chord 的 body callback 在 header group 全部完成后执行。经验：fan-in callback readiness 可以由 header 完成事实派生，用户模型不需要先暴露一个独立 ready artifact。
- Argo DAG 用依赖和 enhanced depends 表达 task result 条件；控制器据此启动后续节点。经验：ready 条件可以来自依赖表达式和上游结果状态，而不是必须作为独立业务事实存在。
- Dask scheduler 内部有 waiting、queued、processing、memory 等状态。经验：调度器需要 ready / queued 类状态来运转，但这些更像 rebuildable runtime projection，不应自动升级为 TokenShare replay authority。

对 TokenShare Phase 5 的影响：

- `MERGE_READY_RECORDED` 不应作为第一版必需权威事件。ready 可以由 accepted `MergePlan`、required slot canonical facts、尚无 merge task link 这三类事实从 ledger/projection 派生。
- 需要权威落账的是“merge work 已被实例化”：merge `TaskUnit`、稳定 `merge_input_bundle`、slot binding、child canonical digest、`MergeTaskLink` 和可选 `TaskRelation(kind=merge_of)` 必须在同一个 batch 中成为一致事实。
- 因为 `merge_input_bundle` artifact 可能先被保存到磁盘，Phase 5 应沿用 Phase 4 staged artifact 口径：未被 accepted batch 引用的 bundle 不是协议事实，crash 后可重试或清理。
- 如果扫描器在 ready 派生后、batch 写入前崩溃，replay 后再次扫描即可；如果 batch 成功写入，幂等键应返回同一 merge task creation batch；如果出现半批次，projection / replay 必须报 ledger inconsistency。
- 第一版如果需要 observability，可以把 `ready_detected_at`、`coordinator_id`、`ready_reason=all_required_slots_canonical` 放入 `MERGE_TASK_LINK_RECORDED` 或 batch marker payload，而不是新增独立 `MERGE_READY_RECORDED`。

本轮结论：

- 该 creation-event 口径已在 2026-06-25 Phase 5 讨论中确认。不新增独立 `MERGE_READY_RECORDED`。
- 新增 `merge_task_creation_batch:{merge_plan_id}` 或等价 deterministic batch id。
- batch 固定写入：`TASK_UNIT_CREATED` for merge unit、可选 `TASK_RELATION_CREATED(kind=merge_of)`、最终 `MERGE_TASK_LINK_RECORDED` marker。
- `MERGE_TASK_LINK_RECORDED` 是 `MergeTaskLink` 的权威事件，记录 parent、merge plan、merge unit、merge input bundle ref/digest、required slot bindings、canonical selection/event seq、child output digests 和 readiness reason。
- `merge_input_bundle` artifact 在 batch 前可以 staged 保存；只有 `MERGE_TASK_LINK_RECORDED` 引用后才是 replay / audit 可消费输入。

## 9. `MERGE_RECORDED` timing 调研：attempt result 还是 canonical commitment

本节对应 Phase 5 当前讨论点：`MERGE_RECORDED` 应该在 merge submission 后、verification 后、canonical 后写入，还是拆成 attempt-level merge audit 与 canonical-level merge commitment。

来源：

- Temporal Events and Event History 文档，来源 URL：`https://docs.temporal.io/workflow-execution/event`，访问日期：2026-06-25。
- Temporal Workflow replay 文档，来源 URL：`https://docs.temporal.io/workflows`，访问日期：2026-06-25。
- Apache Airflow Tasks 文档，来源 URL：`https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html`，访问日期：2026-06-25。
- Celery Tasks 文档，来源 URL：`https://docs.celeryq.dev/en/stable/userguide/tasks.html`，访问日期：2026-06-25。
- Celery Result API 文档，来源 URL：`https://docs.celeryq.dev/en/main/reference/celery.result.html`，访问日期：2026-06-25。
- Dagster Op events 文档，来源 URL：`https://docs.dagster.io/guides/build/ops/op-events`，访问日期：2026-06-25。
- Google MapReduce OSDI 2004 paper，来源 URL：`https://research.google.com/archive/mapreduce-osdi04.pdf`，访问日期：2026-06-25。

观察：

- Temporal 在 activity 执行成功后把 `ActivityTaskCompleted` 写入 Event History，失败和重试也作为 activity event / retry policy 的一部分进入历史。经验：执行尝试的 started / completed / failed 事实和 workflow replay 的 durable history 分开处理，但 replay 消费的是已落账的历史事件，不重新运行 activity 来补结果。
- Airflow 用 task instance terminal state 表达 success / failed / skipped / upstream_failed 等结果；downstream trigger rule 消费的是这些结果状态，而不是 task 运行中的中间输出。
- Celery task retry 会记录为 task state；result API 的 `ready()` 表达任务已经执行完成，`result` 才是完成后的返回值或失败对象。经验：attempt / retry 状态和最终可消费 result 是不同层次。
- Dagster 的 `Output` event 是 op 向下游传递数据的关键事件，`AssetMaterialization` 这类 side-effect event 不能像 output 一样传给其他 op。经验：用于 lineage / observability 的 materialization 事件和真正的数据输出承诺不能混淆。
- MapReduce 讨论 backup tasks / re-execution 时需要避免重复执行导致 counters double count。经验：聚合、奖励和指标不应按所有 execution attempts 计数，而应按被接受的最终结果身份计数。

对 TokenShare Phase 5 的影响：

- merge `TaskUnit` 已经走普通 `ExecutionSubmission -> VerificationReport -> CANONICAL_OUTPUTS_BOUND` 生命周期。submission 和 verification 已经提供 attempt-level audit；不需要再用 `MERGE_RECORDED` 复制一份 attempt 结果。
- `MERGE_RECORDED` 第一版应只在 merge unit 的 `CANONICAL_OUTPUTS_BOUND` 后写入，表达“某个 parent / merge plan 的 required slot canonical inputs 已产生一个被 canonical 选择的 merge output”。
- `MERGE_RECORDED` 应引用 selected merge canonical selection、selected verification report、selected submission / attempt、merge output bundle digest、merge input bundle digest、required slot bindings digest 和 merge policy identity。
- losing merge attempts、verification failed / rejected attempts、late submissions 和 retry attempts 仍通过普通 execution / verification / canonical events 审计，但不得产生 `MERGE_RECORDED`，也不得进入 contribution / settlement 的默认计数。
- 如果未来需要更细粒度 merge attempt observability，可以新增非权威 `MERGE_ATTEMPT_AUDIT_RECORDED` 或直接扩展 verification summary；第一版不引入该事件，避免事件面膨胀。

本轮结论：

- 该 timing 口径已在 2026-06-25 Phase 5 讨论中确认。`MERGE_RECORDED` 是 canonical-level merge commitment，只在 merge `TaskUnit` 已有 `CANONICAL_OUTPUTS_BOUND` 后写入。
- 第一版不新增 attempt-level `MERGE_ATTEMPT_RECORDED`。
- `MERGE_RECORDED` 的 idempotency key 使用 `merge_record:{merge_plan_id}:{merge_unit_id}:{canonical_selection_id}` 或等价 deterministic identity；同一 canonical commitment 重试幂等，出现不同 canonical / input bundle / slot binding digest 必须冲突。
- `MERGE_RECORDED` 可以作为后续 `ExpectedOutputRef` resolution、parent completion / upward merge 和 contribution / settlement 的输入事实；后续已确认 `merge_plan_output` v1 中它必须与 required `EXPECTED_OUTPUT_RESOLVED` events 通过 `merge_resolution_batch:{merge_record_id}` 原子提交。
