# TokenShare 快速启动验证实施计划

> **执行要求：** 按 TDD 顺序实施；不得改动正在并发修复的 Task 14 catalog/runner 文件。

**目标：** 把启动基线改为默认秒级 smoke suite，同时保留显式完整验证入口。

**架构：** `verification/fast-tests.txt` 是唯一快速测试清单。`init.ps1` 和 `init.sh` 继续共享原有公共检查，再根据命令行参数选择该清单或完整 `tests` 目录。Python 契约测试静态验证清单与两个入口，实际执行验证负责证明测试集合可运行。

**技术栈：** PowerShell、Bash、pytest、文本 manifest。

---

### Task 1：定义验证模式契约

**文件：**

- 新增：`tests/test_init_verification_profiles.py`

1. 写测试断言共享清单存在、非空、路径存在且无重复。
2. 写测试断言快速清单包含协议/存储/executor/factorization 与轻量实验契约覆盖。
3. 写测试断言快速清单不包含已知长测试。
4. 写测试断言 PowerShell 暴露 `-Full`、Bash 暴露 `--full`，且两者引用同一清单。
5. 先运行该测试并确认因尚未实现而失败。

### Task 2：实现共享快速清单和模式选择

**文件：**

- 新增：`verification/fast-tests.txt`
- 修改：`init.ps1`
- 修改：`init.sh`

1. 建立只含无网络、轻量 smoke/regression 路径的清单。
2. PowerShell 默认读取清单；`-Full` 执行 `pytest tests`。
3. Bash 默认读取同一清单；`--full` 执行 `pytest tests`。
4. 对空清单、缺失路径和未知参数 fail fast。
5. 重新运行契约测试并确认通过。

### Task 3：同步 harness 文档

**文件：**

- 修改：`AGENTS.md`
- 修改：`README.md`
- 修改：`Doc/agent-navigation.md`
- 修改：`Doc/TechnicalDocument/tokenshare_v1_code_map.md`

1. 把启动流程写成默认快速档。
2. 把 feature 完成标准写成必须运行完整档。
3. 记录共享清单和两个命令行接口。
4. 二次检索旧的“默认运行全部测试”表述并修正。

### Task 4：验证

1. 运行验证模式契约测试。
2. 运行 `\.\init.ps1`，记录耗时和测试数量。
3. 运行 Bash 参数/语法检查；环境允许时执行 `./init.sh`。
4. 运行一次 `\.\init.ps1 -Full`，确认完整档仍执行全部测试。
5. 复核 git diff，确保没有覆盖并发 agent 的 Task 14 修改。

