# -*- coding: utf-8 -*-
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Callable

from utils.logger import get_logger


log = get_logger(__name__)


class SoundReplaceService:
    MANIFEST_VERSION = 1
    MAX_LOGS = 10

    def __init__(self, backup_root: str | Path):
        self.backup_root = Path(backup_root)

    def set_backup_root(self, backup_root: str | Path):
        self.backup_root = Path(backup_root)

    def _game_path_hash(self, game_root: str | Path) -> str:
        normalized = str(Path(game_root).resolve(strict=False)).replace("\\", "/").lower()
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:12]

    def _game_backup_dir(self, game_root: str | Path) -> Path:
        return self.backup_root / self._game_path_hash(game_root)

    def _active_manifest_path(self, game_backup_dir: Path) -> Path:
        return game_backup_dir / "active_manifest.json"

    def _pending_install_path(self, game_backup_dir: Path) -> Path:
        return game_backup_dir / "pending_install.json"

    def _sha256(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _is_path_inside(self, child: Path, parent: Path) -> bool:
        try:
            child_resolved = child.resolve(strict=False)
            parent_resolved = parent.resolve(strict=False)
            common = os.path.commonpath([str(child_resolved), str(parent_resolved)])
            return os.path.normcase(common) == os.path.normcase(str(parent_resolved))
        except (OSError, ValueError):
            return False

    def _is_safe_sound_path(self, game_root: str | Path, target_path: str | Path) -> bool:
        game_root = Path(game_root)
        target = Path(target_path)
        sound_dir = game_root / "sound"
        mod_dir = sound_dir / "mod"
        if target.suffix.lower() != ".bank":
            return False
        if not self._is_path_inside(target, sound_dir):
            return False
        if self._is_path_inside(target, mod_dir):
            return False
        return True

    def _is_safe_existing_sound_target(self, game_root: str | Path, target_path: str | Path) -> bool:
        target = Path(target_path)
        return self._is_safe_sound_path(game_root, target) and target.is_file()

    def _is_safe_backup_path(self, game_backup_dir: Path, backup_path: Path) -> bool:
        originals_dir = game_backup_dir / "originals"
        if backup_path.suffix.lower() != ".bank":
            return False
        return self._is_path_inside(backup_path, originals_dir)

    def _normalize_source_rel(self, relative_path: str) -> Path | None:
        raw = str(relative_path or "").replace("\\", "/").strip()
        if not raw:
            return None
        rel = Path(raw)
        if rel.is_absolute() or ".." in rel.parts:
            return None
        return rel

    def _safe_rel(self, path: Path, base: Path) -> str:
        return path.resolve(strict=False).relative_to(base.resolve(strict=False)).as_posix()

    def _load_json(self, path: Path, default):
        if not path.is_file():
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else default
        except Exception:
            log.warning(f"读取 Sound 替换 JSON 失败: {path}", exc_info=True)
            return default

    def _save_json_atomic(self, path: Path, data: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def _empty_manifest(self, game_root: str | Path) -> dict:
        return {
            "version": self.MANIFEST_VERSION,
            "game_root": str(Path(game_root).resolve(strict=False)),
            "game_path_hash": self._game_path_hash(game_root),
            "active_entries": [],
            "updated_at": "",
        }

    def _load_active_manifest(self, game_root: str | Path, game_backup_dir: Path) -> dict:
        manifest = self._load_json(self._active_manifest_path(game_backup_dir), self._empty_manifest(game_root))
        entries = manifest.get("active_entries")
        if not isinstance(entries, list):
            manifest["active_entries"] = []
        manifest.setdefault("version", self.MANIFEST_VERSION)
        manifest.setdefault("game_root", str(Path(game_root).resolve(strict=False)))
        manifest.setdefault("game_path_hash", self._game_path_hash(game_root))
        return manifest

    def _save_active_manifest(self, game_backup_dir: Path, manifest: dict):
        manifest["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._save_json_atomic(self._active_manifest_path(game_backup_dir), manifest)

    def _clear_active_entry(self, game_backup_dir: Path, manifest: dict, target_rel: str) -> int:
        target_key = str(target_rel or "").lower()
        entries = list(manifest.get("active_entries", []))
        kept = [entry for entry in entries if str(entry.get("target_rel", "")).lower() != target_key]
        manifest["active_entries"] = kept
        self._save_active_manifest(game_backup_dir, manifest)
        return len(entries) - len(kept)

    def _target_changed_result(self, game_backup_dir: Path, manifest: dict, target_rel: str, entry: dict, current_sha: str) -> dict:
        original_sha = str(entry.get("original_sha256") or "")
        cleared = 0
        message = "检测到游戏文件已被外部修改，请先还原或校验游戏文件后再重试。"
        if original_sha and current_sha == original_sha:
            cleared = self._clear_active_entry(game_backup_dir, manifest, target_rel)
            message = "检测到游戏文件已校验恢复，已清除旧替换记录。请更新游戏或重新执行替换。"
        return {
            "success": False,
            "error_code": "target_changed_externally",
            "target_rel": target_rel,
            "cleared_manifest_entries": cleared,
            "error": message,
        }

    def _copy_file_atomic(self, source: Path, target: Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f"{target.name}.aimerwt_tmp")
        try:
            if tmp.exists():
                tmp.unlink()
            shutil.copy2(source, tmp)
            if target.exists():
                try:
                    os.chmod(tmp, target.stat().st_mode)
                except OSError:
                    pass
            os.replace(tmp, target)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    def _copy_backup_file(self, source: Path, backup_path: Path):
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup_path)

    def _write_operation_log(self, game_backup_dir: Path, payload: dict):
        try:
            log_dir = game_backup_dir / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            log_path = log_dir / f"{stamp}_{payload.get('operation', 'sound_replace')}.json"
            self._save_json_atomic(log_path, payload)
            logs = sorted([p for p in log_dir.glob("*.json") if p.is_file()], key=lambda p: p.stat().st_mtime)
            for old_log in logs[:-self.MAX_LOGS]:
                try:
                    old_log.unlink()
                except OSError:
                    pass
        except Exception:
            log.warning("写入 Sound 替换操作日志失败", exc_info=True)

    def _cleanup_new_backups(self, backup_paths: list[Path], originals_dir: Path):
        for backup_path in reversed(backup_paths):
            try:
                if backup_path.is_file() and self._is_path_inside(backup_path, originals_dir):
                    backup_path.unlink()
                    parent = backup_path.parent
                    while parent != originals_dir and self._is_path_inside(parent, originals_dir):
                        try:
                            parent.rmdir()
                        except OSError:
                            break
                        parent = parent.parent
            except OSError:
                log.warning(f"清理 Sound 替换新备份失败: {backup_path}", exc_info=True)

    def _isolate_orphan_backup(self, game_backup_dir: Path, backup_path: Path, target_rel: str) -> Path:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        orphan_path = game_backup_dir / "orphaned" / stamp / target_rel
        counter = 1
        while orphan_path.exists():
            orphan_path = game_backup_dir / "orphaned" / f"{stamp}_{counter}" / target_rel
            counter += 1
        orphan_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(backup_path, orphan_path)
        return orphan_path

    def _build_sound_index(self, game_root: Path) -> dict[str, list[Path]]:
        index: dict[str, list[Path]] = {}
        sound_dir = game_root / "sound"
        if not sound_dir.is_dir():
            return index
        for path in sound_dir.rglob("*.bank"):
            if self._is_safe_existing_sound_target(game_root, path):
                index.setdefault(path.name.lower(), []).append(path)
        return index

    def preview_install(self, game_root: str | Path, source_mod_path: str | Path, install_list: list[str] | None) -> dict:
        game_root = Path(game_root)
        source_mod_path = Path(source_mod_path)
        source_root = source_mod_path.resolve(strict=False)
        sound_dir = game_root / "sound"
        sound_index = self._build_sound_index(game_root)
        matched_files = []
        skipped_files = []
        seen_targets = set()
        game_backup_dir = self._game_backup_dir(game_root)
        active_manifest = self._load_active_manifest(game_root, game_backup_dir)
        active_by_target = {
            str(entry.get("target_rel", "")).lower(): entry
            for entry in active_manifest.get("active_entries", [])
            if entry.get("target_rel")
        }
        sound_bank_size_bytes = 0
        for paths in sound_index.values():
            for path in paths:
                try:
                    sound_bank_size_bytes += path.stat().st_size
                except OSError:
                    pass

        for item in install_list or []:
            rel = self._normalize_source_rel(item)
            if rel is None:
                skipped_files.append({"source_rel": str(item), "reason": "unsafe_source_path"})
                continue
            source_path = (source_mod_path / rel).resolve(strict=False)
            if not self._is_path_inside(source_path, source_root):
                skipped_files.append({"source_rel": rel.as_posix(), "reason": "unsafe_source_path"})
                continue
            if source_path.suffix.lower() != ".bank" or not source_path.is_file():
                skipped_files.append({"source_rel": rel.as_posix(), "reason": "not_bank_file"})
                continue

            candidates = sound_index.get(source_path.name.lower(), [])
            if not candidates:
                skipped_files.append({"source_rel": rel.as_posix(), "reason": "target_not_found"})
                continue
            if len(candidates) > 1:
                skipped_files.append({"source_rel": rel.as_posix(), "reason": "ambiguous_target"})
                continue

            target_path = candidates[0]
            target_rel = self._safe_rel(target_path, sound_dir)
            target_key = target_rel.lower()
            if target_key in seen_targets:
                skipped_files.append({"source_rel": rel.as_posix(), "target_rel": target_rel, "reason": "duplicate_target"})
                continue
            seen_targets.add(target_key)
            active_entry = active_by_target.get(target_key)
            backup_skipped_existing = bool(active_entry and active_entry.get("backup_skipped"))
            needs_backup = active_entry is None
            if active_entry and not backup_skipped_existing:
                backup_rel = str(active_entry.get("original_backup_rel", ""))
                backup_path = game_backup_dir / backup_rel
                needs_backup = not (
                    backup_rel
                    and self._is_safe_backup_path(game_backup_dir, backup_path)
                    and backup_path.is_file()
                )

            matched_files.append({
                "source_rel": rel.as_posix(),
                "source_name": source_path.name,
                "target_rel": target_rel,
                "source_path": str(source_path),
                "target_path": str(target_path),
                "source_sha256": self._sha256(source_path),
                "target_sha256": self._sha256(target_path),
                "target_size_bytes": target_path.stat().st_size,
                "needs_backup": needs_backup,
                "backup_skipped_existing": backup_skipped_existing,
            })

        return {
            "success": True,
            "installable_count": len(matched_files),
            "skipped_count": len(skipped_files),
            "matched_files": matched_files,
            "skipped_files": skipped_files,
            "backup_dir": str(self._game_backup_dir(game_root)),
            "backup_size_bytes": sum(
                item.get("target_size_bytes", 0)
                for item in matched_files
                if item.get("needs_backup")
            ),
            "sound_bank_size_bytes": sound_bank_size_bytes,
            "backup_skipped_existing_count": sum(1 for item in matched_files if item.get("backup_skipped_existing")),
        }

    def install(
        self,
        game_root: str | Path,
        source_mod_path: str | Path,
        install_list: list[str] | None,
        mod_name: str = "",
        progress_callback: Callable[[int, str], None] | None = None,
        skip_backup: bool = False,
    ) -> dict:
        game_root = Path(game_root)
        source_mod_path = Path(source_mod_path)
        game_backup_dir = self._game_backup_dir(game_root)
        originals_dir = game_backup_dir / "originals"

        preview = self.preview_install(game_root, source_mod_path, install_list)
        matched_files = preview.get("matched_files", [])
        if not matched_files:
            return {
                "success": False,
                "error_code": "no_matching_targets",
                "error": "未找到可替换的游戏 Sound 源文件",
                **preview,
            }

        game_backup_dir.mkdir(parents=True, exist_ok=True)
        active_manifest = self._load_active_manifest(game_root, game_backup_dir)
        active_by_target = {
            str(entry.get("target_rel", "")).lower(): entry
            for entry in active_manifest.get("active_entries", [])
            if entry.get("target_rel")
        }
        backup_plan = []
        newly_created_backups: list[Path] = []

        if progress_callback:
            progress_callback(5, "检查 Sound 替换目标...")

        try:
            for idx, item in enumerate(matched_files):
                target_rel = item["target_rel"]
                target_key = target_rel.lower()
                target_path = Path(item["target_path"])
                backup_path = originals_dir / target_rel
                if not self._is_safe_existing_sound_target(game_root, target_path):
                    self._cleanup_new_backups(newly_created_backups, originals_dir)
                    return {"success": False, "error_code": "unsafe_target_path", "target_rel": target_rel}

                active_entry = active_by_target.get(target_key)
                current_sha = self._sha256(target_path)
                is_backup_skipped = False
                if active_entry:
                    expected_sha = active_entry.get("replacement_sha256", "")
                    original_backup_rel = str(active_entry.get("original_backup_rel", ""))
                    was_backup_skipped = bool(active_entry.get("backup_skipped"))
                    if not expected_sha:
                        self._cleanup_new_backups(newly_created_backups, originals_dir)
                        return {"success": False, "error_code": "active_manifest_invalid", "target_rel": target_rel}
                    if was_backup_skipped:
                        if current_sha != expected_sha:
                            self._cleanup_new_backups(newly_created_backups, originals_dir)
                            return self._target_changed_result(game_backup_dir, active_manifest, target_rel, active_entry, current_sha)
                        original_sha = active_entry.get("original_sha256", "")
                        is_backup_skipped = True
                    else:
                        active_backup_path = game_backup_dir / original_backup_rel
                        if not original_backup_rel:
                            self._cleanup_new_backups(newly_created_backups, originals_dir)
                            return {"success": False, "error_code": "active_manifest_invalid", "target_rel": target_rel}
                        if not self._is_safe_backup_path(game_backup_dir, active_backup_path):
                            self._cleanup_new_backups(newly_created_backups, originals_dir)
                            return {"success": False, "error_code": "unsafe_backup_path", "target_rel": target_rel}
                        if not active_backup_path.is_file():
                            self._cleanup_new_backups(newly_created_backups, originals_dir)
                            return {"success": False, "error_code": "backup_missing", "target_rel": target_rel}
                        if current_sha != expected_sha:
                            self._cleanup_new_backups(newly_created_backups, originals_dir)
                            return self._target_changed_result(game_backup_dir, active_manifest, target_rel, active_entry, current_sha)
                        original_sha = active_entry.get("original_sha256", "")
                else:
                    if skip_backup:
                        original_backup_rel = ""
                        original_sha = current_sha
                        is_backup_skipped = True
                    else:
                        if not self._is_safe_backup_path(game_backup_dir, backup_path):
                            self._cleanup_new_backups(newly_created_backups, originals_dir)
                            return {"success": False, "error_code": "unsafe_backup_path", "target_rel": target_rel}
                        if backup_path.exists():
                            self._isolate_orphan_backup(game_backup_dir, backup_path, target_rel)
                        self._copy_backup_file(target_path, backup_path)
                        newly_created_backups.append(backup_path)
                        original_backup_rel = self._safe_rel(backup_path, game_backup_dir)
                        original_sha = current_sha

                backup_plan.append({
                    **item,
                    "original_backup_rel": original_backup_rel,
                    "original_sha256": original_sha,
                    "previous_entry": active_entry,
                    "backup_skipped": is_backup_skipped,
                })
                if progress_callback:
                    bp = 10 + int((idx + 1) / len(matched_files) * 30)
                    progress_callback(bp, f"备份: {Path(target_rel).name}" if not is_backup_skipped else f"检查: {Path(target_rel).name}")
        except Exception as e:
            self._cleanup_new_backups(newly_created_backups, originals_dir)
            return {"success": False, "error_code": "backup_failed", "error": str(e), "backup_dir": str(game_backup_dir)}

        pending_payload = {
            "version": self.MANIFEST_VERSION,
            "operation": "install",
            "mod_name": str(mod_name or source_mod_path.name),
            "game_root": str(game_root.resolve(strict=False)),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "entries": [
                {
                    "source_rel": item["source_rel"],
                    "target_rel": item["target_rel"],
                    "original_backup_rel": item["original_backup_rel"],
                    "source_sha256": item["source_sha256"],
                    "original_sha256": item["original_sha256"],
                    "backup_skipped": bool(item.get("backup_skipped")),
                }
                for item in backup_plan
            ],
        }
        try:
            self._save_json_atomic(self._pending_install_path(game_backup_dir), pending_payload)
        except Exception as e:
            self._cleanup_new_backups(newly_created_backups, originals_dir)
            return {
                "success": False,
                "error_code": "pending_manifest_save_failed",
                "error": str(e),
                "backup_dir": str(game_backup_dir),
            }

        if progress_callback:
            progress_callback(42, "备份完成，开始替换..." if not skip_backup else "已跳过备份，开始替换...")

        replaced_entries = []
        failed_files = []
        for idx, item in enumerate(backup_plan):
            source_path = Path(item["source_path"])
            target_path = Path(item["target_path"])
            target_rel = item["target_rel"]
            try:
                self._copy_file_atomic(source_path, target_path)
                replaced_entries.append({
                    "mod_name": str(mod_name or source_mod_path.name),
                    "source_rel": item["source_rel"],
                    "target_rel": target_rel,
                    "original_backup_rel": item["original_backup_rel"],
                    "original_sha256": item["original_sha256"],
                    "source_sha256": item["source_sha256"],
                    "replacement_sha256": self._sha256(target_path),
                    "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "backup_skipped": bool(item.get("backup_skipped")),
                })
            except Exception as e:
                failed_files.append({"target_rel": target_rel, "reason": str(e)})
            if progress_callback:
                progress = 45 + int((idx + 1) / len(backup_plan) * 45)
                progress_callback(progress, f"替换: {Path(target_rel).name}")

        replaced_keys = {entry["target_rel"].lower() for entry in replaced_entries}
        next_entries = [
            entry
            for entry in active_manifest.get("active_entries", [])
            if str(entry.get("target_rel", "")).lower() not in replaced_keys
        ]
        next_entries.extend(replaced_entries)
        active_manifest["active_entries"] = next_entries

        try:
            self._save_active_manifest(game_backup_dir, active_manifest)
        except Exception as e:
            return {
                "success": False,
                "error_code": "active_manifest_save_failed",
                "error": str(e),
                "replaced": len(replaced_entries),
                "failed": len(failed_files),
                "failed_files": failed_files,
                "pending_manifest": str(self._pending_install_path(game_backup_dir)),
                "backup_dir": str(game_backup_dir),
            }

        for item in backup_plan:
            if item["target_rel"].lower() in replaced_keys:
                continue
            if item.get("previous_entry"):
                continue
            if item.get("backup_skipped"):
                continue
            backup_path = game_backup_dir / item["original_backup_rel"]
            self._cleanup_new_backups([backup_path], originals_dir)

        try:
            self._pending_install_path(game_backup_dir).unlink(missing_ok=True)
        except OSError:
            pass

        result = {
            "success": len(replaced_entries) > 0 and len(failed_files) == 0,
            "partial_success": len(replaced_entries) > 0 and len(failed_files) > 0,
            "replaced": len(replaced_entries),
            "failed": len(failed_files),
            "failed_files": failed_files,
            "skipped": preview.get("skipped_count", 0),
            "skipped_files": preview.get("skipped_files", []),
            "backup_dir": str(game_backup_dir),
        }
        self._write_operation_log(game_backup_dir, {"operation": "install", "mod_name": mod_name, **result})
        if progress_callback:
            progress_callback(100, "Sound 替换完成" if result["success"] else "Sound 替换失败")
        return result

    def get_status(self, game_root: str | Path) -> dict:
        game_root = Path(game_root)
        game_backup_dir = self._game_backup_dir(game_root)
        manifest = self._load_active_manifest(game_root, game_backup_dir)
        active_entries = manifest.get("active_entries", [])
        changed_files = []
        missing_files = []
        active_files = []
        sound_dir = game_root / "sound"

        for entry in active_entries:
            target_rel = str(entry.get("target_rel", ""))
            target_path = sound_dir / target_rel
            if not self._is_safe_sound_path(game_root, target_path):
                changed_files.append({"target_rel": target_rel, "reason": "unsafe_target_path"})
                continue
            if not target_path.is_file():
                missing_files.append({"target_rel": target_rel, "reason": "target_missing"})
                continue
            current_sha = self._sha256(target_path)
            if current_sha == entry.get("replacement_sha256"):
                active_files.append({"target_rel": target_rel, "mod_name": entry.get("mod_name", "")})
            else:
                changed_files.append({"target_rel": target_rel, "reason": "target_changed_externally"})

        active_mod_names = sorted({item.get("mod_name", "") for item in active_entries if item.get("mod_name")})
        backup_skipped_count = sum(1 for e in active_entries if e.get("backup_skipped"))
        return {
            "success": True,
            "backup_dir": str(game_backup_dir),
            "active_count": len(active_entries),
            "clean": len(active_entries) == 0,
            "active_files": active_files,
            "changed_count": len(changed_files),
            "changed_files": changed_files,
            "missing_count": len(missing_files),
            "missing_files": missing_files,
            "active_mod_names": active_mod_names,
            "backup_skipped_count": backup_skipped_count,
            "pending_manifest_exists": self._pending_install_path(game_backup_dir).is_file(),
        }

    def restore(self, game_root: str | Path, progress_callback: Callable[[int, str], None] | None = None) -> dict:
        game_root = Path(game_root)
        game_backup_dir = self._game_backup_dir(game_root)
        originals_dir = game_backup_dir / "originals"
        sound_dir = game_root / "sound"
        manifest = self._load_active_manifest(game_root, game_backup_dir)
        entries = list(manifest.get("active_entries", []))

        if not entries:
            return {
                "success": False,
                "restored": 0,
                "failed": 0,
                "skipped": 0,
                "skipped_files": [],
                "failed_files": [],
                "msg": "没有需要还原的 Sound 替换备份",
            }

        restored_entries = []
        skipped_files = []
        failed_files = []
        remaining_entries = []

        for idx, entry in enumerate(entries):
            target_rel = str(entry.get("target_rel", ""))

            if entry.get("backup_skipped"):
                skipped_files.append({"target_rel": target_rel, "reason": "backup_skipped"})
                remaining_entries.append(entry)
                if progress_callback:
                    progress = 10 + int((idx + 1) / len(entries) * 80)
                    progress_callback(progress, f"跳过: {Path(target_rel).name}")
                continue

            backup_rel = str(entry.get("original_backup_rel", ""))
            target_path = sound_dir / target_rel
            backup_path = game_backup_dir / backup_rel

            if not self._is_safe_sound_path(game_root, target_path):
                failed_files.append({"target_rel": target_rel, "reason": "unsafe_target_path"})
                remaining_entries.append(entry)
                continue
            if not self._is_safe_backup_path(game_backup_dir, backup_path):
                failed_files.append({"target_rel": target_rel, "reason": "unsafe_backup_path"})
                remaining_entries.append(entry)
                continue
            if not backup_path.is_file():
                skipped_files.append({"target_rel": target_rel, "reason": "backup_missing"})
                remaining_entries.append(entry)
                continue
            if not target_path.is_file():
                skipped_files.append({"target_rel": target_rel, "reason": "target_missing"})
                remaining_entries.append(entry)
                continue
            current_sha = self._sha256(target_path)
            if current_sha != entry.get("replacement_sha256"):
                original_sha = str(entry.get("original_sha256") or "")
                record_cleared = bool(original_sha and current_sha == original_sha)
                skipped_files.append({
                    "target_rel": target_rel,
                    "reason": "target_changed_externally",
                    "record_cleared": record_cleared,
                })
                if not record_cleared:
                    remaining_entries.append(entry)
                continue

            try:
                self._copy_file_atomic(backup_path, target_path)
                restored_entries.append(entry)
            except Exception as e:
                failed_files.append({"target_rel": target_rel, "reason": str(e)})
                remaining_entries.append(entry)

            if progress_callback:
                progress = 10 + int((idx + 1) / len(entries) * 80)
                progress_callback(progress, f"还原: {Path(target_rel).name}")

        manifest["active_entries"] = remaining_entries
        try:
            self._save_active_manifest(game_backup_dir, manifest)
        except Exception as e:
            return {
                "success": False,
                "restored": len(restored_entries),
                "failed": len(failed_files) + 1,
                "skipped": len(skipped_files),
                "failed_files": failed_files + [{"reason": f"active_manifest_save_failed: {e}"}],
                "skipped_files": skipped_files,
                "backup_dir": str(game_backup_dir),
            }

        for entry in restored_entries:
            backup_path = game_backup_dir / str(entry.get("original_backup_rel", ""))
            self._cleanup_new_backups([backup_path], originals_dir)

        cleared_records = sum(1 for item in skipped_files if item.get("record_cleared"))
        result = {
            "success": len(failed_files) == 0 and (len(restored_entries) > 0 or cleared_records > 0),
            "restored": len(restored_entries),
            "cleared_records": cleared_records,
            "failed": len(failed_files),
            "skipped": len(skipped_files),
            "failed_files": failed_files,
            "skipped_files": skipped_files,
            "backup_dir": str(game_backup_dir),
        }
        self._write_operation_log(game_backup_dir, {"operation": "restore", **result})
        if progress_callback:
            progress_callback(100, "Sound 还原完成" if result["success"] else "Sound 还原未完成")
        return result

    def clear_backup_skipped_entries(self, game_root: str | Path) -> dict:
        game_root = Path(game_root)
        game_backup_dir = self._game_backup_dir(game_root)
        manifest = self._load_active_manifest(game_root, game_backup_dir)
        entries = list(manifest.get("active_entries", []))
        kept_entries = [entry for entry in entries if not entry.get("backup_skipped")]
        cleared = len(entries) - len(kept_entries)
        manifest["active_entries"] = kept_entries
        self._save_active_manifest(game_backup_dir, manifest)
        result = {
            "success": True,
            "cleared": cleared,
            "remaining": len(kept_entries),
            "backup_dir": str(game_backup_dir),
        }
        self._write_operation_log(game_backup_dir, {"operation": "clear_backup_skipped_entries", **result})
        return result
