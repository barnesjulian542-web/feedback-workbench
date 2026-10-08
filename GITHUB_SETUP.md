# GitHub 接入与后续协作

本项目继续使用当前本地目录、现有 `.venv` 和 `data_real_v1`。GitHub 保存代码、项目说明和评测记录；本地数据库不随代码上传。

## 首次连接

1. 在 GitHub 创建 `feedback-workbench` 私有空仓库。已有本地文件，创建时不添加 README、`.gitignore` 或 License。
2. 在桌面应用的 Plugins 中安装 GitHub，并完成账号及仓库授权。
3. 连接仓库与上传文件是分别完成的动作；核对授权、目标仓库和待提交文件之后再上传。

已核对的账号为 `barnesjulian542-web`。拟使用仓库地址：

https://github.com/barnesjulian542-web/feedback-workbench

## 文件范围

- 上传：Python 源码、依赖清单、项目说明、AGENTS.md、模拟开发样例和已有各轮评测文件。
- 留在本机：`.env`、`.venv`、业务数据库、SQLite 检查点、数据库附属文件、代码/连接备份、运行日志。
- `.gitignore` 已补齐上述本地文件规则；忽略文件不会删除本地文件。
- 私有仓库中的数据同样应经过审核。新增真实反馈时，先脱敏再决定是否提交评测文件。

## 每次继续作业

告诉 Codex 具体问题、目标和当前仓库/本地项目；从 AGENTS.md 指定的交接文档开始读取。

需要更多参考资料时，提供相关 GitHub 仓库、Issue 或 PR 的链接和希望研究的内容。资料将按任务读取，不会因为连接 GitHub 就自动读取所有仓库。

结束一轮修改时保留代码差异、验证结果、已知限制和提示词版本。出现模型质量问题时保留全部回归轮次，不仅保存成功结果。

## 控制重复工作与费用

- 先检查现有代码和历史结果，使用项目说明恢复上下文。
- 按修改范围运行本地测试；先验证代码与模拟流程，再决定是否需要付费模型回归。
- 只有影响模型行为的修改或尚未解决的问题需要重新运行真实回归。
- GitHub 提供版本记录和资料入口，不改变 Codex 的计费方式，也不降低 qwen-plus 的单次调用价格。

## 参考

- GitHub 新建仓库：https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository
- GitHub 忽略文件：https://docs.github.com/en/get-started/git-basics/ignoring-files
- 官方 OpenAI 插件安装：https://learn.chatgpt.com/docs/plugins
