# HITSZ 课程助手 - AstrBot 插件

哈尔滨工业大学（深圳）课程查询、RAG 问答、PR 提交助手。

## 功能

### 📚 课程查询
- `/搜 <关键词>` - 模糊搜索课程
- `/查 <课程代码>` - 查看课程详情

### 💬 RAG 问答
- `/问 <问题>` - 基于知识库的智能问答

### 📝 PR 提交
- `/pr start <课程代码>` - 开始 PR 提交流程
- `/pr show` - 查看当前课程内容
- `/pr add <章节>` - 添加新内容
- `/pr submit` - 提交 PR
- `/pr cancel` - 取消提交
- `/pr help` - 详细帮助

## 安装

### 1. 克隆插件

```bash
cd AstrBot/data/plugins
git clone https://github.com/HIT-A/astrbot_plugin_hitsz.git
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并配置：

```bash
# Agent Backend 地址（必填）
HITSZ_AGENT_BACKEND_URL=http://localhost:8080
HITSZ_AGENT_BACKEND_API_KEY=your_api_key_here
```

### 3. 安装依赖

```bash
cd astrbot_plugin_hitsz
pip install -r requirements.txt
```

### 4. 重启 AstrBot

在 AstrBot WebUI 中重载插件，或重启 AstrBot。

## 架构

```
AstrBot (本插件)
    │
    ├──▶ agent-backend (Skills API)
    │       ├──▶ courses.search/read
    │       ├──▶ search (RAG)
    │       ├──▶ pr.submit/preview
    │       └──▶ 其他 32 个 skills
    │
    └──▶ data-backend/pr-server (内部服务)
            └──▶ GitHub PR 操作
```

## 依赖服务

- **agent-backend**: 提供 Skills API（32 个 skills）
  - 地址: http://localhost:8080
  - 文档: https://github.com/HIT-A/api_bundle

- **data-backend/pr-server**: 内部 PR 服务
  - 由 agent-backend 内部调用

## 开发

### 项目结构

```
astrbot_plugin_hitsz/
├── main.py           # 主插件代码
├── metadata.yaml     # 插件元数据
├── requirements.txt  # 依赖
└── README.md         # 本文档
```

### 添加新功能

1. 在 `main.py` 中添加新的 handler 方法
2. 使用 `@filter.command("指令名")` 注册指令
3. 使用 `self._call_skill()` 调用 agent-backend 的 skill

### 示例

```python
@filter.command("新指令")
async def new_command(self, event: AstrMessageEvent, param: str):
    '''指令描述'''
    result = await self._call_skill("skill.name", {
        "param": param
    })
    yield event.plain_result("结果")
```

## License

AGPL-3.0
