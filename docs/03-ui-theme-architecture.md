# 03 · UI 与主题架构

## 栈

| 层 | 内容 |
|---|---|
| SoC | MediaTek 嵌入式平台，ARMv7 32 位 |
| 系统 | Android 5.0 / API 21（定制嵌入式，非手机） |
| UI 框架 | Qt 5.6 + QtQuick 2.3 + QtQuick.Controls 1.2 |
| 屏幕 | **480 × 854（竖屏）** |
| 主程序 | `/vendor/sony/bin/HgrmMediaPlayerApp` |

> **屏幕是竖屏 480×854，不是横屏 854×480。** 这一点很容易搞反（我第一版文档就写反了），
> 而它直接决定菜单的视觉比例。证据：
>
> - `assets/images/ic_dac_trans_screen_music_to_dac_with_volumebar.png` 是 **480×854** 全屏过渡图
> - `sys_shutdown_logo.png` 是 **480×800**（= 854 − 54，去掉顶部区域）
> - 各页面 QML 里 `width: 480` 出现 20+ 次，而全仓库 QML **搜不到 854**
> - `OptionMenu.qml` 的 `menu_max_height: 581`、`ContentDetailedInfoLong.qml` 的
>   `popup_area_height: 700` —— 都超过 480，只可能存在于 854 高的屏幕

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

## `p` 不是单例，是每个文件里的局部 `QtObject`

QML 里到处出现 `p.xxx`，很容易误以为它是 C++ 注册的全局单例。**不是。**
每个文件都在底部自己声明了一个局部 `QtObject`：

```qml
QtObject {
    id: p
    property int visibleAreaCellCount: 6
    property int bottomSpace: 56
    property int menuBottomSpace: 106
    property int menu_max_height: 581
}
```

所以：

- **`p` 是文件局部的**，两个文件里的 `p` 毫无关系，改的时候要分别看
- `p.*` 是**布局常量**（尺寸、边距），`viewstyle.*` 才是**颜色**（来自 `ScreenSettings.json`）
- 想改某个尺寸，直接在对应文件底部的 `QtObject` 里改，不用去别处找

真正跨文件的全局单例只有 **`viewstyle`** 一个。

### 菜单的实际几何（`OptionMenu`）

按 `p` 里的常量算出来的真实布局：

```
屏幕          480 × 854
菜单宽度      349   = text_width_without_icon(309) + horizontal_text_margin(20) × 2
菜单最大高度  581   = menu_max_height（条目 ≥ 6 时）
右边距        24    → 右边缘 x = 456
下边距        106   → 下边缘 y = 748
菜单占位      x ∈ [107, 456]，y ∈ [167, 748]
压暗层范围    x ∈ [0, 480]，  y ∈ [0, 798]（bottomMargin = 56，避开导航栏）
```

菜单**占满屏幕 73% 的宽、68% 的高** —— 是个很大的面板，不是小浮窗。

## 叠层顺序 —— 玻璃面板背后其实什么都没有 ⚠️

这是整个改造里**最容易被忽略、但决定成败**的一点。

`Dialog.qml` 里有一个全屏压暗层：

```qml
Rectangle {
    id: grayout
    visible: parent.state == "NoneDialog" ? false : true
    width: parent.width
    height: parent.height
    anchors.bottomMargin: parent.state === "OptionMenu" ? 56 : 0
    color: "black"
    opacity: 0.8            // ← 黑 80%
}
```

`Dialog.qml` 里兄弟元素的**声明顺序**决定叠放顺序（后声明的画在上面）：

```
1. grayout        ← 黑 80%，铺满屏幕（避开导航栏 56px）
2. NavigationBar
3. Loader         ← 菜单在这里，画在最上层
```

所以菜单面板是压在一层**黑 80%** 上的。合成算一下：

```
背景层（grayout 之后）= 0.2 × 屏幕内容
面板 #cc0e2033        = 0.8 × (14,32,51) + 0.2 × 背景层
```

| 屏幕内容 | 面板周围 | 面板本身 | 观感 |
|---|---|---|---|
| 亮（255） | 51 | (21, 36, 51) | **面板比周围更暗** —— 像一块暗斑 |
| 暗（0） | 0 | (11, 26, 41) | 勉强看出一点深蓝 |

**结论：`#cc0e2033` 的半透明面板压在黑 80% 上，透出来的只有黑。**
它看起来会是一块**深蓝近黑的圆角矩形**，而不是液态玻璃。

液态玻璃的本质是"让背后的东西透出来"。而这里背后**什么都没有** —— 全被
`grayout` 压黑了。这是设计上的根本问题，不是配色微调能解决的。

### 正确的做法

把 `grayout` 在菜单状态下调轻，让底层 UI 可见：

```qml
opacity: (parent.state === "OptionMenu" || parent.state === "ContextMenu") ? 0.35 : 0.8
```

这样面板才有东西可以"透"。

**但 `Dialog.qml` 目前不在补丁范围内** —— 补丁只改了 5 个文件
（`OptionMenu` / `OptionMenuList` / `ContextMenu` / `ContextMenuList` / `NavigationBar`），
**没有 `Dialog.qml`**。所以当前这一版刷上去，玻璃感会很弱。

### 代价：实测过了，直接改塞不进去，但剥注释后可以

`Dialog.qml` 是**压缩存储**，槽位只有 **2,360 字节**（解压后 10,840 字节）。
三种改法的实测结果：

| 改法 | 解压后 | 重压缩后 | 结果 |
|---|---|---|---|
| 只改 `opacity` 一行 | 10,881 | 2,366 | ❌ **超出 6 字节** |
| 只剥 CJK 注释 | 9,105 | 1,674 | ✅ 余 686 |
| **剥注释 + 改 opacity** | 9,146 | 1,683 | ✅ **余 677** |

`Dialog.qml` 里有大量日文注释（一个日文字符 3 字节），剥掉就能腾出空间 ——
**这就是 `docs/04` 里那条"剥离 CJK 注释腾空间"规则的用武之地**。

所以这个修复是可行的，但**必须连同剥注释一起做**，单独改那一行会直接失败。

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
