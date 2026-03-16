# HIT Plugin for AstrBot

哈工大智能助手插件 - 基于混合AI架构的群聊智能助手

## Architecture

```
User Message
    │
    ▼
┌─────────────────────────────────────┐
│ Message Queue (3-min window)        │
└──────────────────┬──────────────────┘
                   │
    ┌──────────────┼──────────────┐
    ▼              ▼              ▼
┌────────┐   ┌──────────┐   ┌──────────┐
│History │   │  Gemini  │   │  Intent  │
│Manager │   │  Flash   │   │  Store   │
└────────┘   └────┬─────┘   └──────────┘
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
   ┌─────────┐        ┌──────────┐
   │  Chat   │        │  Valid   │
   │ (Ignore)│        │  Intent  │
   └─────────┘        └────┬─────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌─────────┐  ┌─────────┐  ┌─────────┐
        │ Course  │  │  File   │  │  Deep   │
        │ Search  │  │ Search  │  │ Search  │
        └────┬────┘  └────┬────┘  └────┬────┘
             │            │            │
             └────────────┼────────────┘
                          ▼
                   ┌──────────────┐
                   │  Main Agent  │
                   │  (AstrBot)   │
                   └──────────────┘
```

## Features

### 1. Intent Classification Service (`services/intent_classifier.py`)
- Uses **Gemini Flash** (cheap) for intent classification
- Classifies intents: `COURSE_QUERY`, `FILE_SEARCH`, `DEEP_SEARCH`, `CONTRIBUTION`, `CHAT`
- Returns structured intent with confidence score
- Filters out chat/noise to save main agent cost
- Daily quota management (default: 250 calls/day)

### 2. Tool Definitions (`tools/`)

#### `hit_course_search`
Search HIT course information by code, name, or teacher.

#### `hit_file_search`
Search files in group files and COS storage.

#### `hit_deep_search`
Deep search papers, literature, and technical materials (including arXiv and GitHub).

#### `hit_contribute_guide`
Guide users to contribute content (reviews, files).

### 3. History Management (`core/history_manager.py`)
- Learned from SpectreCore's HistoryStorage
- Stores chat history with metadata
- Supports retrieval for summarization
- File-based persistence with JSONL format
- Automatic cleanup of old history

### 4. File Scanner (`services/file_scanner.py`)
- Learned from GroupFS design
- Gets group file list
- Calculates MD5 hash for deduplication
- Uploads to COS (no local storage)
- Stream processing for large files

### 5. Chat Summarizer (`services/chat_summarizer.py`)
- Learned from Daily Analysis plugin
- Incremental analysis (only new messages)
- Filters educational content
- Generates summary with main agent
- Admin confirmation before ingesting

### 6. Main Plugin (`main.py`)
- Registers tools with `@filter.llm_tool`
- Routes to appropriate handler based on intent
- Uses main agent for complex interactions
- Uses Gemini for simple classification

## Directory Structure

```
astrbot_plugin_hit/
├── main.py                    # Main plugin entry
├── core/                      # Core modules
│   ├── __init__.py
│   ├── config.py             # Configuration management
│   ├── message_queue.py      # 3-minute sliding window queue
│   └── history_manager.py    # Chat history with metadata
├── services/                  # Business services
│   ├── __init__.py
│   ├── intent_classifier.py  # Gemini Flash intent classification
│   ├── file_scanner.py       # Group file scanning
│   ├── chat_summarizer.py    # Daily chat summary
│   └── agent_client.py       # Main agent client
├── tools/                     # Tool implementations
│   ├── __init__.py
│   ├── course_tool.py        # Course search tool
│   ├── file_tool.py          # File search tool
│   └── search_tool.py        # Deep search tool
└── utils/                     # Utility functions
    └── __init__.py
```

## Configuration

Add to AstrBot configuration:

```yaml
# Gemini Configuration
HIT_GEMINI_API_KEY=your_gemini_api_key
HIT_GEMINI_MODEL=gemini-2.5-flash-preview-05-20
HIT_GEMINI_DAILY_QUOTA=250

# Agent Backend
HIT_AGENT_BACKEND_URL=http://localhost:8080
HIT_AGENT_BACKEND_API_KEY=

# Scheduler
HIT_INTENT_CHECK_INTERVAL=180  # 3 minutes
HIT_DAILY_SUMMARY_TIME=22:30
HIT_CONTEXT_WINDOW_MINUTES=3

# Admin
HIT_ADMIN_USER_ID=123456789

# File Processing
HIT_MAX_FILE_SIZE_MB=50
HIT_SUPPORTED_EXTENSIONS=pdf,doc,docx,ppt,pptx,txt,md

# COS (Tencent Cloud Object Storage)
HIT_COS_BUCKET=your-bucket
HIT_COS_REGION=ap-guangzhou
HIT_COS_SECRET_ID=your-secret-id
HIT_COS_SECRET_KEY=your-secret-key
```

## Commands

### Admin Commands
- `/hit_scan` - Manually trigger group file scan
- `/hit_ingest` - Ingest uploaded files to knowledge base
- `/hit_summary` - Manually trigger chat summary
- `/hit_status` - Check plugin status

### User Commands (via LLM Tools)
The plugin registers LLM tools that the main agent can call:
- `hit_course_search` - Search courses
- `hit_file_search` - Search files
- `hit_deep_search` - Deep search papers/materials
- `hit_contribute_guide` - Get contribution guidance

## Workflow

### Intent Detection Flow
1. Messages are added to a 3-minute sliding window queue
2. Every 3 minutes (08:00-22:00), Gemini Flash classifies intent
3. Chat/noise is filtered out (saving main agent cost)
4. Valid intents are stored for the main agent to handle

### File Scanning Flow
1. Admin triggers `/hit_scan`
2. Plugin fetches group file list
3. For each file:
   - Calculate MD5 hash
   - Check if already uploaded (dedup)
   - Download to memory (no local storage)
   - Upload to COS
   - Store metadata
4. Admin triggers `/hit_ingest` to add to knowledge base

### Daily Summary Flow
1. Scheduled at 22:30 daily
2. Fetches messages from the day
3. Filters educational content (courses, exams, etc.)
4. Generates summary using main agent
5. Sends to admin for confirmation
6. Admin can approve/reject the summary

## Requirements

- Python 3.9+
- httpx (async HTTP client)
- apscheduler (scheduled tasks)
- AstrBot framework

## Installation

1. Copy the `astrbot_plugin_hit` folder to AstrBot's plugins directory
2. Configure environment variables or config file
3. Restart AstrBot
4. Use `/hit_status` to verify installation

## License

MIT License
