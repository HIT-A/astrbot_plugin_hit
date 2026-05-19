import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("PYTHONPATH", "/root/data/plugins")

from astrbot_plugin_hit.main import HITPlugin
from astrbot_plugin_hit.services.agent_client import AgentResponse
from astrbot_plugin_hit.services.file_scanner import FileScannerService


class FakeEvent:
    def __init__(self):
        self.messages = []

    async def send(self, msg):
        self.messages.append(str(msg))

    def plain_result(self, text):
        return text

    def get_sender_name(self):
        return "smoke-user"


class FakeAgentClient:
    async def search_files(self, keyword, limit=10, course_code=None, file_type=None):
        return AgentResponse(
            success=True,
            output={
                "results": [
                    {
                        "file_name": f"{keyword}-讲义.pdf",
                        "source": "mock",
                        "url": "https://example.com/file1.pdf",
                    }
                ]
            },
        )

    async def call_skill(self, skill_name, input_data, timeout=None):
        if skill_name == "search.deep":
            return {
                "ok": True,
                "output": {
                    "results": [
                        {
                            "title": f"Deep {input_data.get('query', '')}",
                            "source": "mock",
                            "url": "https://example.com/deep",
                        }
                    ]
                },
                "error": {},
            }
        if skill_name == "rag.ingest":
            title = input_data.get("title") or input_data.get("file_name") or "unknown"
            if "fail" in title.lower():
                return {"ok": False, "output": {}, "error": {"message": "mock ingest failed"}}
            return {"ok": True, "output": {"id": "mock-doc"}, "error": {}}
        return {"ok": True, "output": {}, "error": {}}


class FakePlugin:
    pass


async def main():
    fake = FakePlugin()
    fake.agent_client = FakeAgentClient()
    fake.file_scanner = FileScannerService(plugin=fake, agent_client=fake.agent_client)

    # Bind real handlers from HITPlugin to fake self
    fake._handle_file_search = HITPlugin._handle_file_search.__get__(fake, FakePlugin)
    fake._handle_deep_search = HITPlugin._handle_deep_search.__get__(fake, FakePlugin)

    event = FakeEvent()

    await HITPlugin.file_cmd(fake, event, "自动控制 课件")
    await HITPlugin.search_cmd(fake, event, "保研政策")

    await fake.file_scanner.add_uploaded_candidate(
        file_name="ok_material.pdf",
        cos_url="https://cos.example/ok.pdf",
        uploader_id="u1",
        uploader_name="tester",
    )
    await fake.file_scanner.add_uploaded_candidate(
        file_name="fail_material.pdf",
        cos_url="https://cos.example/fail.pdf",
        uploader_id="u2",
        uploader_name="tester",
    )

    await HITPlugin.ingest_cmd(fake, event, "5")

    print("SMOKE_MESSAGES_BEGIN")
    for i, m in enumerate(event.messages, 1):
        print(f"[{i}] {m}")
    print("SMOKE_MESSAGES_END")


if __name__ == "__main__":
    asyncio.run(main())
