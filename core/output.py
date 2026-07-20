from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Any


StreamCallback = Callable[[Any, int, Any, Any], None]


class AudioOutputBackend(ABC):
    """Platform output backend abstraction."""

    @abstractmethod
    def open(
        self,
        sample_rate: int,
        channels: int,
        callback: StreamCallback,
        blocksize: int = 1024,
    ) -> None:
        """Open output stream."""

    @abstractmethod
    def start(self) -> None:
        """Start output stream."""

    @abstractmethod
    def stop(self) -> None:
        """Stop output stream."""

    @abstractmethod
    def close(self) -> None:
        """Close and release output stream."""


class NullOutputBackend(AudioOutputBackend):
    """No-op backend used when no audio device backend is available."""

    def open(
        self,
        sample_rate: int,
        channels: int,
        callback: StreamCallback,
        blocksize: int = 1024,
    ) -> None:
        return None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def close(self) -> None:
        return None


class SoundDeviceOutputBackend(AudioOutputBackend):
    """sounddevice-based realtime output backend."""

    def __init__(self, latency: str | float = "low", device: str | int | None = None):
        """
        初始化类的实例。

        参数:
            latency (str | float): 延迟设置，可以是字符串或浮点数。默认为 "low"。
            device (str | int | None): 指定设备，可以是字符串、整数或 None。默认为 None。

        返回值:
            None
        """
        self._latency = latency  # 存储延迟设置
        self._device = device  # 存储设备信息
        self._stream = None  # 初始化流对象为空

    def open(
        self,
        sample_rate: int,
        channels: int,
        callback: StreamCallback,
        blocksize: int = 1024,
    ) -> None:
        """打开音频输出流并开始播放。
    
        此方法用于创建并启动一个音频输出流。它会使用指定的参数配置流，并通过回调函数处理音频数据。
    
        参数:
            self: 对象实例。
            sample_rate (int): 采样率，单位为赫兹 (Hz)。
            channels (int): 声道数（例如 1 为单声道，2 为立体声）。
            callback (StreamCallback): 当流需要数据时调用的回调函数。
            blocksize (int, optional): 回调处理的块大小（样本数）。默认为 1024。
        
        返回:
            None: 此方法不返回任何值。
        """
        # 导入sounddevice库并简称为sd
        import sounddevice as sd
    
        # 首先关闭任何已存在的流
        self.close()
    
        # 创建一个包含所有流配置参数的字典
        kwargs = dict(
            samplerate=sample_rate,  # 设置采样率
            channels=channels,  # 设置声道数
            dtype="float32",  # 设置数据类型为32位浮点数
            callback=callback,  # 设置回调函数
            blocksize=blocksize,  # 设置处理的块大小
            latency=self._latency,  # 设置期望的延迟
        )
    
        # 如果指定了设备，则进行处理
        if self._device is not None:
            # 如果设备标识符是字符串（如设备名称），则尝试解析为设备索引
            if isinstance(self._device, str):
                resolved = resolve_output_device_index(self._device)
                # 将解析到的设备索引（如果有效）或原始字符串赋值给参数
                kwargs["device"] = resolved if resolved is not None else self._device
            else:
                # 如果设备标识符已经是数字索引，直接使用
                kwargs["device"] = self._device
    
        # 使用配置参数创建并打开一个新的输出流
        self._stream = sd.OutputStream(**kwargs)

    def start(self) -> None:
        """启动流对象。

        当内部流对象存在且未处于激活状态时，调用其 start 方法。
        参数:
            无。
        返回:
            None。
        """
        # 检查内部流对象是否存在且未激活
        if self._stream is not None and not self._stream.active:
            # 启动流
            self._stream.start()

    def stop(self) -> None:
        if self._stream is not None and self._stream.active:
            self._stream.stop()

    def close(self) -> None:
        if self._stream is None:
            return
        try:
            if self._stream.active:
                self._stream.stop()
        except Exception:
            pass
        try:
            self._stream.close()
        except Exception:
            pass
        finally:
            self._stream = None


def list_output_devices() -> list[dict[str, Any]]:
    """列出所有可用的音频输出设备，优先使用WASAPI主机API的设备索引。

    参数: 无。
    返回值: 包含设备索引和名称的字典列表，每个字典格式为 {"index": int, "name": str}。
    """
    try:
        import sounddevice as sd  # 尝试导入 sounddevice 库
    except Exception:
        return []  # 导入失败时返回空列表
    devices = sd.query_devices()  # 查询所有音频设备
    if isinstance(devices, dict):
        return []  # 如果查询结果是字典（可能表示错误），则返回空列表
    seen_names: set[str] = set()  # 用于记录已处理的设备名称，避免重复
    result: list[dict[str, Any]] = []  # 存储最终结果列表
    wasapi_entries: dict[str, int] = {}  # 存储WASAPI主机API的设备名称和对应索引
    for idx, dev in enumerate(devices):
        if not isinstance(dev, dict):
            continue  # 跳过非字典类型的设备条目
        if dev.get("max_output_channels", 0) <= 0:
            continue  # 过滤掉没有输出通道的设备
        name = str(dev.get("name", "")).strip()
        if not name:
            continue  # 跳过名称为空或仅空白的设备
        hostapi = int(dev.get("hostapi", -1))
        if hostapi == 2 and name not in wasapi_entries:
            wasapi_entries[name] = idx  # 如果设备使用WASAPI主机API（索引2），则记录其名称和索引
    for idx, dev in enumerate(devices):
        if not isinstance(dev, dict):
            continue  # 跳过非字典类型的设备条目
        if dev.get("max_output_channels", 0) <= 0:
            continue  # 过滤掉没有输出通道的设备
        name = str(dev.get("name", "")).strip()
        if not name or name in seen_names:
            continue  # 跳过空名称或已处理过的设备名称
        seen_names.add(name)  # 将设备名称添加到已处理集合中
        chosen_idx = wasapi_entries.get(name, idx)  # 优先使用WASAPI索引，否则使用当前索引
        result.append({"index": chosen_idx, "name": name})  # 将设备信息添加到结果列表
    return result  # 返回设备列表


def resolve_output_device_index(device_name: str) -> int | None:
    """根据设备名称解析并返回对应的音频输出设备索引。
    
    功能：在系统音频设备列表中查找指定名称的输出设备，并优先返回使用 WASAPI 主机 API 的设备索引。
    参数：
        device_name (str): 要查找的音频输出设备名称。
    返回值：
        int: 如果找到匹配的设备，返回其索引。
        None: 如果设备名称为空、sounddevice 导入失败、无可用设备，或未找到匹配的设备。
    """
    if not device_name:
        return None  # 设备名称为空，直接返回 None
    try:
        import sounddevice as sd  # 尝试导入 sounddevice 库
    except Exception:
        return None  # 导入失败（如未安装），返回 None
    devices = sd.query_devices()  # 查询所有音频设备
    if isinstance(devices, dict):
        return None  # 旧版 API 可能返回字典，此处处理兼容性问题
    wasapi_idx: int | None = None  # 用于存储匹配且使用 WASAPI 的设备索引
    first_idx: int | None = None  # 用于存储第一个匹配的设备索引
    for idx, dev in enumerate(devices):
        if not isinstance(dev, dict):
            continue  # 跳过非字典格式的设备项
        if dev.get("max_output_channels", 0) <= 0:
            continue  # 跳过无输出通道的设备（非输出设备）
        name = str(dev.get("name", "")).strip()  # 获取设备名称并去除首尾空白
        if name != device_name:
            continue  # 名称不匹配，跳过当前设备
        if first_idx is None:
            first_idx = idx  # 记录第一个匹配设备的索引
        hostapi = int(dev.get("hostapi", -1))  # 获取主机 API 标识
        if hostapi == 2:  # 2 通常代表 Windows WASAPI 主机 API
            wasapi_idx = idx
            break  # 找到 WASAPI 设备后立即跳出循环
    return wasapi_idx if wasapi_idx is not None else first_idx  # 优先返回 WASAPI 设备索引，否则返回第一个匹配的索引
