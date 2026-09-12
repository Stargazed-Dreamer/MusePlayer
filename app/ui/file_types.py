"""音频文件类型常量（UI 层共用）。"""

from __future__ import annotations

AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp3", ".flac", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".wma"}
)
AUDIO_FILE_FILTER: str = "音频文件 (*.mp3 *.flac *.wav *.m4a *.aac *.ogg *.opus *.wma)"
