# HIT 插件 QQ 测试与调试手册

## 1. 当前能力清单（与你截图一致）

事件监听器：
- 平台消息下发时 -> 处理群消息
- 平台消息下发时 -> 处理私聊消息

指令组：
- hit

指令：
- hit help
- hit course
- hit scan
- hit status
- hit file
- hit search
- hit contribute
- hit ingest
- hit errlog
- hit debug

## 2. 模型与路由（已改为 MiniMax）

- 自动意图分析：MiniMax-M2.7
- 复杂任务（如搜索任务拆解、每日总结）：MiniMax-M2.7
- 接口：/v1/text/chatcompletion_v2（MiniMax 原生）

## 3. 启动前检查

先确认环境变量（至少要有 AI key）：
- HITSZ_AI_API_KEY
- HITSZ_AI_BASE_URL（默认 https://api.minimaxi.com）
- HITSZ_AI_INTENT_MODEL（默认 MiniMax-M2.7）
- HITSZ_AI_COMPLEX_MODEL（默认 MiniMax-M2.7）
- HITSZ_AGENT_BACKEND_URL
- HITSZ_AGENT_BACKEND_API_KEY（若后端要求鉴权）

建议：
- 先执行 hit errlog on
- 再执行 hit debug 看配置是否生效

## 4. QQ 端回归测试步骤

命令输入建议：
- 优先使用 `hit ...`（不带前导 `/`）。
- 你当前环境下，`/hit ...` 可能被主聊天插件当作普通对话处理，导致出现“我不会这个命令”的回复。

自然语言主入口（已启用）：
- 现在可以直接说自然语言（例如“查一下 COMP3006”“帮我找机器学习论文”），无需 `hit`。
- `hit ...` 作为保底入口，适合调试或强制指定动作。

### 4.1 基础可用性

1. 发送 /hit help
- 期望：返回帮助菜单。
 - 推荐输入：hit help

2. 发送 /hit status
- 期望：看到 AI 调用额度、服务状态、队列数。
 - 推荐输入：hit status

3. 发送 /hit debug
- 期望：显示 AI 意图模型、AI 复杂模型、AI 接口地址、Key 配置状态。
 - 推荐输入：hit debug

### 4.2 课程检索

1. 发送 /hit course 自动控制
- 期望：返回课程列表；若无结果，给出贡献引导。
 - 推荐输入：hit course 自动控制

### 4.3 资料检索

1. 发送 /hit file 自动控制 课件
- 期望：返回资料列表，包含来源信息。
 - 推荐输入：hit file 自动控制 课件

2. 发送 /hit search 哈工大深圳 保研政策
- 期望：返回深度搜索结果，可能包含拆分子查询后的聚合内容。
 - 推荐输入：hit search 哈工大深圳 保研政策

### 4.4 群文件扫描与入库（仅群聊）

1. 在群内发送 /hit scan
- 期望：显示总文件数、新增数、上传成功数、失败数。
 - 推荐输入：hit scan

2. 发送 /hit ingest 20
- 期望：显示候选数、成功数、失败数、跳过数。
 - 推荐输入：hit ingest 20

### 4.5 贡献链路

1. 发送：
/hit contribute mode=preview campus=shenzhen course_code=AUTO1001 course_name=自动控制原理 teacher=张老师 content=讲解清晰，作业适中 semester=2026春
- 期望：返回 preview 结果，不创建 PR。
 - 推荐输入：hit contribute mode=preview campus=shenzhen course_code=AUTO1001 course_name=自动控制原理 teacher=张老师 content=讲解清晰，作业适中 semester=2026春

2. 发送：
/hit contribute mode=submit campus=shenzhen course_code=AUTO1001 course_name=自动控制原理 teacher=张老师 content=讲解清晰，作业适中 semester=2026春
- 期望：提交成功并返回 PR 信息或任务信息。
 - 推荐输入：hit contribute mode=submit campus=shenzhen course_code=AUTO1001 course_name=自动控制原理 teacher=张老师 content=讲解清晰，作业适中 semester=2026春

### 4.6 自动意图触发（无命令）

在群里连续发 2-3 句自然语言，例如：
- 自动控制原理给分怎么样
- 王老师讲课节奏快吗
- @机器人 帮我查一下机器学习相关论文

期望：
- 对 @机器人 的查询优先即时触发；未命中时才回退到周期检查（默认 3 分钟）。

## 5. 在哪里看日志

优先看 AstrBot 运行终端（最完整）：
- 如果你是用脚本启动，直接看脚本所在终端输出。
- 建议重启后立刻观察启动阶段日志是否出现 MiniMax 配置信息。

若你启用了文件日志（AstrBot 全局配置里 log_file_enable=true）：
- 查看 logs/astrbot.log

插件内调试建议：
- 先执行 /hit errlog on
- 再复现问题
- 再执行 /hit debug

## 6. 出问题时发我这 6 样信息

1. 问题发生的精确时间（到分钟）
2. 你在 QQ 里发送的原始指令或原始消息
3. 机器人返回的完整文本
4. /hit status 的完整输出
5. /hit debug 的完整输出
6. 同时段终端日志（前后各 30-60 行）

## 7. 快速定位建议

- 如果 /hit help 正常，但其他命令都失败：优先检查 agent-backend URL 与鉴权。
- 如果命令正常，但自动意图不触发：检查时间窗口（默认 08:00-22:00）和检查间隔（默认 180 秒）。
- 如果 scan 失败：优先检查 Napcat 侧群文件 API 权限与返回格式。
- 如果 contribute preview 正常、submit 失败：重点看后端 pr.submit 返回的 error 字段。

## 8. 结论

你列的技能清单与当前插件实现是一致的。
按本手册从 help/status/debug 到 scan/ingest/contribute 逐条测一遍，基本能覆盖 90% 线上故障。