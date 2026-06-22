---
name: _index
description: >
  Skill 索引入口。Agent 接到任务时，先查阅此文件确定应该使用哪个 Skill。
---

# Skill 索引

> `.agents/skills/` 是项目唯一的 Skill 定义目录。
> 每个 Skill 文件顶部有 YAML frontmatter，`name` 是标识符，`description` 包含触发词。
> 本文件（`_index.md`）是唯一索引。每个新 Skill 必须在此注册。

## Skill 列表

| Skill | 触发词 | 依赖 | 输出 |
|-------|--------|------|------|
| **player-control** | 播放、暂停、下一首、上一首、切歌、调音量、跳转、播放模式、控制播放器 | 运行中的 MusePlayer 实例 | 播放状态 JSON |
| **library-manage** | 导入、导入文件夹、导入歌单、新建歌单、删除歌单、曲库管理、歌单管理 | 运行中的 MusePlayer 实例 | 操作结果 JSON |
| **skill-creator** | 创建skill、新建skill、写个skill、优化skill | 无 | 新 Skill 文件 |
| **neat-freak** | 同步一下、整理文档、整理一下、收尾、/sync | 无 | 更新后的文档 |

## 使用规则

1. Agent 接到任务时，先扫描上方表格的"触发词"列
2. 匹配到 Skill → 读取对应 `.md` 文件，按工作流执行
3. 未匹配 → 询问用户是否需要创建新 Skill
4. 每个新 Skill 创建后，必须在此表格中注册一行
