# 03 · 播放器 UI 与主题系统

## 设备

| | |
|---|---|
| 机型 | Sony NW-A40 / NW-A45 系列 |
| 屏幕 | 854 × 480（UI 内部按 480 宽布局，横屏） |
| SoC | 联发科嵌入式平台，ARMv7（32 位） |
| 系统 | Android 5.0 / API 21（深度定制的嵌入式系统，不是手机） |
| UI 栈 | Qt 5.6 · QtQuick 2.3 · QtQuick.Controls 1.2 |
| 主程序 | `/vendor/sony/bin/HgrmMediaPlayerApp`（8.6 MB，32 位 ARM ELF） |
| 资源 | Qt RCC 内嵌，271 个 QML + 图像资源 |

主程序里能看到 `QQmlApplicationEngine`、`qrc:/framework/FrameWork.qml`、
`OptionMenu`、`ContextMenu`、`ScreenSettings.json` 等线索——**UI 是 Qt Quick 写的**。

## 目录结构（qrc 内）

```
framework/
  FrameWork.qml          应用框架
  Dialog.qml             对话框基类（自带一层黑色 80% 压暗）
  ...
components/
  NavigationBar.qml      底部/侧边导航栏
  SettingItemNormal.qml  设置项
  menu/
    OptionMenu.qml          选项菜单（"…" 键弹出）
    OptionMenuList.qml      选项菜单的列表部分
    ContextMenu.qml         上下文菜单（长按）
    ContextMenuList.qml     上下文菜单的列表部分
parts/
  SCmnIcon.qml           通用图标
assets/images/
  popup_option.png, list_option_4s_no_separator_normal.png, ...
```

## 主题系统：`ScreenSettings.json`

这是最有价值的发现。

播放器的 UI 颜色**不是**散落在各个 QML 里的字面量，而是集中在一个
JSON 文件里：

```
/vendor/sony/bin/ScreenSettings.json
```

结构大致是：

```json
{
  "style": {
    "normal":  { "bgcolor": { "L1": "#ffffff", "D1": "#000000", "V1": "#000000" } },
    "reverse": { "bgcolor": { ... } }
  }
}
```

这些值在 QML 里通过一个 C++ 注册的单例 `viewstyle` 暴露出来：

```qml
color: viewstyle.bgcolor.D1
```

`viewstyle.bgcolor.D1` 在 **40 多个 QML 文件**里被引用——覆盖所有库视图、
播放控件、设置页、状态栏。

**所以改一处 `bgcolor.D1` 就能全局生效。** 这就是本项目实现「界面统一」
的方式：把 `#000000` 换成 `#0a1622`（深蓝），所有界面一起变。

因为替换前后字符串长度相同（都是 7 个字符），文件长度不变，
可以直接原地写。

## 为什么还需要改 QML

改主题色解决了「底色」，但菜单弹窗的背景不是用主题色画的，而是**贴图**：

```qml
BorderImage {
    id: backgroundImage
    source: p.option_background_parts      // PNG 九宫格
    border { top: p.image_border; ... }
    horizontalTileMode: BorderImage.Stretch
    verticalTileMode: BorderImage.Stretch
}
```

`p` 是另一个 C++ 暴露的参数单例，`option_background_parts` 指向
`assets/images/popup_option.png` 之类的贴图。

所以玻璃效果必须**同时**做两件事：

1. 把这些 `BorderImage` / `Image` 换成带 alpha 的 `Rectangle`（QML 层）；
2. 把若干纯黑的列表行 / 面板底图 PNG 换成全透明（资源层），
   否则深蓝主题会被贴图盖住。

## 改造覆盖范围

| 文件 | 改了什么 |
|---|---|
| `OptionMenu.qml` | 全屏压暗层：`opacity: 0.0` → `#400a1420` |
| `OptionMenuList.qml` | 面板 / 行 / 选中行 / 分隔线 共 4 处贴图 → Rectangle |
| `ContextMenu.qml` | 标题栏贴图 → Rectangle |
| `ContextMenuList.qml` | 主体 / 页脚 / 常态行 / 按下行 / 分隔线 共 5 处 |
| `NavigationBar.qml` | `color: "black"` → `#0a1622` |
| `ScreenSettings.json` | 全局 `bgcolor` 黑 → 深蓝 |
| 3 个背景 PNG | 纯黑 → 全透明（共 5 处实例） |

**其余 266 个 QML、全部图像资源、以及整个 ELF 的机器码都没有动。**

## 想继续扩展的话

同一套方法可以直接往这些地方铺：

* 状态栏 / 播放界面 / 文件浏览页的 QML 组件（都还在 chunk 8 里）
* `assets/images/` 下的其他贴图
* 图标：`parts/SCmnIcon.qml` 等，`source` 指向的 PNG 都可以换
* 字体：QML 里 `font.family` 指向的资源
* 启动画面 / logo（在别的分区，需要单独处理）

唯一的约束还是那一条：**替换后的体积不能超过原槽位**。
图像可以用「同尺寸但更简单的 PNG」或者手写极小 PNG（`tools/minpng.py`）来压。
