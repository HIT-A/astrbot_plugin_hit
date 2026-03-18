"""
群文件扫描服务 - 扫描群文件并上传到COS
"""

import os
import hashlib
import asyncio
import base64
import inspect
import mimetypes
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass

import httpx
from astrbot.api import logger


@dataclass
class FileInfo:
    """文件信息"""

    file_id: str
    file_name: str
    file_size: int
    file_url: str
    uploader_id: str
    uploader_name: str
    upload_time: datetime
    file_hash: Optional[str] = None
    cos_url: Optional[str] = None
    status: str = "pending"  # pending, scanned, uploaded, failed


@dataclass
class ScanResult:
    """扫描结果"""

    total_files: int
    new_files: int
    uploaded_files: int
    failed_files: int
    files: List[FileInfo]
    scan_time: datetime
    error_message: Optional[str] = None


@dataclass
class IngestResult:
    """入库结果"""

    total_candidates: int
    ingested_files: int
    failed_files: int
    skipped_files: int
    ingest_time: datetime
    details: List[Dict[str, Any]]
    error_message: Optional[str] = None


class FileScannerService:
    """群文件扫描服务"""

    def __init__(
        self,
        cos_bucket: str = "",
        cos_region: str = "",
        cos_secret_id: str = "",
        cos_secret_key: str = "",
        plugin=None,
        agent_client=None,
        max_file_size_mb: int = 50,
        supported_extensions: Optional[List[str]] = None,
        **kwargs,
    ):
        self.plugin = plugin
        self.agent_client = agent_client
        self.cos_bucket = cos_bucket
        self.cos_region = cos_region
        self.cos_secret_id = cos_secret_id
        self.cos_secret_key = cos_secret_key
        self.max_file_size = max_file_size_mb * 1024 * 1024  # 转换为字节
        self.supported_extensions = supported_extensions or [
            "pdf",
            "doc",
            "docx",
            "ppt",
            "pptx",
            "txt",
            "md",
            "zip",
            "rar",
            "7z",
        ]
        self._scanned_hashes: set = set()  # 已扫描文件的哈希集合
        self._uploaded_files: Dict[str, FileInfo] = {}  # 最近上传成功文件
        self._lock = asyncio.Lock()

    def _calculate_hash(self, content: bytes) -> str:
        """计算文件内容的MD5哈希"""
        return hashlib.md5(content).hexdigest()

    def _is_supported_file(self, file_name: str) -> bool:
        """检查文件类型是否支持"""
        ext = file_name.lower().split(".")[-1] if "." in file_name else ""
        return ext in self.supported_extensions

    def _is_oversized(self, file_size: int) -> bool:
        """检查文件是否超过大小限制"""
        return file_size > self.max_file_size

    async def scan_group_files(
        self,
        group_id: str,
        get_file_list_func: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
        download_file_func: Optional[Callable[[str], bytes]] = None,
    ) -> ScanResult:
        """
        扫描群文件

        Args:
            group_id: 群ID
            get_file_list_func: 获取文件列表的回调函数
            download_file_func: 下载文件的回调函数

        Returns:
            ScanResult 扫描结果
        """
        logger.info(f"🔍 开始扫描群 {group_id} 的文件...")
        scan_time = datetime.now()

        if get_file_list_func is None or download_file_func is None:
            msg = "当前平台未接入群文件列表/下载回调，无法执行扫描。"
            logger.warning(f"⚠️ {msg}")
            return ScanResult(
                total_files=0,
                new_files=0,
                uploaded_files=0,
                failed_files=0,
                files=[],
                scan_time=scan_time,
                error_message=msg,
            )

        try:
            # 获取文件列表
            file_list = await self._invoke_callback(get_file_list_func, group_id)
            if not isinstance(file_list, list):
                file_list = []

            files: List[FileInfo] = []
            new_files = 0
            uploaded_files = 0
            failed_files = 0

            for file_data in file_list:
                file_info = FileInfo(
                    file_id=file_data.get("file_id", ""),
                    file_name=file_data.get("file_name", ""),
                    file_size=file_data.get("file_size", 0),
                    file_url=file_data.get("file_url", ""),
                    uploader_id=file_data.get("uploader_id", ""),
                    uploader_name=file_data.get("uploader_name", ""),
                    upload_time=datetime.now(),
                )

                # 检查文件类型
                if not self._is_supported_file(file_info.file_name):
                    logger.debug(f"⏭️ 跳过不支持的文件类型: {file_info.file_name}")
                    continue

                # 检查文件大小
                if self._is_oversized(file_info.file_size):
                    logger.warning(f"⚠️ 文件过大，跳过: {file_info.file_name}")
                    file_info.status = "failed"
                    failed_files += 1
                    files.append(file_info)
                    continue

                try:
                    # 下载文件并计算哈希
                    file_content = await self._invoke_callback(
                        download_file_func,
                        {
                            "file_id": file_info.file_id,
                            "file_name": file_info.file_name,
                            "file_size": file_info.file_size,
                            "file_url": file_info.file_url,
                        },
                    )
                    if not isinstance(file_content, (bytes, bytearray)):
                        raise TypeError("download callback must return bytes")

                    file_hash = self._calculate_hash(file_content)
                    file_info.file_hash = file_hash

                    # 检查是否已存在
                    async with self._lock:
                        if file_hash in self._scanned_hashes:
                            logger.debug(f"⏭️ 文件已存在，跳过: {file_info.file_name}")
                            file_info.status = "scanned"
                            files.append(file_info)
                            continue

                    new_files += 1

                    # 上传到COS
                    cos_url = await self._upload_to_cos(
                        file_content, file_info.file_name, file_hash
                    )

                    if cos_url:
                        file_info.cos_url = cos_url
                        file_info.status = "uploaded"
                        uploaded_files += 1

                        async with self._lock:
                            self._scanned_hashes.add(file_hash)
                            self._uploaded_files[file_hash] = file_info

                        logger.info(f"✅ 文件上传成功: {file_info.file_name}")
                    else:
                        file_info.status = "failed"
                        failed_files += 1

                except Exception as e:
                    logger.error(f"❌ 处理文件失败 {file_info.file_name}: {e}")
                    file_info.status = "failed"
                    failed_files += 1

                files.append(file_info)

            result = ScanResult(
                total_files=len(files),
                new_files=new_files,
                uploaded_files=uploaded_files,
                failed_files=failed_files,
                files=files,
                scan_time=scan_time,
            )

            logger.info(
                f"✅ 群 {group_id} 文件扫描完成: "
                f"总计 {result.total_files}, 新增 {result.new_files}, "
                f"上传 {result.uploaded_files}, 失败 {result.failed_files}"
            )

            return result

        except Exception as e:
            logger.error(f"❌ 扫描群文件失败: {e}")
            return ScanResult(
                total_files=0,
                new_files=0,
                uploaded_files=0,
                failed_files=0,
                files=[],
                scan_time=scan_time,
                error_message=str(e),
            )

    async def _invoke_callback(self, callback: Callable[..., Any], *args) -> Any:
        """兼容同步/异步回调调用。"""
        if callback is None:
            return None

        if inspect.iscoroutinefunction(callback):
            return await callback(*args)

        result = callback(*args)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _upload_to_cos(
        self, file_content: bytes, file_name: str, file_hash: str
    ) -> Optional[str]:
        """
        上传文件到腾讯云COS

        Args:
            file_content: 文件内容
            file_name: 原始文件名
            file_hash: 文件哈希

        Returns:
            COS文件URL或None（上传失败）
        """
        try:
            if not self.agent_client:
                logger.error("❌ Agent 客户端未初始化，无法上传到 COS")
                return None

            ext = ""
            if "." in file_name:
                ext = file_name.rsplit(".", 1)[-1].lower()
            key_suffix = f".{ext}" if ext else ""
            object_key = f"group-files/{file_hash[:2]}/{file_hash[2:4]}/{file_hash}{key_suffix}"
            content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

            result = await self.agent_client.invoke_skill(
                "files.upload",
                {
                    "key": object_key,
                    "content_base64": base64.b64encode(file_content).decode("utf-8"),
                    "content_type": content_type,
                },
                timeout=120.0,
            )

            if not result.success:
                msg = (result.error or {}).get("message", "未知错误")
                logger.error(f"❌ files.upload 失败: {msg}")
                return None

            out = result.output or {}
            # 兼容不同后端返回：access_url/url/key/results
            url = str(out.get("access_url") or out.get("url") or "").strip()
            if url:
                logger.info(f"☁️ 文件上传到 COS 成功: {url}")
                return url

            key = str(out.get("key") or "").strip()
            if key:
                logger.info(f"☁️ 文件上传到 COS 成功: key={key}")
                return f"key://{key}"

            results = out.get("results")
            if isinstance(results, list) and results:
                first = results[0] if isinstance(results[0], dict) else {}
                url = str(first.get("access_url") or first.get("url") or "").strip()
                if url:
                    logger.info(f"☁️ 文件上传到 COS 成功: {url}")
                    return url
                key = str(first.get("key") or "").strip()
                if key:
                    logger.info(f"☁️ 文件上传到 COS 成功: key={key}")
                    return f"key://{key}"

            logger.warning("⚠️ files.upload 成功但未返回 url/key，回退记录 object key")
            return f"key://{object_key}"
        except Exception as e:
            logger.error(f"❌ 上传到COS失败: {e}")
            return None

    def _get_cos_auth(self, method: str, object_key: str) -> str:
        """获取COS请求签名（简化示例）"""
        # 实际实现需要使用腾讯云COS的签名算法
        # 参考: https://cloud.tencent.com/document/product/436/7778
        import hmac
        import base64

        timestamp = int(datetime.now().timestamp())
        date_str = datetime.now().strftime("%Y-%m-%d")

        # 简化示例，实际需要完整的签名计算
        string_to_sign = f"{method}\n\n\n{timestamp}\n/{self.cos_bucket}/{object_key}"
        signature = base64.b64encode(
            hmac.new(
                self.cos_secret_key.encode(), string_to_sign.encode(), hashlib.sha1
            ).digest()
        ).decode()

        return f"q-sign-algorithm=sha1&q-ak={self.cos_secret_id}&q-sign-time={timestamp};{timestamp + 3600}&q-key-time={timestamp};{timestamp + 3600}&q-header-list=&q-url-param-list=&q-signature={signature}"

    async def check_file_exists(self, file_hash: str) -> bool:
        """检查文件是否已存在"""
        async with self._lock:
            return file_hash in self._scanned_hashes

    async def add_uploaded_candidate(
        self,
        file_name: str,
        cos_url: str,
        file_hash: str = "",
        uploader_id: str = "",
        uploader_name: str = "",
    ) -> None:
        """向待入库集合注入候选文件（用于手动补录/测试）。"""
        now = datetime.now()
        if not file_hash:
            file_hash = hashlib.md5(f"{file_name}:{cos_url}:{now.isoformat()}".encode()).hexdigest()

        info = FileInfo(
            file_id=file_hash,
            file_name=file_name,
            file_size=0,
            file_url=cos_url,
            uploader_id=uploader_id,
            uploader_name=uploader_name,
            upload_time=now,
            file_hash=file_hash,
            cos_url=cos_url,
            status="uploaded",
        )
        async with self._lock:
            self._uploaded_files[file_hash] = info

    async def ingest_uploaded_files(self, limit: int = 20) -> IngestResult:
        """将已上传文件批量提交给 data.ingest。"""
        now = datetime.now()

        if not self.agent_client:
            return IngestResult(
                total_candidates=0,
                ingested_files=0,
                failed_files=0,
                skipped_files=0,
                ingest_time=now,
                details=[],
                error_message="Agent 客户端未初始化，无法执行入库。",
            )

        safe_limit = max(1, min(limit, 100))
        async with self._lock:
            candidates = list(self._uploaded_files.values())[:safe_limit]

        if not candidates:
            return IngestResult(
                total_candidates=0,
                ingested_files=0,
                failed_files=0,
                skipped_files=0,
                ingest_time=now,
                details=[],
                error_message="没有可入库的候选文件，请先执行 /hit scan 或补录候选。",
            )

        ingested = 0
        failed = 0
        details: List[Dict[str, Any]] = []

        for file_info in candidates:
            if not file_info.cos_url:
                details.append(
                    {
                        "file_name": file_info.file_name,
                        "status": "skipped",
                        "error": "缺少 cos_url",
                    }
                )
                continue

            payload = {
                "source_type": "manual",
                "source_name": file_info.file_name,
                "content": (
                    f"上传文件: {file_info.file_name}\n"
                    f"COS链接: {file_info.cos_url}\n"
                    f"上传者: {file_info.uploader_name or file_info.uploader_id}\n"
                    f"上传时间: {file_info.upload_time.isoformat()}\n"
                    f"文件哈希: {file_info.file_hash or ''}\n"
                ),
                "store_in_cos": False,
                "auto_ingest_rag": True,
                "overwrite": False,
            }

            try:
                result = await self.agent_client.invoke_skill("data.ingest", payload)
                if result.success:
                    ingested += 1
                    output = result.output or {}
                    details.append(
                        {
                            "file_name": file_info.file_name,
                            "status": output.get("status") or "ingested",
                            "job_id": output.get("job_id", ""),
                        }
                    )
                else:
                    failed += 1
                    err = (result.error or {}).get("message", "未知错误")
                    details.append(
                        {
                            "file_name": file_info.file_name,
                            "status": "failed",
                            "error": err,
                        }
                    )
            except Exception as e:
                failed += 1
                details.append(
                    {
                        "file_name": file_info.file_name,
                        "status": "failed",
                        "error": str(e),
                    }
                )

        skipped = len([d for d in details if d.get("status") == "skipped"])
        return IngestResult(
            total_candidates=len(candidates),
            ingested_files=ingested,
            failed_files=failed,
            skipped_files=skipped,
            ingest_time=now,
            details=details,
            error_message=None,
        )

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_scanned": len(self._scanned_hashes),
            "uploaded_pending_ingest": len(self._uploaded_files),
            "max_file_size_mb": self.max_file_size / (1024 * 1024),
            "supported_extensions": self.supported_extensions,
            "cos_bucket": self.cos_bucket,
            "cos_region": self.cos_region,
        }
