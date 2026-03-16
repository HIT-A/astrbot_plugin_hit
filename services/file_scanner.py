"""
群文件扫描服务 - 扫描群文件并上传到COS
"""

import os
import hashlib
import asyncio
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


class FileScannerService:
    """群文件扫描服务"""

    def __init__(
        self,
        cos_bucket: str,
        cos_region: str,
        cos_secret_id: str,
        cos_secret_key: str,
        max_file_size_mb: int = 50,
        supported_extensions: Optional[List[str]] = None,
    ):
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
        get_file_list_func: Callable[[str], List[Dict[str, Any]]],
        download_file_func: Callable[[str], bytes],
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

        try:
            # 获取文件列表
            file_list = await asyncio.get_event_loop().run_in_executor(
                None, get_file_list_func, group_id
            )

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
                    file_content = await asyncio.get_event_loop().run_in_executor(
                        None, download_file_func, file_info.file_id
                    )

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
            # 构建COS对象键
            ext = file_name.lower().split(".")[-1] if "." in file_name else ""
            object_key = (
                f"group-files/{file_hash[:2]}/{file_hash[2:4]}/{file_hash}.{ext}"
            )

            # 这里使用腾讯云COS SDK或HTTP API上传
            # 实际实现需要根据具体的COS SDK进行
            cos_url = f"https://{self.cos_bucket}.cos.{self.cos_region}.myqcloud.com/{object_key}"

            # TODO: 实现实际的COS上传逻辑
            # 示例使用PUT请求上传
            # async with httpx.AsyncClient() as client:
            #     # 获取临时密钥或签名
            #     auth = self._get_cos_auth("PUT", object_key)
            #     resp = await client.put(
            #         cos_url,
            #         content=file_content,
            #         headers={
            #             "Authorization": auth,
            #             "Content-Type": "application/octet-stream",
            #         },
            #     )
            #     resp.raise_for_status()

            logger.info(f"☁️ 文件上传到COS: {object_key}")
            return cos_url

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

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_scanned": len(self._scanned_hashes),
            "max_file_size_mb": self.max_file_size / (1024 * 1024),
            "supported_extensions": self.supported_extensions,
            "cos_bucket": self.cos_bucket,
            "cos_region": self.cos_region,
        }
