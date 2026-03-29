from __future__ import annotations

from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile

from app.models.entities import Track, new_id


class MetadataService:
    def __init__(self):
        self._lyrics_cache: dict[str, str] = {}
        self._cover_cache: dict[str, bytes | None] = {}

    def extract_track(self, path: Path, track_id: str | None = None) -> Track:
        source = Path(path).resolve()
        audio_easy = None
        audio_raw = None
        duration_sec = 0.0
        title = source.stem
        artist = "未知歌手"
        album = "未知专辑"
        track_no = 0
        year = ""

        try:
            audio_easy = MutagenFile(str(source), easy=True)
        except Exception:
            audio_easy = None

        try:
            audio_raw = MutagenFile(str(source), easy=False)
        except Exception:
            audio_raw = None

        if audio_raw is not None and getattr(audio_raw, "info", None) is not None:
            duration_sec = float(getattr(audio_raw.info, "length", 0.0) or 0.0)

        if audio_easy is not None and getattr(audio_easy, "tags", None):
            tags = audio_easy.tags
            title = self._first_tag(tags, ["title", "TITLE"]) or title
            artist = self._first_tag(tags, ["artist", "ARTIST"]) or artist
            album = self._first_tag(tags, ["album", "ALBUM"]) or album
            track_text = self._first_tag(tags, ["tracknumber", "TRACKNUMBER"]) or "0"
            year = self._first_tag(tags, ["date", "DATE", "year", "YEAR"]) or ""
            track_no = self._parse_track_no(track_text)

        return Track(
            id=track_id or new_id(),
            path=str(source),
            title=title,
            artist=artist,
            album=album,
            duration_sec=duration_sec,
            track_no=track_no,
            year=year,
        )

    def read_lyrics(self, path: Path) -> str:
        source = str(Path(path).resolve())
        if source in self._lyrics_cache:
            return self._lyrics_cache[source]

        lyrics = self._read_lrc_sidecar(Path(source))
        if not lyrics:
            lyrics = self._read_tag_lyrics(Path(source))

        lyrics = lyrics.strip()
        self._lyrics_cache[source] = lyrics
        return lyrics

    def read_cover_bytes(self, path: Path) -> bytes | None:
        source = str(Path(path).resolve())
        if source in self._cover_cache:
            return self._cover_cache[source]

        data = self._read_embedded_cover(Path(source))
        self._cover_cache[source] = data
        return data

    def _read_lrc_sidecar(self, path: Path) -> str:
        lrc = path.with_suffix(".lrc")
        if not lrc.exists():
            return ""
        try:
            return lrc.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                return lrc.read_text(encoding="gbk")
            except Exception:
                return ""
        except Exception:
            return ""

    def _read_tag_lyrics(self, path: Path) -> str:
        try:
            audio = MutagenFile(str(path), easy=False)
        except Exception:
            return ""
        if audio is None or getattr(audio, "tags", None) is None:
            return ""

        tags = audio.tags

        # ID3
        if hasattr(tags, "getall"):
            try:
                uslt_list = tags.getall("USLT")
                if uslt_list:
                    return str(getattr(uslt_list[0], "text", ""))
            except Exception:
                pass

        # Vorbis/FLAC
        for key in ("lyrics", "LYRICS", "unsyncedlyrics"):
            value = self._first_tag(tags, [key])
            if value:
                return value

        return ""

    def _read_embedded_cover(self, path: Path) -> bytes | None:
        try:
            audio = MutagenFile(str(path), easy=False)
        except Exception:
            return None
        if audio is None or getattr(audio, "tags", None) is None:
            return None

        tags = audio.tags

        # MP3 ID3 APIC
        if hasattr(tags, "getall"):
            try:
                apic = tags.getall("APIC")
                if apic:
                    return bytes(apic[0].data)
            except Exception:
                pass

        # FLAC pictures
        pictures = getattr(audio, "pictures", None)
        if pictures:
            try:
                return bytes(pictures[0].data)
            except Exception:
                pass

        # MP4 covr
        try:
            covr = tags.get("covr")
            if covr:
                first = covr[0]
                return bytes(first)
        except Exception:
            pass

        return None

    @staticmethod
    def _first_tag(tags: Any, candidates: list[str]) -> str:
        for key in candidates:
            try:
                value = tags.get(key)
            except Exception:
                continue
            if not value:
                continue
            if isinstance(value, list):
                if not value:
                    continue
                value = value[0]
            return str(value).strip()
        return ""

    @staticmethod
    def _parse_track_no(raw: str) -> int:
        text = (raw or "0").strip()
        if "/" in text:
            text = text.split("/", 1)[0]
        try:
            return max(0, int(text))
        except Exception:
            return 0
