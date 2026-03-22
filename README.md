# HIT 智能助手插件（AstrBot）

面向哈工大课程生态的群聊助手插件，聚焦三类能力：
- 课程与资料检索
- 群文件入库（对接 agent-backend）
- 贡献引导与课程内容沉淀

当前版本已对齐 2026-03-17 的 agent-backend skills 契约（`search`、`course.read`、`pr.*`、`data.ingest` 等）。

## 1. 已实现功能

### 1.1 消息队列与意图分析
- 基于 `core/message_queue.py` 做 3 分钟滑窗聚合。
- `main.py` 中定时任务循环按间隔触发意图分析。
- 通过 `services/intent_service.py` 和 `services/intent_classifier.py` 做意图识别与结构化提取。

### 1.2 课程检索
- 命令：`/hit course <关键词>`。
- 调用 `courses.search`，返回课程列表，支持自然语言关键词。
- 代码入口：`main.py::_search_course`、`services/agent_client.py::search_courses`。

### 1.3 资料检索与深度搜索
- 命令：
    - `/hit file <关键词>`：资料搜索
    - `/hit search <关键词>`：深度搜索
- 统一通过 `search` skill，按 `sources` 组合查询（如 `cos/rag/brave/arxiv/github`）。
- 代码入口：`main.py::_handle_file_search`、`main.py::_handle_deep_search`、`services/agent_client.py::search_files`。

### 1.4 群文件扫描与入库
- 命令：
    - `/hit scan`
    - `/hit ingest [limit]`
- 扫描流程：
    - 按扩展名和大小过滤
    - MD5 去重
    - 记录候选文件
- 入库流程：
    - 当前实现走 `data.ingest`（`source_type=manual`）
    - 兼容异步 `job_id` 返回
- 代码入口：`services/file_scanner.py`、`main.py::ingest_cmd`。

### 1.5 贡献与 PR 相关能力（客户端层）
- `services/agent_client.py` 已提供：
    - `preview_pr` -> `pr.preview`
    - `submit_pr` -> `pr.submit`
    - `lookup_pr` -> `pr.lookup`
- `submit_review` 已改为通过 `pr.submit` 提交 `add_lecturer_review` 操作。

### 1.6 状态与诊断
- 命令：
    - `/hit status`
    - `/hit debug`
- 提供额度、队列、任务、扫描统计、配置状态等观测信息。

## 2. 关键实现说明

## 2.1 模块结构

```text
astrbot_plugin_hit/
├── main.py
├── core/
│   ├── config.py
│   ├── history_manager.py
│   └── message_queue.py
├── services/
│   ├── agent_client.py
│   ├── file_scanner.py
│   ├── intent_service.py
│   ├── intent_classifier.py
│   ├── chat_summarizer.py
│   └── contribution_service.py
├── tools/
└── utils/
```

## 2.2 AgentClient 设计
- 统一入口：`invoke_skill(skill_name, input_data)`。
- 重试策略：超时/异常指数退避；4xx 不重试。
- 返回结构：标准化为 `AgentResponse(success, output, error, raw_response)`。
- 对异步 skill（仅返回 `job_id`）做了兼容映射，避免调用方空输出崩溃。

## 2.3 文件入库策略
- 由于后端新增聚合入口，插件从 `rag.ingest` 迁移到 `data.ingest`。
- 当前将扫描结果转换为 manual source 内容体，优先保证链路稳定可用。

## 2.4 配置读取
- 通过 `core/config.py` 读取以下环境变量（`HITSZ_*` 前缀）：
    - `HITSZ_AI_API_KEY`
    - `HITSZ_AI_BASE_URL`（默认 `https://api.minimaxi.com`）
    - `HITSZ_AI_INTENT_MODEL`（默认 `M2-her`）
    - `HITSZ_AI_COMPLEX_MODEL`（默认 `M2-her`）
    - `HITSZ_AGENT_BACKEND_URL`
    - `HITSZ_AGENT_BACKEND_API_KEY`
    - `HITSZ_INTENT_CHECK_INTERVAL`
    - `HITSZ_DAILY_SUMMARY_TIME`
    - `HITSZ_MAX_FILE_SIZE_MB`
    - `HITSZ_CONTEXT_WINDOW_MINUTES`

兼容说明：
- 仍可读取旧变量 `HITSZ_GLM_*` 作为兜底。

## 3. 使用命令

```text
/hit help
/hit course <关键词>
/hit file <关键词>
/hit search <关键词>
/hit scan
/hit ingest [limit]
/hit status
/hit debug
/hit contribute

# 贡献闭环（推荐）
/hit contribute mode=preview campus=shenzhen course_code=AUTO1001 course_name=自动控制原理 teacher=张老师 content="讲解清晰，作业适中" semester=2026春
/hit contribute mode=submit campus=shenzhen course_code=AUTO1001 course_name=自动控制原理 teacher=张老师 content="讲解清晰，作业适中" semester=2026春
```

说明：
- `mode=preview` 仅预览，不创建 PR。
- `mode=submit` 提交 PR。
- multi 仓库自动路由：
    - 有 `teacher` -> `add_course_teacher_review`
    - 无 `teacher` -> `append_course_section_item`
- normal 仓库使用 `add_lecturer_review`。
- 已禁用 `append_course_review`，不会再生成该 op。

## 4. 运行依赖
- Python 3.9+
- AstrBot 运行时
- `httpx`
- 可访问的 agent-backend 服务（默认 `http://localhost:8080`）

## 5. 未来开发目标

## 5.1 短期（1-2 周）
- 将 `contribution_service` 从“文案引导”升级为可实际触发 `pr.preview/pr.submit` 的完整会话流程。
- 为 `data.ingest` 增加任务轮询与结果回执（基于 `job_id` 查询），完善入库可观测性。
- 增加关键链路的最小集成测试（course/file/search/ingest/pr.lookup）。

## 5.2 中期（2-4 周）
- 引入群级配置（campus、目标仓库、管理员白名单）并持久化。
- 优化文件扫描接入：补齐平台回调适配，提升真实群环境可用性。
- 输出统一的错误码和用户提示模板，降低运维排障成本。

## 5.3 长期（1-2 月）
- 构建“从群聊到课程仓库”的闭环：识别贡献意图 -> 结构化采集 -> PR 预览/提交流程自动化。
- 完善知识沉淀策略：扫描文件、课程评价、FAQ 同步进入统一检索入口。
- 增加插件级观测面板（调用量、成功率、延迟、失败分布）。

## 6. 开发备注
- 本仓库当前存在未跟踪开发文件（如 `data/`、`smoke_cmd_test.py`），提交前建议按发布策略筛选。
- 若后端 skills 再次变更，请优先更新 `services/agent_client.py` 的映射层，避免在业务层散落改动。
