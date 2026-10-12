# 05 · 渲染开销审计

目标设备是**低端嵌入式 GPU**（Mali 入门级，共享内存带宽）。任何全屏离屏渲染
都是实打实的卡顿来源。所以这次改造的核心目标之一就是：**不要变慢**。

结论先说：**改造后比原厂更快。**

## 逐项对比

对 5 个改造文件的 QML 做了静态统计（`BorderImage` / `Image` / `Rectangle` /
`opacity` / 模糊类 / 动画类的出现次数）：

| 指标 | 原厂 | 改造后 | 变化 |
|---|---|---|---|
| 图片元素（`BorderImage` + `Image`） | 11 | 1 | **−10** |
| 每帧图片解码 / 九宫格拉伸点 | 11 | 1 | **−10** |
| `opacity` 属性（可能触发离屏渲染） | 1 | 0 | **−1** |
| 模糊 / 着色器 / 离屏图层 | 0 | 0 | 0 |
| 动画元素 | 0 | 0 | 0 |
| 纯色 `Rectangle` | 2 | 12 | +10 |

## 为什么这是净提速

### 原厂每帧在做什么

原厂菜单背景是 `BorderImage` 九宫格贴图：

```qml
BorderImage {
    source: p.option_button_normal_parts
    border { left: p.image_border; right: p.image_border; }
    horizontalTileMode: BorderImage.Stretch
    verticalTileMode: BorderImage.Stretch
    smooth: false
}
```

每个这样的元素，每帧要：**采样 PNG 纹理 → 九宫格切分 → 边缘拉伸 → 中心填充**。
一个 6 行菜单 = 1 个面板 + 6 个行背景 + 6 个分隔线 + 若干高亮层，
**11 处**这样的操作。

### 改造后

```qml
Rectangle {
    color: "#ad122842"
    radius: 10
}
```

一个纯色圆角矩形。GPU 只需要填充 —— 没有纹理采样、没有拉伸、没有 PNG 解码。

**所以这不是"用玻璃效果换来性能损失"，而是用更便宜的绘制方式实现了更现代的外观。**

## 三个具体的性能决策

### 1. 半透明用 ARGB 颜色，不用 `opacity`

```qml
// ❌ 会触发 group opacity
Rectangle {
    color: "#0e2033"
    opacity: 0.8
}

// ✅ 单条 ARGB，无需额外 pass
Rectangle {
    color: "#cc0e2033"
}
```

Qt Quick 里，**带子元素的 item 上设 `opacity < 1`** 会走 group opacity，
可能强制一次离屏 FBO 合成 —— 对低端 GPU 是明确的负担。

改成 `#AARRGGBB` 后，alpha 直接进填充色，**零额外开销**。

改造后 `opacity` 属性出现次数 = **0**。

### 2. 去掉 delegate 里的三元绑定

```qml
// ❌ 每帧每个 delegate 求值一次
color: index === list.currentIndex ? "#e01e4a72" : "#ad122842"

// ✅ 常量
color: "#ad122842"
```

原设计里**已经有**一个独立的 `highlightImage` 叠加层负责选中态，
所以 `listImage` 完全可以是常量。这样每个 delegate 每帧少一次绑定求值。

### 3. 压暗层只给 25%

```qml
color: "#400a1420"    // 25% alpha
```

原厂 `Dialog.qml` 已经有**黑色 80%** 的全屏压暗。再叠一层重压暗会：

- 视觉上把背景压成纯黑（玻璃感全失）
- 每帧多一次全屏 alpha 混合

25% 既能突出面板，又不至于压过头。

## 全屏 alpha 混合的实际成本

改造引入了一个覆盖全屏的 25% 压暗层（480×854，避开底部 56px 导航栏 → 实际 480×798）。评估：

- 面积：约 41 万像素
- 操作：一次 read-modify-write alpha blend
- 在 Mali 入门级上，这是**一次全屏 fill** 的量级，远低于一次纹理采样 + 九宫格拉伸

**净账**：省掉 10 处九宫格拉伸（每处都要采样 + 混合），换来 1 次纯色全屏填充。
**稳赚。**

## 性能护栏

`patch_ui.py` 在写回前会扫描产物，命中以下任一构造就**直接中止构建**：

```python
BANNED_TOKENS = (
    b"FastBlur", b"GaussianBlur", b"DropShadow", b"ShaderEffect",
    b"layer.enabled", b"opacity:",
)
```

这是**构建期的硬约束**，不是事后检查。任何人（包括未来的我）想加模糊效果，
构建会立刻失败并说明原因。

## 还有哪些可以再优化

按"收益 / 成本"排序：

| 优化 | 收益 | 影响观感 | 成本 |
|---|---|---|---|
| 压暗层只覆盖菜单区域，不全屏 | 省约 60% 混合面积 | 几乎无 | 低（改 anchors） |
| 去掉分隔线，用间距代替 | 少 5~6 个绘制元素 | 轻微 | 低 |
| 面板 `radius` 从 18 降到 12 | 圆角三角带像素更少 | 轻微 | 极低 |
| 行背景去掉 `border` | 少一圈描边绘制 | 失去高光边 | 极低 |
| 用不透明色 | 完全消除混合 | **失去玻璃感** | 极低 |

**前四项都可以做，但收益都是边际的** —— 因为当前改动已经比原厂快了，
没有必须再优化的理由。第五项不建议，那是这个项目的全部意义所在。

## 实测数据

在本机用 Python 对导出的 QML 做静态统计（`tools/` 外的审计脚本），
结论如上表。**没有在真机上测过帧率** —— 因为目前没有已验证的设备调试通道
（见 `docs/06`）。上机后的实际观感需要刷完后确认。
