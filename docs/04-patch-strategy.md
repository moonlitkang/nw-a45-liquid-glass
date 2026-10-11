# 04 · 固定槽位补丁策略

这一篇记录的是**实际踩过的坑**，以及为什么 `tools/patch_ui.py` 长成现在这样。

## 核心约束：槽位是固定的

QML 文件编译在二进制的 Qt 资源表里，每个文件占一个**固定大小的槽位**
（`data` 表里的 `stored_size`）。补丁只能在槽位内做，**不能改变文件长度**。

原因：改长度会让后面所有资源错位，而资源表里的偏移是写死的。

## 第一版为什么把菜单改坏了

第一版补丁只替换了属性行，没换元素类型：

```qml
BorderImage {
    id: backgroundImage
    source: p.option_background_parts
    color: "#cc0e2033"        // ← 加了这个，但 BorderImage 没有 color 属性
    ...
}
```

两个致命问题：

1. **新文本比原文件长** → 写回时超出槽位 → **尾部 `}` 被截断** →
   QML 语法错误 → **菜单完全打不开**（用户实际看到的现象："点设置后未弹出界面"）
2. **压缩资源重压缩后变大** → 脚本**静默回退**成原版 → 那两个文件根本没改

设备没坏，但补丁是坏的。用户看到的就是"菜单打不开 + 外观没变"。

## 现在强制执行的五条规则

### 1. 元素类型一起换，净减小体积

把 `BorderImage` / `Image` 整个换成 `Rectangle`：

```qml
Rectangle {
    id: backgroundImage
    anchors { ... }
    color: "#cc0e2033"
    radius: 18
    border.color: "#4a7a9a"
    border.width: 1
}
```

`BorderImage` 的九宫格 + 两个 `TileMode` 属性比 `Rectangle` 长得多，
所以换完之后**体积反而变小**，天然塞得进槽位。

实测：

| 文件 | 原大小 | 改后 | 槽位 | 存储方式 |
|---|---|---|---|---|
| `OptionMenu.qml` | 2888 B | 2858 B | 2888 B | 未压缩 |
| `OptionMenuList.qml` | 8702 B | 7174 B | 2174 B | 压缩后 1751 B |
| `ContextMenu.qml` | 1860 B | 1738 B | 1860 B | 未压缩 |
| `ContextMenuList.qml` | 5880 B | 4978 B | 1685 B | 压缩后 1183 B |
| `NavigationBar.qml` | 2755 B | 2375 B | 2755 B | 未压缩 |

### 2. 剥离 CJK 注释腾空间

原厂 QML 里有大量日文注释（`// NavigationBarの高さ分考慮`）。
UTF-8 里一个日文字符 3 字节，删掉这些注释能省 143 ~ 860 字节 —— 是**最便宜的
空间来源**，且完全不影响语义。

```python
def strip_cjk_comments(text):
    for line in text.split("\n"):
        m = re.search(r"//.*", line)
        if m and re.search(r"[^\x00-\x7f]", m.group()):
            line = line[:m.start()].rstrip()
```

只删**含非 ASCII 字符**的注释 —— 纯英文注释保留，不损失可读性。

### 3. 每处替换必须断言

```python
for old, new in subs:
    if old not in text:
        raise RuntimeError(f"{basename}: substitution did not match")
    text = text.replace(old, new)
```

**绝不允许静默跳过。** 静默跳过 = 用户拿到一个"改了一半"的 UI，
而你以为成功了。

### 4. 塞不下就中止，不截断、不回退

```python
if len(stored) > slot:
    raise QtResourceError(f"payload {len(stored)}B does not fit slot {slot}B")
```

截断会产生语法错误（第一版的教训），回退会让改动失效（第一版的另一个教训）。
唯一正确的做法是**报错让人来处理**。

### 5. 写回后立即读回校验

```python
fs.write_file(exts, bytes(blob))
back, _ = fs.read_file(ino)
if back != bytes(blob):
    raise RuntimeError("read-back mismatch")
```

以及 QML 层面的语义校验：

- 大括号平衡（深度归零且**从未为负**）
- 结尾确实是 `}`（没被截断）
- 不含 `BorderImage` 残留
- 含全部 8 个玻璃配色字面量

## 填充字节的选择

| 类型 | 填充 | 理由 |
|---|---|---|
| 未压缩（文本） | `0x20`（空格） | QML 解析器若读越界，空格是无害空白；NUL 不是 |
| 压缩（二进制） | `0x00` | zlib 流之后是填充，零最自然 |

## 一个实现陷阱

`QtResources` 工作在**自己的副本**上（`self.d = bytearray(blob)`）。
所以每改一个文件，必须立刻把整个 chunk 写回 `blob`：

```python
written = res.write(data_off, flags, new_bytes, pad=pad)
blob[:] = res.tobytes()          # ← 漏了这行，所有改动会静默消失
```

而且必须**在重新读取之前**写回 —— 否则下一次 `QtResources(blob, ...)`
拿到的是旧数据，前面的修改全部丢失。

---

## 检查清单（发布前必过）

- [ ] 5 个 QML 文件的替换**全部**命中（无 MISS）
- [ ] 每个文件改后体积 ≤ 槽位
- [ ] 大括号平衡、结尾完整
- [ ] 8 个玻璃配色字面量都在
- [ ] 无 `BorderImage` 残留
- [ ] 无 `FastBlur` / `GaussianBlur` / `DropShadow` / `ShaderEffect` / `layer.enabled` / `opacity:`
- [ ] `system.img` 与 stock 的差异**只落在**两个预期文件上
- [ ] ext4 元数据（超级块、GDT）逐字节相同
- [ ] `.UPG` 的 sector 1 清单 MD5 已同步
- [ ] 容器整包 MD5 自校验通过
