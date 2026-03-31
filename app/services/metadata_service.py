from __future__ import annotations

from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile

from app.models.entities import Track, new_id


class MetadataService:
    """音频文件元数据服务。
    
    负责提取音频文件的标签信息、歌词和封面图片。
    使用Mutagen库处理多种音频格式（MP3、FLAC、M4A、OGG等）。
    """
    def __init__(self):
        """初始化元数据服务。
        
        创建用于缓存歌词和封面数据的内部存储。
        """
        self._lyrics_cache: dict[str, str] = {}
        self._cover_cache: dict[str, bytes | None] = {}

    def extract_track(self, path: Path, track_id: str | None = None) -> Track:
        """从音频文件提取完整的曲目信息。
        
        使用Mutagen库读取音频文件的标签和基本信息，
        创建一个Track实例表示该音频文件。
        
        Args:
            path: 音频文件路径
            track_id: 可选的曲目ID，如果未提供则生成新的
            
        Returns:
            Track: 包含提取的元数据的曲目对象
        """
        source = Path(path).resolve()
        audio_easy = None
        audio_raw = None
        duration_sec = 0.0
        title = source.stem  # 默认使用文件名作为标题
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

        # 提取音频时长信息
        if audio_raw is not None and getattr(audio_raw, "info", None) is not None:
            duration_sec = float(getattr(audio_raw.info, "length", 0.0) or 0.0)

        # 提取标签信息
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
        """读取音频文件的歌词信息。
        
        优先读取同名的.lrc文件，如果不存在则尝试从
        音频文件标签中读取嵌入的歌词。
        结果会被缓存以提高性能。
        
        Args:
            path: 音频文件路径
            
        Returns:
            str: 歌词文本，如果没有歌词则返回空字符串
        """
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
        """读取音频文件内嵌的封面图片。
        
        从多种音频格式中提取嵌入的封面图片数据。
        结果会被缓存以提高性能。
        
        Args:
            path: 音频文件路径
            
        Returns:
            bytes | None: 封面图片数据，如果没有封面则返回None
        """
        source = str(Path(path).resolve())
        if source in self._cover_cache:
            return self._cover_cache[source]

        data = self._read_embedded_cover(Path(source))
        self._cover_cache[source] = data
        return data

    def _read_lrc_sidecar(self, path: Path) -> str:
        """读取同名的.lrc歌词文件。
        
        Args:
            path: 音频文件路径
            
        Returns:
            str: 歌词文本，如果文件不存在或读取失败则返回空字符串
        """
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
        """从音频文件标签中读取嵌入的歌词。
        
        支持多种音频格式的歌词标签：
        - ID3格式的USLT标签（MP3）
        - Vorbis/FLAC格式的lyrics标签
        
        Args:
            path: 音频文件路径
            
        Returns:
            str: 歌词文本，如果没有找到则返回空字符串
        """
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
        """从音频文件标签中读取嵌入的封面图片。
        
        支持多种音频格式的封面：
        - ID3 APIC标签（MP3）
        - FLAC pictures
        - MP4 covr原子
        
        Args:
            path: 音频文件路径
            
        Returns:
            bytes | None: 封面图片数据，如果没有则返回None
        """
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
        """从标签中按优先级获取第一个存在的值。
        
        Args:
            tags: 标签对象
            candidates: 可能的标签键名列表，按优先级排序
            
        Returns:
            str: 找到的标签值，如果没有则返回空字符串
        """
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
        """解析音轨号字符串。
        
        支持多种格式的音轨号：
        - 纯数字："5"
        - 分数形式："3/10"
        
        Args:
            raw: 音轨号字符串
            
        Returns:
            int: 解析后的音轨号，解析失败返回0
        """
        text = (raw or "0").strip()
        if "/" in text:
            text = text.split("/", 1)[0]
        try:
            return max(0, int(text))
        except Exception:
            return 0
