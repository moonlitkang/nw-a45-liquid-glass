# 03 · UI 与主题架构

## 栈

| 层 | 内容 |
|---|---|
| SoC | MediaTek 嵌入式平台，ARMv7 32 位 |
| 系统 | Android 5.0 / API 21（定制嵌入式，非手机） |
| UI 框架 | Qt 5.6 + QtQuick 2.3 + QtQuick.Controls 1.2 |
| 屏幕 | 854 × 480（UI 内部按 480 宽布局，横屏） |
| 主程序 | `/vendor/sony/bin/HgrmMediaPlayerApp` |

## QML 目录结构（从资源树导出）

```
framework/                 框架层
├── FrameWork.qml          主框架（QQmlApplicationEngine 加载的根）
├── Dialog.qml             弹窗基类（含全屏压暗层）
└── ...
components/                通用组件
├── NavigationBar.qml      底部导航栏
├── SettingItemNormal.qml  设置项
└── menu/                  菜单族
    ├── OptionMenu.qml         选项菜单容器（`…` 键）
    ├── OptionMenuList.qml     选项菜单列表 + 委托
    ├── ContextMenu.qml        上下文菜单容器（长按）
    └── ContextMenuList.qml    上下文菜单列表 + 委托
parts/                     零件
libs/  views/  pages/      库视图 / 页面
```

## 全局主题机制 —— `viewstyle`

这是改造里最重要的一条线索。

QML 里到处写着 `viewstyle.bgcolor.D1`、`viewstyle.textcolor.L1` 之类，
但 **`viewstyle` 在 QML 里没有定义** —— 它是 C++ 侧注册的 QML 单例。

顺着这个名字找下去，最终落在：

```
/vendor/sony/bin/ScreenSettings.json
```

结构（节选）：

```json
{
  "style": {
    "normal": {
      "bgcolor": { "L1": "#ffffff", "D1": "#000000", "V1": "#000000" },
      "textcolor": { ... }
    },
    "reverse": { ... }
  }
}
```

`bgcolor.D1 = #000000` 就是**全局背景色**。

**关键发现**：`viewstyle.bgcolor.D1` 在整个 UI 里被 **40+ 个 QML 文件**引用
（库视图、播放控件、设置页、状态栏……）。所以改这一个值，整个 UI 的背景一起变。

这是性价比最高的一处改动：**一个 64 字节的字符串替换，换来全局统一色调。**

> 为什么改 `#000000` → `#0a1622` 而不是别的？
> 长度必须相同（6 位十六进制色值），否则文件长度变化会破坏 ext4 的块分配。
> `#0a1622` 是深蓝，正好配玻璃面板。

## 第二个 C++ 单例：`p`

除了 `viewstyle`（主题），QML 里还有一个参数单例 `p`，提供布局与资源路径：

```qml
source: p.option_background_parts     // → assets/images/popup_option.png
height: p.cell_height
border { left: p.image_border; right: p.image_border; }
```

`p` 的值同样来自 `ScreenSettings.json`（尺寸、边距、贴图路径）。
所以改 QML 时要**同时**注意：`p.*` 是布局参数，`viewstyle.*` 是颜色。

## 为什么还需要改 PNG

主题色改了，但**菜单行的背景是独立的 PNG 贴图**（纯黑），会盖住主题色。

这些 PNG 是 1-bit 单色编码的纯色图（193 ~ 226 字节），例如：

| 文件 | 尺寸 | 原大小 | 用途 |
|---|---|---|---|
| `list_full_normal.png` | 480×84 | 193 B | 列表行常态背景 |
| `list_full_pressed.png` | 480×84 | 196 B | 列表行按下背景 |
| `panel_controller_background.png` | 480×158 | 226 B | 播放器面板背景 |

**难点**：用 Pillow 重新编码成"深蓝色"，输出 352 字节 —— **塞不进 193 字节的原槽位**。
彩色 PNG 必然比 1-bit 单色大。

**解法**：把它们做成**全透明**，让底下刚改好的深蓝主题色透出来。
但 Pillow 的全透明 RGBA PNG 也要 300+ 字节。于是手写最小 PNG：

```
IHDR (1-bit 调色板) + PLTE (单色) + tRNS (alpha) + IDAT (全零行) + IEND
```

全透明输出 **112 字节**，按下态（浅蓝 alpha 60）**112 字节** —— 都塞得下。

见 `tools/minpng.py`。

## `Dialog.qml` 的压暗层

原厂 `Dialog.qml` 已经有一层**黑色 80%** 的全屏压暗。所以新加的玻璃菜单压暗层
只能给 **25%**，否则两层叠加会把背景压成纯黑，玻璃感全没了。

见 `theme.py` 里的 `"scrim": "#400a1420"`。

## 改造覆盖范围

| 界面 | 状态 | 方式 |
|---|---|---|
| 选项菜单（`…` 键） | ✅ | QML 替换 + 玻璃面板 |
| 上下文菜单（长按） | ✅ | QML 替换 + 玻璃面板 + 玻璃标题栏 |
| 导航栏 | ✅ | QML 颜色替换 |
| 全局背景（40+ 界面） | ✅ | `ScreenSettings.json` 主题色 |
| 列表行 / 面板背景 | ✅ | PNG → 透明 |
| 播放界面控件 | ⚠️ 部分 | 继承主题色，但控件本身未逐个改 |
| 图标 | ❌ 未改 | 仍是原厂 |
| 字体 | ❌ 未改 | 仍是原厂 |
| 启动画面 | ❌ 未改 | 在 sector 0，未触碰 |
