# 05 · 低端 SoC 上的渲染开销

结论先行：**改造后比原厂更快，不是负担。**

---

## 静态统计对比

对改造前后的菜单 QML 做静态扫描：

| 指标 | 原厂 | 改造后 |
|---|---|---|
| 图片元素（`BorderImage` + `Image`） | 11 | **1** |
| 每帧图片解码 / 九宫格拉伸点 | 11 | **1** |
| `opacity` 属性（可能触发离屏合成） | 1 | **0** |
| 模糊 / 着色器 / 离屏图层 | 0 | 0 |
| 动画元素 | 0 | 0 |
| 纯色 `Rectangle` | 2 | 12 |

唯一保留的那 1 个图片元素是封面图标——本来就该留。

## 为什么原厂更贵

原厂的菜单背景是 `BorderImage` + `horizontalTileMode: Stretch`：

```qml
BorderImage {
    source: p.option_background_parts   // PNG
    border { top: p.image_border; bottom: p.image_border; ... }
    horizontalTileMode: BorderImage.Stretch
    verticalTileMode: BorderImage.Stretch
    smooth: false
}
```

每帧要做的事：

1. 采样 PNG 纹理；
2. 按 `border` 切成 9 块；
3. 四角保持原样，四边拉伸，中间平铺/拉伸；
4. 合成到目标矩形。

**每个 `BorderImage` 每帧一次**。菜单列表每行还有常态/按下两张贴图，
面板再加一张，分隔线再来一张——一个 6 行的菜单，一帧就是十几次
纹理采样加九宫格计算。

换成纯色 `Rectangle`：

```qml
Rectangle {
    color: "#cc0e2033"
    radius: 18
    border.color: "#4a7a9a"
    border.width: 1
}
```

一次填充 + 一次圆角光栅化 + 一次 1px 描边。没有纹理绑定，没有采样。

## `opacity` 的陷阱

原厂 `OptionMenu.qml` 里有一个：

```qml
Rectangle {
    id: opacityArea
    opacity: 0.0        // 隐藏状态
}
```

改造时如果写成 `color` + `opacity: 0.8` 两行，Qt Quick 会认为这是一个
**需要 group opacity 的子树**，可能强制走离屏 FBO 合成。

改用单条 ARGB：

```qml
color: "#400a1420"      // 25% alpha 直接编码在颜色里
```

alpha 混合在填充阶段就完成了，不需要额外 pass。

## 全屏 alpha 混合的成本

菜单打开时会有一层覆盖整屏的压暗层：

```qml
Rectangle {
    color: "#400a1420"   // 25% alpha，854×480 全屏
}
```

854 × 480 ≈ 410k 像素，每帧一次 alpha 混合。这在桌面 GPU 上可以忽略，
在入门级 Mali 上是可测量的——但**只发生在菜单打开期间**，
而且原厂系统本身的 `Dialog.qml` 已经有一层黑色 80% 的压暗
（成本比这层更高），所以净增量很小。

第一版用的是 45%（`#730a1420`），叠在系统自带的 80% 黑之上会把背景压得
几乎全黑。降到 25% 后层次感正常，同时混合成本也降了。

## 圆角与边框

Qt 5.6 默认渲染器下，`radius` 走的是 `QQuickRectangle` 的路径光栅化，
在 350×580 这个尺寸上是几十微秒量级，可以忽略。`border.width: 1`
只是多一条描边路径，同样可忽略。

真正要避免的是「给大尺寸元素加 `layer.enabled: true` 再配 `radius`」——
那会强制离屏渲染，成本立刻上一个数量级。本项目没有这么写。

## 每帧绑定

原厂 delegate 里有一个随选中项变化的绑定：

```qml
color: index === list.currentIndex ? A : B
```

每个 delegate 每帧都要重新求值。原厂设计里本来就有独立的
`highlightImage` 叠加层来画选中态，所以 `listImage` 其实可以是常量色。
改造时去掉了这个绑定——**每个 delegate 每帧少一次表达式求值**。

## 性能护栏

`tools/patch_ui.py` 在构建时会扫描补丁后的所有 QML，命中以下任一 token
就直接失败：

```python
BANNED_TOKENS = (
    b"FastBlur", b"GaussianBlur", b"DropShadow", b"ShaderEffect",
    b"layer.enabled", b"opacity:",
)
```

这是为了防止以后加效果时不小心引入性能坑。

## 没有实测帧率

以上全部是**静态代码分析**的结论，不是设备上的实测数据。

要拿到真实帧率需要设备端有可用的 profiling 通道（Qt 的 `QSG_RENDER_TIMING`
环境变量，或者 ADB 里的 `dumpsys gfxinfo`），目前这台设备上没有验证到
可用的调试接口。所以「更快」这个结论的置信度是「结构上必然如此」，
而不是「实测高了 N 帧」。

不过方向上是可以放心的：**把 11 次纹理采样换成 1 次，不可能更慢。**
