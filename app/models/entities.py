from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import time
import uuid


def _now_ts() -> float:
    """获取当前时间戳。
    
    Returns:
        float: 当前时间的时间戳（秒）
    """
    return time.time()


def new_id() -> str:
    """生成新的唯一ID。
    
    Returns:
        str: 32字符的十六进制UUID字符串
    """
    return uuid.uuid4().hex


@dataclass(slots=True)
class Track:
    """音乐曲目实体类。
    
    表示单个音乐文件的所有元数据和状态信息。
    使用slots=True优化内存使用。
    """
    id: str
    """曲目唯一标识符"""
    path: str
    """音频文件路径"""
    title: str
    """曲目标题"""
    artist: str = "未知歌手"
    """艺术家名称，默认为'未知歌手'"""
    album: str = "未知专辑"
    """专辑名称，默认为'未知专辑'"""
    duration_sec: float = 0.0
    """曲目时长（秒），0.0表示未知"""
    track_no: int = 0
    """音轨号，0表示未知"""
    year: str = ""
    """发行年份"""
    added_at: float = field(default_factory=_now_ts)
    """添加到库的时间戳"""
    source_track_id: str = ""
    """源系统中的曲目ID（用于导入）"""
    source_storage_relpath: str = ""
    """源存储中的相对路径"""
    source_lyrics_storage_relpath: str = ""
    """源歌词文件的相对路径"""
    source_lyrics_path: str = ""
    """歌词文件绝对路径"""
    source_sha256: str = ""
    """源文件的SHA256哈希值"""

    @property
    def path_obj(self) -> Path:
        """获取路径对象。
        
        Returns:
            Path: 音频文件的路径对象
        """
        return Path(self.path)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式，用于序列化。
        
        Returns:
            dict[str, Any]: 包含所有属性的字典
        """
        return {
            "id": self.id,
            "path": self.path,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration_sec": float(self.duration_sec),
            "track_no": int(self.track_no),
            "year": self.year,
            "added_at": float(self.added_at),
            "source_track_id": self.source_track_id,
            "source_storage_relpath": self.source_storage_relpath,
            "source_lyrics_storage_relpath": self.source_lyrics_storage_relpath,
            "source_lyrics_path": self.source_lyrics_path,
            "source_sha256": self.source_sha256,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Track":
        """从字典创建Track实例，用于反序列化。
        
        Args:
            data: 包含Track数据的字典
            
        Returns:
            Track: 新创建的Track实例
        """
        return cls(
            id=str(data.get("id", new_id())),
            path=str(data.get("path", "")),
            title=str(data.get("title", "未知标题")),
            artist=str(data.get("artist", "未知歌手")),
            album=str(data.get("album", "未知专辑")),
            duration_sec=float(data.get("duration_sec", 0.0)),
            track_no=int(data.get("track_no", 0)),
            year=str(data.get("year", "")),
            added_at=float(data.get("added_at", _now_ts())),
            source_track_id=str(data.get("source_track_id", "")),
            source_storage_relpath=str(data.get("source_storage_relpath", "")),
            source_lyrics_storage_relpath=str(data.get("source_lyrics_storage_relpath", "")),
            source_lyrics_path=str(data.get("source_lyrics_path", "")),
            source_sha256=str(data.get("source_sha256", "")),
        )


@dataclass(slots=True)
class Playlist:
    """播放列表实体类。
    
    表示一组音乐曲目的集合，包含播放列表的元数据和来源信息。
    使用slots=True优化内存使用。
    """
    id: str
    """播放列表唯一标识符"""
    name: str
    """播放列表名称"""
    track_ids: list[str] = field(default_factory=list)
    """包含的曲目ID列表"""
    created_at: float = field(default_factory=_now_ts)
    """创建时间戳"""
    updated_at: float = field(default_factory=_now_ts)
    """最后更新时间戳"""
    source_schema: str = ""
    """源播放列表架构版本"""
    source_file: str = ""
    """源播放列表文件路径"""
    source_playlist_hash: str = ""
    """源播放列表的哈希值"""
    source_database_location: str = ""
    """源数据库位置"""
    source_exported_at: str = ""
    """源播放列表导出时间"""

    def touch(self) -> None:
        """更新播放列表的修改时间戳。
        
        用于标记播放列表内容或元数据的更改。
        """
        self.updated_at = _now_ts()

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式，用于序列化。
        
        Returns:
            dict[str, Any]: 包含所有属性的字典
        """
        return {
            "id": self.id,
            "name": self.name,
            "track_ids": list(self.track_ids),
            "created_at": float(self.created_at),
            "updated_at": float(self.updated_at),
            "source_schema": self.source_schema,
            "source_file": self.source_file,
            "source_playlist_hash": self.source_playlist_hash,
            "source_database_location": self.source_database_location,
            "source_exported_at": self.source_exported_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Playlist":
        """从字典创建Playlist实例，用于反序列化。
        
        Args:
            data: 包含Playlist数据的字典
            
        Returns:
            Playlist: 新创建的Playlist实例
        """
        return cls(
            id=str(data.get("id", new_id())),
            name=str(data.get("name", "新建歌单")),
            track_ids=[str(x) for x in data.get("track_ids", [])],
            created_at=float(data.get("created_at", _now_ts())),
            updated_at=float(data.get("updated_at", _now_ts())),
            source_schema=str(data.get("source_schema", "")),
            source_file=str(data.get("source_file", "")),
            source_playlist_hash=str(data.get("source_playlist_hash", "")),
            source_database_location=str(data.get("source_database_location", "")),
            source_exported_at=str(data.get("source_exported_at", "")),
        )


@dataclass(slots=True)
class Settings:
    """应用设置实体类。
    
    包含所有可配置的应用程序和播放器设置。
    使用slots=True优化内存使用。
    """
    control_host: str = "127.0.0.1"
    """控制接口监听主机地址"""
    control_port: int = 43121
    """控制接口监听端口"""
    control_interface_enabled: bool = False
    """是否启用控制接口"""
    auto_restore_session: bool = True
    """是否自动恢复上次会话"""
    logging_enabled: bool = False
    """是否启用日志记录"""
    crash_logging_enabled: bool = True
    """是否记录崩溃日志"""
    data_maintenance_logging_enabled: bool = True
    """是否记录数据维护日志"""
    enable_single_loop_mode: bool = True
    """是否启用单曲循环模式"""
    enable_playlist_loop_mode: bool = False
    """是否启用歌单循环模式"""
    collect_playback_data: bool = True
    """是否收集播放统计信息"""
    global_gain_boost: float = 1.0
    """全局音量增益（0.5-5.0）"""
    read_strategy: str = "window"
    """读取策略：'window'或'full'"""
    output_device: str = ""
    """输出硬件设备名称，空字符串表示跟随系统"""
    timed_save_enabled: bool = False
    """是否启用定时保存"""
    timed_save_minutes: int = 5
    """定时保存间隔（分钟）"""
    dark_theme: bool = True
    """是否使用深色主题"""
    remember_window_geometry: bool = True
    """是否记住窗口几何形状"""
    window_x: int = -1
    """窗口X坐标，-1表示未设置"""
    window_y: int = -1
    """窗口Y坐标，-1表示未设置"""
    window_width: int = 0
    """窗口宽度，0表示默认"""
    window_height: int = 0
    """窗口高度，0表示默认"""
    max_window_width: int = 0
    """最大窗口宽度，0表示不限制"""
    max_window_height: int = 0
    """最大窗口高度，0表示不限制"""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式，用于序列化。
        
        确保所有数值类型正确转换。
        
        Returns:
            dict[str, Any]: 包含所有设置项的字典
        """
        return {
            "control_host": self.control_host,
            "control_port": int(self.control_port),
            "control_interface_enabled": bool(self.control_interface_enabled),
            "auto_restore_session": bool(self.auto_restore_session),
            "logging_enabled": bool(self.logging_enabled),
            "crash_logging_enabled": bool(self.crash_logging_enabled),
            "data_maintenance_logging_enabled": bool(self.data_maintenance_logging_enabled),
            "enable_single_loop_mode": bool(self.enable_single_loop_mode),
            "enable_playlist_loop_mode": bool(self.enable_playlist_loop_mode),
            "collect_playback_data": bool(self.collect_playback_data),
            "global_gain_boost": float(self.global_gain_boost),
            "read_strategy": self.read_strategy,
            "output_device": self.output_device,
            "timed_save_enabled": bool(self.timed_save_enabled),
            "timed_save_minutes": int(self.timed_save_minutes),
            "dark_theme": bool(self.dark_theme),
            "remember_window_geometry": bool(self.remember_window_geometry),
            "window_x": int(self.window_x),
            "window_y": int(self.window_y),
            "window_width": int(self.window_width),
            "window_height": int(self.window_height),
            "max_window_width": int(self.max_window_width),
            "max_window_height": int(self.max_window_height),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        """从字典创建Settings实例，用于反序列化。
        
        对输入数据进行验证和范围限制，确保设置的合法性。
        
        Args:
            data: 包含设置数据的字典
            
        Returns:
            Settings: 新创建的Settings实例
        """
        # 验证读取策略
        read_strategy = str(data.get("read_strategy", "window")).strip().lower()
        if read_strategy not in {"window", "full"}:
            read_strategy = "window"
        max_window_width = max(0, int(data.get("max_window_width", 0)))
        max_window_height = max(0, int(data.get("max_window_height", 0)))
        if 0 < max_window_width < 600:
            max_window_width = 600
        if 0 < max_window_height < 800:
            max_window_height = 800
        
        return cls(
            control_host=str(data.get("control_host", "127.0.0.1")),
            # 限制端口范围在有效范围内
            control_port=max(1, min(65535, int(data.get("control_port", 43121)))),
            control_interface_enabled=bool(data.get("control_interface_enabled", False)),
            auto_restore_session=bool(data.get("auto_restore_session", True)),
            logging_enabled=bool(data.get("logging_enabled", False)),
            crash_logging_enabled=bool(data.get("crash_logging_enabled", True)),
            data_maintenance_logging_enabled=bool(data.get("data_maintenance_logging_enabled", True)),
            enable_single_loop_mode=bool(data.get("enable_single_loop_mode", True)),
            enable_playlist_loop_mode=bool(data.get("enable_playlist_loop_mode", False)),
            collect_playback_data=bool(data.get("collect_playback_data", True)),
            # 限制音量增益在合理范围内
            global_gain_boost=max(0.5, min(5.0, float(data.get("global_gain_boost", 1.0)))),
            read_strategy=read_strategy,
            output_device=str(data.get("output_device", "")).strip(),
            timed_save_enabled=bool(data.get("timed_save_enabled", False)),
            # 限制保存间隔在1分钟到24小时内
            timed_save_minutes=max(1, min(1440, int(data.get("timed_save_minutes", 5)))),
            dark_theme=bool(data.get("dark_theme", True)),
            remember_window_geometry=bool(data.get("remember_window_geometry", True)),
            window_x=int(data.get("window_x", -1)),
            window_y=int(data.get("window_y", -1)),
            # 确保窗口尺寸不为负数
            window_width=max(0, int(data.get("window_width", 0))),
            window_height=max(0, int(data.get("window_height", 0))),
            max_window_width=max_window_width,
            max_window_height=max_window_height,
        )


@dataclass(slots=True)
class SessionState:
    """会话状态实体类。
    
    保存播放器的当前状态，用于会话恢复和状态同步。
    使用slots=True优化内存使用。
    """
    current_playlist_id: str | None = None
    """当前播放列表ID，None表示未设置"""
    current_track_id: str | None = None
    """当前播放的曲目ID，None表示未设置"""
    position_sec: float = 0.0
    """当前播放位置（秒）"""
    volume: float = 1.0
    """当前音量（0.0-1.0）"""
    play_mode: str = "single_loop"
    """播放模式：'single_loop', 'playlist_loop', 'random'"""
    random_seed: int = 1
    """随机播放的种子"""
    random_index: int = 0
    """随机播放的索引位置"""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式，用于序列化。
        
        Returns:
            dict[str, Any]: 包含当前会话状态的字典
        """
        return {
            "current_playlist_id": self.current_playlist_id,
            "current_track_id": self.current_track_id,
            "position_sec": float(self.position_sec),
            "volume": float(self.volume),
            "play_mode": self.play_mode,
            "random_seed": int(self.random_seed),
            "random_index": int(self.random_index),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionState":
        """从字典创建SessionState实例，用于反序列化。
        
        对输入数据进行验证和范围限制，确保会话状态的合法性。
        
        Args:
            data: 包含会话状态数据的字典
            
        Returns:
            SessionState: 新创建的SessionState实例
        """
        return cls(
            current_playlist_id=data.get("current_playlist_id"),
            current_track_id=data.get("current_track_id"),
            # 确保播放位置不为负数
            position_sec=max(0.0, float(data.get("position_sec", 0.0))),
            # 限制音量在0-1范围内
            volume=max(0.0, min(1.0, float(data.get("volume", 1.0)))),
            play_mode=str(data.get("play_mode", "single_loop")),
            # 确保随机种子和索引不为负数
            random_seed=max(0, int(data.get("random_seed", 1))),
            random_index=max(0, int(data.get("random_index", 0))),
        )
