# HITSZ 智能助手 v3.0 - 设计方案

## 1. 系统架构

```
用户消息
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│              消息队列 & 上下文管理器                         │
│  - 3分钟滑动窗口                                             │
│  - 早8晚10激活                                               │
│  - 上下文去重和压缩                                          │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│           GLM 4.7 Flash (意图判断)                           │
│  额度: 250次/天                                              │
│  频率: 每3分钟判断一次（有新增上文时）                         │
│  功能:                                                        │
│    - 过滤闲聊/吐槽                                            │
│    - 识别有效意图                                             │
│    - 提取关键信息                                             │
└──────────────────────┬──────────────────────────────────────┘
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
    ┌─────────┐  ┌──────────┐  ┌──────────┐
    │课程查询 │  │文件搜索  │  │深度搜索  │
    │(Tool)   │  │(Tool)    │  │(Tool)    │
    └────┬────┘  └────┬─────┘  └────┬─────┘
         │            │             │
         ▼            ▼             ▼
    ┌─────────────────────────────────────────┐
    │         agent-backend Skills            │
    │  - courses.search/read                  │
    │  - search (RAG)                         │
    │  - files.upload/download                │
    │  - rag.ingest                           │
    │  - github.batch_download                │
    │  - document.convert                     │
    └─────────────────────────────────────────┘
```

## 2. 意图判断模块 (GLM 4.7 Flash)

### 2.1 激活策略

```python
激活时间: 08:00 - 22:00
判断频率: 每3分钟一次（仅当有新消息时）
日额度: 250次

触发条件:
1. 3分钟窗口内有新消息
2. 消息不是纯闲聊/吐槽
3. 消息可能包含有效意图
```

### 2.2 意图分类

```json
{
  "intents": [
    {
      "name": "course_query",
      "description": "查询课程信息、选课、学分、考试等",
      "keywords": ["课程", "选课", "学分", "考试", "作业", "老师", "教师"],
      "priority": 1
    },
    {
      "name": "file_search",
      "description": "搜索课件、资料、试卷、答案等文件",
      "keywords": ["课件", "PPT", "PDF", "资料", "试卷", "答案", "笔记"],
      "priority": 2
    },
    {
      "name": "deep_search",
      "description": "深度搜索论文、文献、技术资料",
      "keywords": ["论文", "文献", "arxiv", "研究", "技术", "原理"],
      "priority": 3
    },
    {
      "name": "contribution",
      "description": "贡献内容、提交评价、分享资料",
      "keywords": ["贡献", "提交", "分享", "上传", "评价", "打分"],
      "priority": 4
    },
    {
      "name": "general_query",
      "description": "通用查询，使用web search",
      "keywords": ["什么是", "怎么", "为什么", "如何"],
      "priority": 5
    },
    {
      "name": "chat",
      "description": "闲聊、吐槽、无意义对话",
      "keywords": [],
      "priority": 0,
      "action": "ignore"
    }
  ]
}
```

### 2.3 GLM Prompt 设计

```
你是一个意图识别助手。请分析以下对话上下文，判断用户的真实意图。

对话上下文:
{context}

请输出JSON格式:
{
  "has_valid_intent": true/false,  // 是否有有效意图（非闲聊）
  "intent": "course_query|file_search|deep_search|contribution|general_query|chat",
  "confidence": 0.95,  // 置信度
  "extracted_info": {
    "course_code": "",  // 提取的课程代码
    "course_name": "",  // 提取的课程名
    "teacher_name": "", // 提取的教师名
    "file_type": "",    // 文件类型（pdf/ppt/等）
    "keywords": []      // 关键搜索词
  },
  "reasoning": "判断理由"
}

注意:
- 如果只是吐槽、闲聊、表情、无意义内容，has_valid_intent=false
- 如果涉及学校、学习、个人发展相关内容，has_valid_intent=true
- 提取的信息要准确，不要猜测
```

## 3. 群文件处理流程（全自动）

### 3.1 实时监听

```python
文件监听流程:
1. 监听群文件上传事件
2. 立即下载到内存（不存本地）
3. 计算文件哈希
4. 查重（KV存储）
5. 如果重复，跳过
6. 如果不重复:
   a. 上传到COS
   b. 记录元信息（文件名、哈希、COS地址、上传者、时间）
   c. 如果是文档，提取文本摘要（AI生成）
   d. 保存到待审核队列
```

### 3.2 每日总结（需管理员确认）

```python
定时任务（每天22:30）:
1. 汇总当天所有新文件
2. 筛选与学校/学习/个人发展相关的文件
3. 生成总结报告:
   
   "今日群文件汇总（共15个）:
   
   📚 学习资料（8个）:
   - AUTO1001_课件.pdf (2.3MB) - 自动控制原理课件
   - CS201_笔记.md (15KB) - 数据结构笔记
   ...
   
   📝 考试资料（3个）:
   - MATH101_试卷.pdf (1.1MB) - 高数期中试卷
   ...
   
   📖 其他（4个）:
   ...
   
   是否将这些文件入库到RAG知识库？
   回复: 全部入库 / 选择入库 [1,3,5] / 跳过"

4. 私聊管理员
5. 等待回复（24小时超时）
6. 根据回复执行:
   - 全部入库: 所有文件走入库流程
   - 选择入库: 指定文件走入库流程
   - 跳过: 标记为已处理，不入库
```

### 3.3 文件入库流程（流式处理）

```python
单个文件入库:
1. 从COS下载到内存
2. 根据文件类型处理:
   - PDF/DOC/PPT → document.convert → Markdown
   - TXT/MD → 直接读取
   - 其他 → 仅备份，不入库
3. 上传到GitHub RAG仓库
4. 调用 rag.ingest 向量化
5. 更新KV存储状态:
   {
     "file_hash": "xxx",
     "status": "ingested",
     "ingest_time": "2024-01-01T00:00:00",
     "github_url": "https://github.com/...",
     "vectorized": true
   }
6. 清理内存
7. 记录日志
```

### 3.4 KV存储结构

```json
{
  "file_meta:{file_hash}": {
    "file_name": "xxx.pdf",
    "file_hash": "md5_hash",
    "file_size": 2345678,
    "cos_url": "cos://...",
    "cos_key": "files/xxx.pdf",
    "upload_time": "2024-01-01T12:00:00",
    "group_id": "123456",
    "uploader": "user_id",
    "uploader_name": "用户名",
    "status": "pending|approved|rejected|ingested",
    "file_type": "pdf",
    "summary": "AI生成的摘要",
    "is_educational": true,
    "github_url": "",
    "ingest_time": "",
    "vectorized": false
  },
  
  "daily_summary:{date}": {
    "date": "2024-01-01",
    "total_files": 15,
    "educational_files": 12,
    "pending_review": true,
    "admin_decision": "pending|approved_all|partial|rejected",
    "approved_files": ["hash1", "hash2"],
    "rejected_files": ["hash3"]
  }
}
```

## 4. 智能贡献功能

### 4.1 自动识别贡献意图

```
用户消息示例:
"我觉得王老师的自动控制原理课讲得特别好，
尤其是根轨迹那部分，很清晰。"
    │
    ▼
GLM识别:
- intent: contribution
- type: course_review
- course: 自动控制原理
- teacher: 王老师
- content: 评价内容
    │
    ▼
Bot回复:
"感谢您的评价！我检测到您想分享关于「自动控制原理-王老师」的课程评价。

我可以帮您:
1. 直接提交到课程仓库（需要审核）
2. 进入PR流程，您可以编辑更多内容

请选择: [直接提交] / [编辑后提交]"
```

### 4.2 智能PR流程

```
用户: "我想分享一份数据结构笔记"
    │
    ▼
Bot: "好的！请直接发送文件，或粘贴内容。"
    │
    ▼
用户发送文件/内容
    │
    ▼
Bot: "收到！正在处理..."
    │
    ├─▶ AI提取关键信息（课程代码、章节、内容摘要）
    ├─▶ 生成TOML格式
    ├─▶ AI审核（合规性检查）
    │
    ▼
Bot: "已为您生成以下内容:

课程: CS201 数据结构
章节: 第二章 线性表
内容摘要: [AI摘要]

内容预览:
```toml
[sections]
title = "线性表笔记"
...
```

是否提交? [提交] / [修改] / [取消]"
    │
    ▼
用户确认
    │
    ▼
自动提交PR
```

### 4.3 群设置

```
群初始化命令:
/hitsz init

Bot回复:
"欢迎使用HITSZ智能助手！

请设置本群信息:
1. 校区: [哈工大] [哈工深] [哈工威]
2. PR目标组织: [HIT-A] [HITSZ-OpenAuto] [其他]
3. 管理员: @用户1 @用户2

设置完成后，我将:
- 自动处理群文件
- 每日22:30发送文件汇总
- 智能识别贡献意图"
```

## 5. 搜索策略

### 5.1 分层搜索

```python
搜索优先级:

1. 课程相关查询:
   - L1: courses.search (HOA课程库)
   - L2: search (RAG知识库)
   - L3: web_search (AstrBot内置)

2. 文件相关查询:
   - L1: 群文件搜索
   - L2: COS文件搜索
   - L3: deep_search (论文/资料聚合)

3. 通用查询:
   - L1: web_search (AstrBot内置)
   - L2: search (RAG知识库)
```

### 5.2 深度搜索策略

```python
def deep_search(query, file_type="auto"):
    """
    根据文件类型选择搜索源
    """
    if file_type == "paper" or "论文" in query:
        # 学术论文搜索
        sources = ["arxiv", "annas_archive", "google_scholar"]
    elif file_type == "course_material":
        # 课件资料搜索
        sources = ["cos", "rag", "web"]
    elif file_type == "exam":
        # 考试资料搜索
        sources = ["cos", "rag", "github"]
    else:
        # 自动判断
        sources = ["rag", "web", "cos"]
    
    # 并行搜索
    results = await asyncio.gather(*[
        search_source(source, query) for source in sources
    ])
    
    # 聚合结果
    return aggregate_results(results)
```

## 6. 配置项

```yaml
# GLM配置
HITSZ_GLM_API_KEY=your_glm_api_key
HITSZ_GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
HITSZ_GLM_INTENT_MODEL=glm-4.7-flash
HITSZ_GLM_COMPLEX_MODEL=glm-5

# Agent Backend
HITSZ_AGENT_BACKEND_URL=http://localhost:8080
HITSZ_AGENT_BACKEND_API_KEY=

# 定时任务
HITSZ_INTENT_CHECK_INTERVAL=180  # 3分钟
HITSZ_DAILY_SUMMARY_TIME=22:30
HITSZ_FILE_SCAN_CRON=0 2 * * *   # 凌晨2点深度扫描

# 群设置
HITSZ_DEFAULT_CAMPUS=shenzhen
HITSZ_PR_TARGET_ORG=HITSZ-OpenAuto
HITSZ_ADMIN_USER_ID=123456789

# 文件处理
HITSZ_MAX_FILE_SIZE_MB=50
HITSZ_SUPPORTED_EXTENSIONS=pdf,doc,docx,ppt,pptx,txt,md,zip
HITSZ_CONTEXT_WINDOW_MINUTES=3

# 上下文管理
HITSZ_MAX_CONTEXT_LENGTH=4000  # 超过则分块
HITSZ_CONTEXT_COMPRESSION=true
```

## 7. 消息队列设计

```python
class MessageQueue:
    """3分钟滑动窗口消息队列"""
    
    def __init__(self, window_minutes=3):
        self.window = timedelta(minutes=window_minutes)
        self.messages = []
        self.last_check = None
    
    def add_message(self, msg):
        """添加消息"""
        self.messages.append({
            "time": datetime.now(),
            "content": msg,
            "processed": False
        })
        self._cleanup_old_messages()
    
    def has_new_messages(self):
        """检查是否有新消息（未处理）"""
        return any(not m["processed"] for m in self.messages)
    
    def get_context(self):
        """获取当前上下文"""
        self._cleanup_old_messages()
        return [m["content"] for m in self.messages]
    
    def mark_processed(self):
        """标记所有消息为已处理"""
        for m in self.messages:
            m["processed"] = True
    
    def _cleanup_old_messages(self):
        """清理过期消息"""
        cutoff = datetime.now() - self.window
        self.messages = [m for m in self.messages if m["time"] > cutoff]
```

## 8. 更新日志

### v3.0.0 (2025-03-16)
- 使用 GLM 4.7 Flash 进行意图判断
- 3分钟滑动窗口上下文管理
- 全自动群文件处理（无需实时确认）
- 每日总结需管理员确认
- 智能贡献识别和PR流程
- 分层搜索策略

