# -*- coding: utf-8 -*-
"""
副功能资源索引缓存：为任务库、模型库、机库与炮镜库提供轻量级持久化扫描结果。

功能定位:
- 将每个资源项的扫描结果保存为 JSON，减少重启后重复统计目录和编码封面。
- 使用目录与封面文件签名判断单项缓存是否仍可复用。

输入输出:
- 输入: 资源库根目录、资源项目录、封面文件路径、前端卡片数据。
- 输出: 可复用的卡片数据记录与本地 JSON 缓存文件。
"""
import json
import hashlib
import os
import re
from pathlib import Path
from typing import Any

from utils.logger import get_logger
from utils.utils import get_docs_data_dir

log = get_logger(__name__)

CACHE_VERSION = 1


class ResourceIndexCache:
    """基于 JSON 的资源索引缓存。"""

    def __init__(self, cache_name: str, cache_dir: str | Path | None = None):
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(cache_name)).strip("._")
        if not safe_name:
            safe_name = "resource"
        base_dir = Path(cache_dir) if cache_dir else get_docs_data_dir() / "cache" / "resource_index"
        self.cache_file = base_dir / f"{safe_name}.json"

    def load_records(self, root_path: str | Path) -> dict[str, dict[str, Any]]:
        """读取指定资源库根目录对应的缓存记录。"""
        try:
            if not self.cache_file.exists():
                return {}
            with open(self.cache_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                return {}
            if payload.get("version") != CACHE_VERSION:
                return {}
            if payload.get("root_key") != self._path_key(root_path):
                return {}
            records = payload.get("records")
            return records if isinstance(records, dict) else {}
        except Exception as e:
            log.debug(f"读取资源索引缓存失败，已忽略: {e}")
            return {}

    def save_records(self, root_path: str | Path, records: dict[str, dict[str, Any]]) -> None:
        """原子写入指定资源库根目录的缓存记录。"""
        payload = {
            "version": CACHE_VERSION,
            "root_key": self._path_key(root_path),
            "root_path": str(root_path),
            "records": records,
        }
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = self.cache_file.with_suffix(self.cache_file.suffix + ".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            tmp_file.replace(self.cache_file)
        except Exception as e:
            log.debug(f"写入资源索引缓存失败，已忽略: {e}")

    def clear(self) -> None:
        """清空当前索引缓存文件。"""
        try:
            if self.cache_file.exists():
                self.cache_file.unlink()
        except Exception as e:
            log.debug(f"清空资源索引缓存失败，已忽略: {e}")

    def build_root_signature(self, root_path: str | Path, skip_hidden: bool = True) -> list[dict[str, Any]]:
        """生成资源库顶层目录签名，用于判断内存缓存是否仍可复用。"""
        root = Path(root_path)
        signature: list[dict[str, Any]] = []
        try:
            for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                if not child.is_dir():
                    continue
                if skip_hidden and child.name.startswith("."):
                    continue
                stat = child.stat()
                content = self._build_dir_content_signature(child)
                signature.append({
                    "name": child.name,
                    "mtime_ns": self._mtime_ns(stat),
                    "size": int(getattr(stat, "st_size", 0)),
                    **content,
                })
        except Exception:
            return []
        return signature

    def build_item_signature(
        self,
        item_dir: str | Path,
        cover_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """生成单个资源项签名，目录或封面变化时缓存自然失效。"""
        item = Path(item_dir)
        item_stat = self._safe_stat(item)
        cover = Path(cover_path) if cover_path else None
        cover_stat = self._safe_stat(cover) if cover else None
        content = self._build_dir_content_signature(item)
        return {
            "item_key": self._path_key(item),
            "item_mtime_ns": self._mtime_ns(item_stat),
            "item_size": int(getattr(item_stat, "st_size", 0)) if item_stat else 0,
            **content,
            "cover_key": self._path_key(cover) if cover and cover.exists() else "",
            "cover_mtime_ns": self._mtime_ns(cover_stat),
            "cover_size": int(getattr(cover_stat, "st_size", 0)) if cover_stat else 0,
        }

    def _build_dir_content_signature(self, item_dir: Path) -> dict[str, Any]:
        digest = hashlib.sha1()
        file_count = 0
        total_size = 0
        max_mtime_ns = 0
        try:
            base = item_dir.resolve(strict=False)
            for path in sorted(item_dir.rglob("*"), key=lambda p: str(p).lower()):
                if not path.is_file():
                    continue
                stat = path.stat()
                rel = path.resolve(strict=False).relative_to(base).as_posix()
                mtime_ns = self._mtime_ns(stat)
                size = int(getattr(stat, "st_size", 0))
                digest.update(f"{rel}\0{mtime_ns}\0{size}\n".encode("utf-8", errors="ignore"))
                file_count += 1
                total_size += size
                max_mtime_ns = max(max_mtime_ns, mtime_ns)
        except Exception:
            pass
        return {
            "content_hash": digest.hexdigest(),
            "content_file_count": file_count,
            "content_size": total_size,
            "content_mtime_ns": max_mtime_ns,
        }

    @staticmethod
    def get_cached_item(
        cached_records: dict[str, dict[str, Any]],
        item_name: str,
        signature: dict[str, Any],
    ) -> dict[str, Any] | None:
        """按资源项名称和签名读取可复用的卡片数据。"""
        record = cached_records.get(item_name)
        if not isinstance(record, dict):
            return None
        if record.get("signature") != signature:
            return None
        item = record.get("item")
        return dict(item) if isinstance(item, dict) else None

    @staticmethod
    def make_record(signature: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        """生成可写入 JSON 的单项缓存记录。"""
        return {"signature": signature, "item": item}

    @staticmethod
    def _safe_stat(path: Path | None):
        try:
            return path.stat() if path else None
        except Exception:
            return None

    @staticmethod
    def _mtime_ns(stat_result) -> int:
        if stat_result is None:
            return 0
        return int(getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000)))

    @staticmethod
    def _path_key(path: str | Path | None) -> str:
        if path is None:
            return ""
        try:
            resolved = Path(path).resolve(strict=False)
        except Exception:
            resolved = Path(path)
        return os.path.normcase(os.path.normpath(str(resolved)))
