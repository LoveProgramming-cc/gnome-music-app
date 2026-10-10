# 音乐（Music）

一个 GNOME 原生的音乐播放器：GTK 4 + libadwaita + GStreamer，用 Python（PyGObject）编写。
字幕的解析、管理和显示由程序自己实现，不依赖播放框架。

## 运行

```bash
./music
```

加到 GNOME 应用列表（仅当前用户；同时安装图标和“活动”概览里的搜索）：

```bash
./install.sh
```

移除用 `./uninstall.sh`。程序始终从这个文件夹运行，文件夹挪了位置就再执行一次 `install.sh`。

界面语言跟随系统（English / 简体中文 / 日本語 / 한국어 / العربية）。临时换一种：`MUSIC_LANG=zh ./music`。

## 依赖

Ubuntu / Debian 上的包名：

| 用途 | 包 |
| --- | --- |
| 界面 | `python3-gi` `gir1.2-gtk-4.0` `gir1.2-adw-1`（libadwaita ≥ 1.8） |
| 播放、读取标签 | `gstreamer1.0-plugins-base` `gstreamer1.0-plugins-good` `gir1.2-gst-plugins-base-1.0` |
| 视频画面 | `gstreamer1.0-gtk4` |
| AAC / M4A、H.264 / MP4 等 | `gstreamer1.0-libav`（没装时这些格式能导入但不能播放） |

## 功能

- **资料库**：导入歌曲和视频（按钮、菜单、拖进窗口、拖进文件夹），自动读取标签、封面、时长；
  按最近添加、艺人、专辑、歌曲、喜爱浏览；搜索；按标题 / 艺人 / 专辑 / 时长 / 添加日期 / 播放次数排序。
- **歌单与歌单文件夹**：新建、重命名（标题可直接点击编辑）、自定义封面（点击或拖入图片）、
  拖动排序、把歌曲拖到侧边栏的歌单上、把歌单拖进 / 拖出文件夹。
- **播放**：随机、循环（全部 / 单曲）、待播清单（插播、最后播放、拖动排序、移除）、播放历史、
  暂停 / 继续淡入淡出、0–12 秒交叉淡化。
- **正在播放**：全窗口视图，封面 + 待播清单 / 播放历史；视频占满窗口，移动鼠标时显示控制条，双击或 F11 全屏。
- **字幕**：视频内嵌的字幕轨和外挂字幕文件（SRT、WebVTT、ASS / SSA、LRC）统一由程序解析并绘制在画面上；
  每个视频记住所选字幕；导入视频时，同名字幕文件会一并带入。字幕大小可调，位置可选自动（上方 / 下方）或直接在画面上拖动。
- **导入 / 导出**：歌单导出为 `.zip`（`playlist.json` + 封面 + 歌曲），可再导入；单曲导出。
  整个资料库也可以导出为一个 `.zip`（主菜单或 设置 → 存储），用于备份或迁移到另一台电脑；导入时只添加还没有的歌曲和歌单，重复导入不会产生重复项。
- **设置**：外观（跟随系统 / 浅色 / 深色）、强调色、播放、默认封面、储存空间管理
  （按大小 / 从未播放 / 3 个月未播放 / 不在歌单中筛选并删除，清理未使用的文件）。
- **桌面集成**：顶栏面板（`shell-extension/` 里的 GNOME Shell 扩展，由 `install.sh` 安装：封面、歌名、播放控制、进度条；没启用扩展时退回为 AppIndicator 图标 + 菜单；设置里可关闭）、MPRIS（点顶栏时钟弹出的媒体控制、锁屏、媒体键）、GNOME Shell 搜索歌曲和歌单、
  `music://song/<id>` 与 `music://playlist/<id>` 链接、“用音乐打开”。


## 快捷键

| | |
| --- | --- |
| 播放 / 暂停 | 空格 |
| 下一首 / 上一首 | Alt+→ / Alt+← |
| 随机 / 循环 | Ctrl+S / Ctrl+R |
| 正在播放 / 待播清单 | Ctrl+L / Ctrl+U |
| 导入歌曲 | Ctrl+O |
| 新建歌单 / 歌单文件夹 | Ctrl+N / Ctrl+Shift+N |
| 导出歌单 | Ctrl+E |
| 搜索 | Ctrl+F |
| 从歌单中移除所选 | Delete |
| 设置 / 全部快捷键 | Ctrl+, / Ctrl+? |

## 数据存放

导入的文件会**复制**进资料库：`~/.local/share/local.koen.Music/`（`library.json` + `files/`）。
位置可以在 设置 → 存储 → 资料库位置 里更改：现有资料库会整体迁移过去，旧位置随后清空。所选位置记在 `~/.config/local.koen.Music/config.json`。

## 代码结构

```
music                 启动脚本
musicapp/
  app.py              Adw.Application：启动、快捷键、强调色、外观
  core.py             状态、资料库操作、播放队列逻辑、各处菜单
  store.py            文件仓库与 library.json
  importer.py         后台线程：复制文件、读标签和封面、zip 导入导出
  audio.py            两个 GStreamer playbin（交叉淡化）、淡入淡出、内嵌字幕文本输出
  subtitles.py        字幕解析器（SRT / VTT / ASS / LRC）与字幕轨管理
  window.py           主窗口：侧边栏 | 内容 | 待播清单，“正在播放”覆盖其上
  sidebar.py content.py playerbar.py nowplaying.py dialogs.py widgets.py art.py
  mpris.py            MPRIS 媒体控制
  tray.py             顶栏图标及其菜单
  search.py           GNOME Shell 搜索
data/                 样式、图标、语言包、.desktop 等模板
shell-extension/      顶栏面板（GNOME Shell 扩展，通过 MPRIS 与程序通信）
```

## 说明

本项目借助 AI 完成。

## 许可

GPL-3.0，见 [LICENSE](LICENSE)。
