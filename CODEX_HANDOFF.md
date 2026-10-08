# AI用户反馈分析与需求分流工作台：Codex交接

交接日期：2026-10-08（Asia/Shanghai）。本说明记录网页端已完成的工作；继续修改时，以电脑上的实际代码、数据库和最新评测文件为准。

## 项目与用户

用户是Python/LangGraph初学者，正在完成可用于AI产品经理求职展示的项目。回复需给出具体文件、操作位置和命令，避免只给抽象建议。

项目使用Python、LangGraph、Streamlit、Pydantic、SQLite。真实反馈分析调用qwen-plus，使用OpenAI兼容接口；Codex负责开发与运行代码，业务模型仍由项目.env配置决定。

已实现：反馈录入/CSV导入、结构化分析、原文证据引用、已有需求检索、人工审核、创建/关联/驳回、待补充与再次分析、优先级管理、事件记录、LangGraph检查点与恢复。检索曾采用字符bigram，不要未经核对将它描述成向量RAG或模型自主工具调用。

## 本地位置与环境

用户当前使用Windows和PowerShell，项目目录此前截图确认是：

```text
C:\Users\jo\Desktop\jianli\xiangmu\LangGraph_feedback_workbench_starter\feedback-workbench
```

请打开这个既有项目目录，采用本机/Local及Windows原生环境，复用现有Python虚拟环境。实际目录如果已移动，以用户选中的目录为准。

关键文件与目录：

| 文件或目录 | 用途 |
| --- | --- |
| app.py、workflow.py、models.py、storage.py | 页面、流程、结构约束、业务持久化 |
| requirements.txt、.venv | 依赖与已有运行环境 |
| .env | 真实模型及DATA_DIR配置；不要打印或复制其中的密钥 |
| data_real_v1 | 此前确认的业务数据目录，需核对当前DATA_DIR仍指向这里 |
| evaluation_v2_recovered | 已恢复并人工评分的20条首轮评测基线 |
| export_evaluation.py、recover_evaluation.py | 原始导出与历史首轮恢复工具 |
| apply_quality_patch.py | 用户已运行的提示词v1安装脚本 |
| quality_smoke_test.py | 8条真实模型定向回归，直接调用real_analysis，不写业务库 |
| quality_smoke_results | 各轮回归的JSON与Markdown，保留各轮结果 |

不要新建空项目替代原项目，或清空SQLite文件。保留业务库、检查点库、模型配置与历史评测。在同一电脑打开原目录，就是继续使用同一批本地文件。

## 已完成的评测

1. 20条模拟开发样例，bug/ux/feature/unclear各5条，真实模型qwen-plus。
2. 曾因重复分析导致18条首轮输出无法由原导出工具获取，已使用recover_evaluation.py从历史检查点核对并恢复。20条可评分，18条来自历史检查点，2条来自已有导出或单次分析。
3. 用户已人工评分20条。原CSV中N列均为“支持”，O列均为“无明显越界”，P列均为“无”；验收范围评分还需要复核，不能仅凭这些填写宣布全部质量合格。
4. 基线分类20/20，澄清标记19/20，需要澄清的样例识别5/6。唯一标记错误是B05：同时提出上传CSV的500故障和新增批量审核，模型只处理故障且没有标记需澄清。
5. 这批是开发模拟样例，不代表真实用户准确率或独立测试集成绩。记录的平均analyze耗时约4.822秒，首轮输入/输出Token为11676/3909，不完整覆盖失败与重试费用。

项目分流约定：多个仍在当前处理范围内的独立诉求，需要确认拆分；主要问题明确时category仍按主诉求分类，needs_clarification仍可为true。后续补充明确本条仅处理其中一个、其他另建或暂缓后，应按已确认范围重新判断，避免无限澄清。

## 用户已安装的提示词v1

LangGraph_quality_update_v1.zip包含apply_quality_patch.py、quality_smoke_test.py和安装说明。安装只给workflow.py中real_analysis的system_prompt增补范围规则，并自动备份原文件。代码中应能看到QUALITY_REVIEW_V1_BEGIN/END标记。继续修改前核对实际安装状态，不要用旧版workflow.py覆盖用户现有文件。

v1真实回归已运行，run_id为20261008T055740Z_229706a1。原始完整JSON预计位于：

```text
quality_smoke_results/20261008T055740Z_229706a1.json
```

用户也把完整JSON粘贴回网页，已核对8条分类全对，自动检查7条pass、1条fail，无error或未运行条目。验收范围字段仍全部为“未评分”。

| 案例 | 当前结果与待处理事项 |
| --- | --- |
| R01 | B05现在为bug且需澄清，多诉求遗漏得到修复；问题确认拆单后又追问次要功能细节，仍可收紧提问范围 |
| R02 | 单故障无需澄清，正确；“数据未写入”是未验证原因推断，4xx作为错误响应方案需核对范围 |
| R03 | ux且无需澄清，正确；仍引用“WCAG最小字体建议”和1920x1080等未经确认标准或测试条件 |
| R04 | 唯一自动fail：搜索结果正确但等待十多秒，预期ux、无需澄清，实际needs_clarification=true；还擅自提出≤2秒 |
| R05 | feature且无需澄清，正确；不再强制增加负责人筛选统计，但可选/必填、列表展示及审计建议还应人工确认 |
| R06 | unclear且需澄清，正确；没有继续增加95%/2秒之类数值 |
| R07 | 主要诉求ux加截图上传，需澄清，正确；仍擅自提出不少于14px，应去除无依据阈值；问题应优先确认拆分 |
| R08 | B05补充明确只处理上传，实际bug、无需澄清，正确；直接模型回归通过不能替代实际LangGraph页面的澄清流程验证 |

## 接下来要完成的任务

1. 先读取当前workflow.py、models.py、storage.py、quality_smoke_test.py及最新真实JSON。核对既有AGENTS.md等项目指导文件，确认解释器和DATA_DIR。不输出.env密钥。
2. 针对R04明确澄清边界：用户已经说明操作对象、具体体验问题及改进方向时，缺少根因、数据量、并发、复现参数或最终验收阈值，不应单独阻断需求分流。工程排查项和待确认参数可保留为建议，但不要因此把单一明确诉求一律needs_clarification=true。保留真正信息不足、多诉求拆分以及补充后的范围判断。
3. 收紧验收条件：不得把原文未给出的响应时间、字号、标准要求、额外功能或技术方案直接当成既定验收范围。需要具体参数时写待确认；不要仅把无依据数字改成“例如”就认为问题已解决。
4. 完成相关代码验证后，运行已有8条真实模型回归，保留此次完整输出并逐条核验验收范围。真实调用沿用qwen-plus；不要更换模型、改预期答案或只挑一次成功结果来制造提升。
5. 再验证一次真实页面澄清流程：新来源IDiter2-B05-01，输入B05原文，确认bug且需澄清；在同一条反馈中补充“本条只处理上传CSV的500错误，批量审核另开一条需求，当前先不处理。”，确认再次分析后无需澄清，再人工审核。不要在原20条基线上反复分析。
6. 最后形成有真实测量依据的迭代记录，再推进公开演示、产品文档与求职材料。不要虚构真实用户调研、上线效果或性能提升。

## 常用命令（在项目根目录的PowerShell运行）

启动网页：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

模型定向回归（会真实调用项目配置的模型）：

```powershell
.\.venv\Scripts\python.exe quality_smoke_test.py
```

仅汇总旧基线人工评分（不调用模型）：

```powershell
.\.venv\Scripts\python.exe recover_evaluation.py --summarize-only
```

recover_evaluation.py已经兼容UTF-8与WPS常见的GBK编码。用户在WPS保存CSV后应关闭该表再运行脚本，之前曾因WPS占用文件导致保存失败，无需每次进VS Code转编码。

## 平台同步边界

OpenAI官方说明：Work会话历史与Codex历史分开；对话同步与本地文件访问是两回事。Local模式直接使用当前项目目录。聊天账号相同不等于Codex已经收到这段Work会话的全部上下文，因此用本文件交接当前进度，并保留真实代码和评测记录。

官方资料：
- https://learn.chatgpt.com/docs/projects
- https://learn.chatgpt.com/docs/environments/modes
- https://learn.chatgpt.com/docs/windows/windows-app

## 本地续接完成记录（2026-10-08，Asia/Shanghai）

已在原目录、原 `.venv`、原 `data_real_v1` 中继续修改。最终提示词为 `quality_v2.4`，统一规则位于 `analysis_prompt.py`；旧 v1 增补块已整合，不应再次执行旧安装脚本覆盖当前版本。`workflow.py` 调用统一规则并记录提示词版本及哈希，`models.py` 仅增加字段说明，旧数据格式兼容。

- 最终真实回归：`quality_smoke_results/20261008T064831Z_8556a55d.json`，8条自动通过；Codex 逐条核对验收未发现无依据指标或额外范围。原人工评分字段仍是“未评分”，不冒充人工验收。
- R04 已为 ux、无需澄清，验收参数待确认；R01/R07 保留多诉求拆分，R08 补充后停止澄清。
- 所有中间轮次，包括网络失败、R01 退化和验收仍越界的轮次均保留；详情见 `QUALITY_ITERATION_V2.md`，不要只用自动通过率判断全部质量。
- 实际页面逻辑已通过 AppTest 和真实模型验证，来源 `iter2-B05-01` 在同一条中补充后 bug、无需澄清。目前停在待人工审核，尚未创建或关联需求。记录为 `quality_smoke_results/20261008T064930Z_8e9686a0_page.json`；不要重复分析该来源。
- 9项代码/页面测试全部通过。原34条反馈、原需求、事件、补充及检查点全部逐行核对未修改；只新增上述页面验证记录。旧评测文件及回归脚本哈希不变。
- 原代码和两库一致性备份：`quality_iteration_backups/20261008T062432Z_3c096845/`；完整保留核对：`quality_smoke_results/20261008T064831Z_8556a55d_integrity.json`。
- 已知质量问题仍需人工审核注意：最终 R02 的 problem 推断了“数据未写入”，原文仅报告列表没有新记录；须纠正这条原因断言，不能宣称所有字段均合格。

继续工作时先读 `QUALITY_ITERATION_V2.md`。下一步是用户在“人工审核”核对新验证反馈（ID开头 `c0a73d`），再决定创建、关联或驳回；没有代替用户完成最终审批或新建批量审核需求。

## GitHub 接入准备（2026-10-08）

用户选择新建私有仓库 `feedback-workbench`。本地已初始化 Git，分支 `main`；GitHub 插件已连接账号 `barnesjulian542-web`。远端创建、地址及上传状态以 GitHub 仓库和 `git remote -v`、`git status` 的实际结果为准。

`.gitignore` 已排除本地数据库、环境、备份和日志；原本地文件保留。`AGENTS.md` 提供后续作业读取路径、测试和付费回归边界；接入步骤见 `GITHUB_SETUP.md`。代码和评测版本管理能够减少重复交接，不改变模型服务的计费。
