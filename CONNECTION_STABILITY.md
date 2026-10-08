# 连接稳定性排查与修复

日期：2026-10-08（Asia/Shanghai）。实际项目：`C:\Users\jo\Desktop\jianli\xiangmu\LangGraph_feedback_workbench_starter\feedback-workbench`。

## 已确认的原因

Codex 日志反复记录 `tls handshake eof`、`request timed out`、`stream connection failed`；Clash 日志在相同时段记录当前上游节点连接超时。当前系统代理为 `127.0.0.1:7897`，同时开启 TUN。Codex 启动时没有显式 HTTP_PROXY/HTTPS_PROXY，doctor 显示 respect_system_proxy 未启用。不能仅根据“Reconnecting”把原因归到项目代码或 WebSocket 配置。

对实际 Codex 端点测试：香港进阶 IEPL 01、02 均超时；新加坡 IEPL 01 和日本原生 IEPL 01 可达。新加坡 IEPL 01 切换前三次复测耗时为 359、552、639 毫秒。已将“节点选择”由香港进阶 IEPL 02 切换至新加坡 IEPL 01；原选择保存在 `F:\project\1\proxy-selection-backup.json`。

原 Streamlit 服务在 8501 端口，健康检查返回 ok。未复现用户先前网页断连，也没有先前网页 WebSocket 的关闭码，因此网页原始断连根因仍不能确定；本次针对路由隔离、短暂断线恢复和进程生命周期补强，并验证实际连接。

## 已应用的修改

- Windows 用户级 HTTP_PROXY/HTTPS_PROXY 显式指向 `http://127.0.0.1:7897`，NO_PROXY 加入 localhost、127.0.0.1、::1、.aliyuncs.com。新进程才继承，Codex 完全退出后重开生效。原用户级环境值保存在 `F:\project\1\codex-proxy-env-backup.json`。这是用户级环境设置，其他尊重这些环境变量的新程序也会读取它们。
- `workflow.py` 使用 SDK 提供的 `DefaultHttpxClient`，读取 `LLM_TRUST_ENV`，并在调用结束后关闭客户端。保持原模型、提示词、45 秒超时和一次重试。现有 `.env` 增加 `LLM_TRUST_ENV=false`，使百炼请求不受 HTTP 代理环境影响；没有禁用 TLS 校验。该设置不绕过 TUN。如换用需要代理或企业自定义 CA 的模型服务，设置为 true。
- `.streamlit/config.toml` 使用本地 127.0.0.1，心跳间隔及超时 30 秒，断线会话保留 600 秒，关闭保存后自动运行。浏览器必须连接这台机器的本地地址。此配置支持短暂断线恢复，不保证浏览器关闭或服务重启后保存未提交输入。
- `start_app.py` 用现有 `.venv` 独立启动隐藏后台进程，固定项目工作目录，移除子进程代理环境，写入 `runtime_logs`，启动后检查健康端点。进程不依赖 Codex 工具会话或编辑器终端。
- 已在 8502 启动新配置实例；原 8501 实例保留，避免中断正在打开的旧页面。两个入口使用同一业务数据库。这是单用户原型，请只在一个入口提交审核操作。

## 使用方法

现在打开 <http://127.0.0.1:8502>。后续在项目目录运行：

```powershell
.\.venv\Scripts\python.exe start_app.py --port 8502
```

端口已有服务时脚本不会重复启动或杀死进程。电脑重启后重新运行该命令；默认不设开机自启。要停止新实例，可在任务管理器核对 python.exe 命令行包含本项目 app.py 和 `--server.port=8502` 后结束对应进程。

## 验证

- 现有业务及页面 9 项测试通过，仅使用临时库和模拟模型。
- 新增连接测试 2 项通过：一次连接失败后 SDK 重试成功、客户端资源关闭、错误信息不泄露密钥。测试使用 SDK 实际依赖 HTTPX2 的 MockTransport，没有调用真实模型。
- 修复后 Codex doctor 连续两次 HTTPS 和 WebSocket 均为 ok；推理端点返回 HTTP 405（无推理的连通测试），CDN 返回 HTTP 200，WebSocket 握手返回 HTTP 101。脱敏报告为 `connection_verification.json`。
- 百炼模型端点无认证连通检查连续三次返回 HTTP 401，耗时 0.46、0.25、0.21 秒。401 是本次故意不带密钥的预期结果，不代表认证功能测试。
- 新服务真实 WebSocket 完成页面初始化、跨 30 秒心跳保持 36 秒，以及断开后恢复相同 session_id。期间没有提交反馈、创建需求或调用付费模型。初次探测客户端没有消费页面帧造成缓冲阻塞，修正测试客户端后复测；最终结果见实际探测记录。

本次有限时长验证不能保证外部代理节点长期不故障。如果再次发生，先检查 runtime_logs、Codex 日志和 Clash 上游超时；保留错误时间和网页地址，以便关联分析。

## 备份与恢复

项目原文件备份：`connection_fix_backups/20261008T072800Z/`，包含 workflow.py、.env、.env.example。原来没有 `.streamlit/config.toml` 或 start_app.py。恢复时先停止本项目服务，复制备份文件回原路径，并移除本次新增的配置和启动文件。

代理节点可在 Clash 界面恢复为备份中的 previous。用户级环境变量可按 `codex-proxy-env-backup.json` 恢复；这台机器原三项值均未设置。业务数据库、检查点、模型密钥、模型型号及旧评测文件未修改。

## 参考

- [OpenAI 官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)：provider 的 WebSocket 支持与流超时/重试配置。
- [Mihomo 官方控制 API](https://wiki.metacubex.one/api/)：节点延迟检查和代理组选择。
- 本机 Streamlit 1.65.0 的 config.py、starlette_server.py 与随包技能文档：心跳参数、断线会话 TTL、CLI 启动配置。
