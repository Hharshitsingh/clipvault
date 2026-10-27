"""Persistent clipboard history (JSON + optional image files)."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Iterator

from . import DATA_DIR_NAME, LEGACY_DATA_DIR_NAME, MAX_HISTORY


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _data_dir() -> Path:
    base = Path.home() / ".local" / "share" / DATA_DIR_NAME
    legacy = Path.home() / ".local" / "share" / LEGACY_DATA_DIR_NAME
    if not base.exists() and legacy.exists():
        try:
            shutil.move(str(legacy), str(base))
        except OSError:
            shutil.copytree(legacy, base, dirs_exist_ok=True)
    images = base / "images"
    base.mkdir(parents=True, exist_ok=True)
    images.mkdir(parents=True, exist_ok=True)
    return base


@dataclass
class ClipItem:
    id: str
    kind: str  # "text" | "image"
    text: str = ""
    image_path: str = ""
    pinned: bool = False
    created_at: float = field(default_factory=time.time)
    digest: str = ""  # sha256 of image bytes (images only)

    def preview(self, limit: int = 120) -> str:
        if self.kind == "image":
            return "Image"
        text = (self.text or "").replace("\n", " ").strip()
        if len(text) > limit:
            return text[: limit - 1] + "…"
        return text or "(empty)"


class HistoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.root = path or _data_dir()
        self.file = self.root / "history.json"
        self.images = self.root / "images"
        self.images.mkdir(parents=True, exist_ok=True)
        self._items: list[ClipItem] = []
        self.load()

    def load(self) -> None:
        if not self.file.exists():
            self._items = []
            return
        known = {f.name for f in fields(ClipItem)}
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
            items: list[ClipItem] = []
            for entry in raw:
                if not isinstance(entry, dict) or "id" not in entry:
                    continue
                item = ClipItem(**{k: v for k, v in entry.items() if k in known})
                # Drop image entries whose file vanished
                if item.kind == "image" and not Path(item.image_path).exists():
                    continue
                if item.kind == "image" and not item.digest:
                    item.digest = _sha256(Path(item.image_path).read_bytes())
                items.append(item)
            self._items = items
        except (json.JSONDecodeError, TypeError, KeyError, OSError):
            self._items = []

    def save(self) -> None:
        payload = [asdict(item) for item in self._items]
        tmp = self.file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.file)  # atomic — tray reader never sees a partial file

    def items(self) -> list[ClipItem]:
        pinned = [i for i in self._items if i.pinned]
        rest = [i for i in self._items if not i.pinned]
        return pinned + rest

    def get(self, item_id: str) -> ClipItem | None:
        for item in self._items:
            if item.id == item_id:
                return item
        return None

    def add_text(self, text: str) -> ClipItem | None:
        text = text or ""
        if not text.strip():
            return None
        # Deduplicate: move matching unpinned/pinned text to top
        for existing in list(self._items):
            if existing.kind == "text" and existing.text == text:
                if existing.pinned:
                    return existing
                self._items.remove(existing)
                existing.created_at = time.time()
                self._items.insert(0, existing)
                self._trim()
                self.save()
                return existing
        item = ClipItem(id=uuid.uuid4().hex, kind="text", text=text)
        self._items.insert(0, item)
        self._trim()
        self.save()
        return item

    def add_image(self, png_bytes: bytes) -> ClipItem | None:
        if not png_bytes:
            return None
        digest = _sha256(png_bytes)
        # Deduplicate identical images (e.g. re-copied from history)
        for existing in list(self._items):
            if existing.kind == "image" and existing.digest == digest:
                if existing.pinned:
                    return existing
                self._items.remove(existing)
                existing.created_at = time.time()
                self._items.insert(0, existing)
                self.save()
                return existing
        item_id = uuid.uuid4().hex
        path = self.images / f"{item_id}.png"
        path.write_bytes(png_bytes)
        item = ClipItem(
            id=item_id,
            kind="image",
            image_path=str(path),
            created_at=time.time(),
            digest=digest,
        )
        self._items.insert(0, item)
        self._trim()
        self.save()
        return item

    def toggle_pin(self, item_id: str) -> ClipItem | None:
        item = self.get(item_id)
        if not item:
            return None
        item.pinned = not item.pinned
        self.save()
        return item

    def remove(self, item_id: str) -> None:
        item = self.get(item_id)
        if not item:
            return
        self._items = [i for i in self._items if i.id != item_id]
        if item.kind == "image" and item.image_path:
            try:
                Path(item.image_path).unlink(missing_ok=True)
            except OSError:
                pass
        self.save()

    def clear_unpinned(self) -> None:
        keep: list[ClipItem] = []
        for item in self._items:
            if item.pinned:
                keep.append(item)
            elif item.kind == "image" and item.image_path:
                try:
                    Path(item.image_path).unlink(missing_ok=True)
                except OSError:
                    pass
        self._items = keep
        self.save()

    def search(self, query: str) -> list[ClipItem]:
        q = (query or "").strip().lower()
        if not q:
            return self.items()
        result: list[ClipItem] = []
        for item in self.items():
            hay = item.text.lower() if item.kind == "text" else "image"
            if q in hay:
                result.append(item)
        return result

    def _trim(self) -> None:
        pinned = [i for i in self._items if i.pinned]
        unpinned = [i for i in self._items if not i.pinned]
        overflow = unpinned[MAX_HISTORY:]
        for item in overflow:
            if item.kind == "image" and item.image_path:
                try:
                    Path(item.image_path).unlink(missing_ok=True)
                except OSError:
                    pass
        self._items = pinned + unpinned[:MAX_HISTORY]

    def wipe_all(self) -> None:
        if self.images.exists():
            shutil.rmtree(self.images, ignore_errors=True)
        self.images.mkdir(parents=True, exist_ok=True)
        self._items = []
        self.save()

    def __iter__(self) -> Iterator[ClipItem]:
        return iter(self.items())
