# HIT智能助手 - AstrBot 插件 v1.0.0

哈尔滨工业大学智能课程查询、资料搜索、群聊总结和贡献引导助手。

## 功能特性

### 🤖 智能意图识别
- 使用 Gemini 2.5 Flash 进行意图判断
- 3分钟滑动窗口上下文管理
- 自动过滤闲聊，识别有效意图
- 日额度 250 次，早8晚10激活

### 📚 课程查询
- 自动识别课程查询意图
- 支持课程代码、名称、教师搜索
- 未找到课程时智能引导贡献

### 🔍 资料搜索
- 课程资料搜索
- 深度搜索（论文、文献）
- 分层搜索策略

### 📝 智能贡献引导
- 自动识别分享意图
- 智能PR提交流程
- 引导用户贡献内容

### 📊 每日总结
- 每日22:30生成群聊精华总结
- 提取有价值的学习信息
- TODO: 管理员确认后入库

## 安装

### 1. 克隆插件

```bash
cd AstrBot/data/plugins
git clone https://github.com/HIT-A/astrbot_plugin_hit.git
```

### 2. 配置环境变量

```bash
# Gemini API (用于意图识别)
HITSZ_GEMINI_API_KEY=your_gemini_api_key

# Agent Backend (用于调用Skills)
HITSZ_AGENT_BACKEND_URL=http://localhost:8080
HITSZ_AGENT_BACKEND_API_KEY=your_api_key
```

### 3. 安装依赖

```bash
cd astrbot_plugin_hit
pip install -r requirements.txt
```

### 4. 重启 AstrBot

在 AstrBot WebUI 中重载插件。

## 使用

### 指令

```
/hit help - 显示帮助
/hit course <关键词> - 搜索课程
/hit status - 查看插件状态
```

### 智能功能

插件会自动识别以下意图并响应：
- **课程查询**: "自动控制原理怎么样？"、"AUTO1001的老师是谁？"
- **资料搜索**: "有课件吗？"、"求试卷"
- **贡献引导**: "我觉得这门课..."、"分享一份笔记"

## 架构

```
用户消息
    │
    ▼
AstrBot + HIT Plugin
    │
    ├──▶ Gemini Flash (意图识别)
    │
    └──▶ Agent Backend (Skills)
            ├──▶ courses.search/read
            ├──▶ search (RAG)
            └──▶ pr.submit
```

## 配置项

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| HITSZ_GEMINI_API_KEY | Gemini API Key | - |
| HITSZ_AGENT_BACKEND_URL | Agent Backend地址 | http://localhost:8080 |
| HITSZ_AGENT_BACKEND_API_KEY | Agent Backend API Key | - |

## 开发计划

- [x] 基础架构和意图识别
- [x] 课程查询功能
- [ ] 文件搜索功能
- [ ] 深度搜索功能
- [ ] 群文件主动扫描
- [ ] 每日聊天记录总结
- [ ] 智能PR贡献流程

## License

AGPL-3.0
