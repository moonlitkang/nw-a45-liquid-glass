# 04 · 原地替换的五条铁律

这一篇是踩坑记录。第一版补丁**刷进去了但完全没生效**，而且把菜单弄坏了
（点「…」不弹界面）。原因全部在这五条里。

---

## 铁律 1：替换必须净减小体积

资源槽位是固定的。第一版的问题：

```
OptionMenu.qml       2888 B 槽位 → 替换后 3050 B   ← 超出
```

写超之后代码没报错，而是**静默截断**。结果 QML 文件末尾的 `}` 被切掉了：

```qml
        }
    }
// ← 文件到这里就没了，最后两个大括号不见了
```

QML 解析失败 → 整个菜单组件加载不了 → 点「…」什么都不弹。

**正确做法**：

* 先测新文本长度，超了就报错中止；
* 靠删注释腾空间（CJK 注释在 UTF-8 里一个字符 2–3 字节，
  是最便宜的减重来源）；
* 尽量让替换本身也变小（`BorderImage` 换成 `Rectangle` 通常更短）。

实际结果：

| 文件 | 原大小 | 改后 | 槽位 | 余量 |
|---|---|---|---|---|
| `OptionMenu.qml` | 2888 | 2858 | 2888 | 30 |
| `OptionMenuList.qml` | 8702 | 7174 | 2174（压缩） | 423 |
| `ContextMenu.qml` | 1860 | 1738 | 1860 | 122 |
| `ContextMenuList.qml` | 5880 | 4978 | 1685（压缩） | 502 |
| `NavigationBar.qml` | 2755 | 2375 | 2755 | 380 |

## 铁律 2：元素类型要跟着一起换

第一版只改了属性，没改元素类型：

```qml
BorderImage {                          ← 还是 BorderImage
    id: backgroundImage
    source: p.option_background_parts  ← 还在贴 PNG
    color: "#cc0e2033"                 ← 加了 color，但 BorderImage 不认
    radius: 18                         ← 同上，无效
}
```

`BorderImage` 没有 `color` / `radius` 属性。QML 对未知属性只会警告，
不会报错，于是**看起来改了、实际一点效果都没有**——这正好解释了
「整个菜单还是原来的风格」。

必须把元素类型一起换掉：

```qml
Rectangle {
    id: backgroundImage
    color: "#cc0e2033"
    radius: 18
    border.color: "#4a7a9a"
    border.width: 1
}
```

顺带的好处：`Rectangle` 比 `BorderImage` 短，天然满足铁律 1。

## 铁律 3：每处替换必须断言，失败即中止

第一版有两个压缩资源（`OptionMenuList` / `ContextMenuList`）在重新压缩后
**变大了**，脚本于是**静默跳过**——刷完发现这两个文件根本没改。

现在改成：

```python
for old, new in subs:
    if old not in text:
        raise RuntimeError(f"{basename}: substitution did not match:\n{old[:120]}")
    text = text.replace(old, new)
```

匹配不上就整个构建失败。宁可不出包，也不出半个包。

## 铁律 4：不引入昂贵构造

在 854×480、入门级 Mali GPU、Qt 5.6 默认渲染器上，下面这些都会带来
额外的离屏渲染：

```python
BANNED_TOKENS = (
    b"FastBlur", b"GaussianBlur", b"DropShadow", b"ShaderEffect",
    b"layer.enabled", b"opacity:",
)
```

注意最后一个 `opacity:`。在 Qt Quick 里，**给一个有子元素的 Item 设
`opacity < 1` 会触发 group opacity**，也就是把整棵子树先渲染到离屏
FBO 再合成——对这块 SoC 是实打实的开销。

玻璃质感改用**单条 ARGB 颜色**表达：

```qml
color: "#cc0e2033"      // A=0xCC, R=0x0E, G=0x20, B=0x33
```

而不是：

```qml
color: "#0e2033"
opacity: 0.8            // ← 会被禁用
```

构建时对这四个 token 做断言，命中就直接失败。

## 铁律 5：写完必须回读校验

每个文件写完都重新解出来检查：

1. 大括号平衡（深度回到 0，且过程中从未为负）；
2. 文件末尾确实是 `}`，没有被截断；
3. `BorderImage` 已完全消失；
4. 八个玻璃配色字面量都在；
5. 没有命中禁用 token。

另外整个 ELF 写回 ext4 之后再读一次，与内存中的补丁结果逐字节比较。

---

## 附：为什么不做模糊

真正的 Liquid Glass 有背景模糊。这里没做，原因是：

* Qt 5.6 的 `FastBlur` / `GaussianBlur` 是纯软件实现或需要 shader，
  在 854×480 上每帧都要读一遍背景纹理再做多轮采样；
* 设备 GPU 显存和带宽都很紧张；
* 屏幕只有 480 像素宽，菜单面板约 350×580，模糊带来的观感提升有限，
  但掉帧是立刻能感觉到的。

所以用「高 alpha 深色 + 圆角 + 1px 高光边」模拟玻璃的层次感。
如果哪天愿意接受性能代价，`QtGraphicalEffects` 是可以加进来的，
但必须先拿掉 `BANNED_TOKENS` 里的对应项。
