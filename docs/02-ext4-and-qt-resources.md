# 02 · ext4 与 Qt 内嵌资源

## 目标文件

播放器主程序：`/vendor/sony/bin/HgrmMediaPlayerApp`
- 8,644,592 字节，32 位 ARM ELF
- Qt 5.6 + QtQuick 2.3
- 整个 UI（QML + 图片）编译在二进制内部，通过 Qt 资源系统（RCC）加载

同一目录下还有 `ScreenSettings.json`（全局主题配置，见 `docs/03`）。

---

## ext4 定位

镜像块大小 4096。定位一个文件需要走三步：

1. **超级块**：偏移 1024，`s_magic` 应为 `0xEF53`。
   - `s_log_block_size` → 块大小
   - `s_inodes_per_group`、`s_inode_size`、`s_feature_incompat`
2. **组描述符表**：紧跟超级块（块大小 1024 时从块 2 开始，否则从块 1）。
   - `bg_inode_table_lo`（+ `_hi` 当 64BIT 特性置位）→ inode 表位置
3. **inode**：`(num-1)//ipg` 定组，`(num-1)%ipg` 定组内下标。
   - `i_mode` 判类型（`0x8000` = 普通文件，`0x4000` = 目录）
   - `i_size_lo` / `i_size_high`
   - `i_block[60]` 起始是 extent 树

### extent 树

节点头 12 字节：`eh_magic(0xF30A)`、`eh_entries`、`eh_max`、`eh_depth`、`eh_generation`。
之后每项 12 字节：

- `depth == 0`（叶子）：`ee_block`(u32)、`ee_len`(u16)、`ee_start_hi`(u16)、`ee_start_lo`(u32)
  → 逻辑块、物理块、块数
- `depth > 0`（索引）：`ei_block`(u32)、`ei_leaf_lo`(u32)、`ei_leaf_hi`(u16)、`ei_unused`(u16)
  → 递归读子节点

注意 `ee_len` 要 `& 0x7FFF`：最高位是"未初始化 extent"标志。

### 原位写入

本工具链**只做原位写入**：文件长度不变，因此块位图、inode 位图、inode 表本身
**完全不动**。这是刻意设计 —— ext4 元数据零改动意味着镜像的其它部分与出厂状态
逐字节一致，出问题时排查范围极小。

`Ext4.write_file()` 会检查 `len(data) <= 已分配块数 × 块大小`，超了直接报错。

---

## Qt 5 资源格式（RCC v1）

### 三张表

**tree 表** —— 14 字节一个节点，大端：

```
u32 name_offset     名称表偏移（根节点为 0）
u16 flags           bit0 = 已压缩，bit1 = 目录
--- 若为目录 ---
u32 child_count
u32 child_offset    第一个子节点的**索引**（子节点连续排列）
--- 若为文件 ---
u32 locale
u32 data_offset     data 表偏移
```

**names 表**：

```
u16 length          UTF-16 码元个数
u32 hash            Qt 自己的名字哈希（读取时不需要）
length × 2 字节     UTF-16 **大端** 文本
```

**data 表**：

```
u32 stored_size     后面跟的字节数
payload             原始字节，或（flags bit0 置位时）qCompress 数据
```

### 最容易搞错的一点

压缩资源的 payload 是 **Qt `qCompress()` 的格式**，而不是裸 zlib 流：

```
u32 BE 未压缩长度 | zlib 流
```

所以 `stored_size = 4 + len(zlib 流)`，解压时**必须先跳过 4 字节**：

```python
raw = blob[p + 4 : p + 4 + stored_size]
dec = zlib.decompress(raw[4:])        # ← 跳过 4 字节
```

实测（`OptionMenuList.qml`）：

```
stored_size = 2174 (0x87e)
raw[:8]     = 00 00 21 fe | 78 9c ...
              ^^^^^^^^^^^^  ^^^^^^
              未压缩长度 8702  zlib
```

直接 `zlib.decompress(raw)` 会抛
`Error -3 while decompressing data: unknown compression method` ——
因为开头两个字节是 `00 00` 而不是 zlib 的 `78 9c`。

**这个细节是自写解析器产出垃圾数据的头号原因**，`qtres.py` 里专门注释了。

> 反向写入时别忘了把 4 字节长度前缀加回去，否则自己写出去的数据自己读不回来。

### 在无符号二进制里找到资源表

ELF 被 strip 过，没有符号。但 Qt 的资源注册代码是编译器生成的固定形状：

```asm
; ARM Thumb
ldr  r1, [pc, #..]      ; tree
movs r0, #1             ; version = 1
ldr  r2, [pc, #..]      ; names
ldr  r3, [pc, #..]      ; data
push {r7, lr}
add  r7, sp, #0
bl   qRegisterResourceData
```

按这段字节模式（`0a4901200a4a0b4b80b500af`）搜索，然后**解码三条 PC 相对字面量
加载**，就能得到三张表的地址。

关键细节：三条 `add rX, pc, #imm` 指令各自使用**不同的 PC 值**（`addr+0x10`、
`addr+0x12`、`addr+0x14`），不能统一按一个偏移算。算错会得到三个几乎正确、
但差几字节的地址 —— 于是解析出"看起来像树、实际是垃圾"的结果。

NW-A45 这个 build 里能找到 **34 个**资源表，共 **1076 个**资源文件。

---

## 导出结果示例

```
work/resources/
├── chunk_0/assets/images/...          (图片、图标)
├── chunk_1/...
├── ...
├── chunk_8/
│   ├── components/
│   │   ├── NavigationBar.qml
│   │   └── menu/
│   │       ├── OptionMenu.qml
│   │       ├── OptionMenuList.qml
│   │       ├── ContextMenu.qml
│   │       └── ContextMenuList.qml
│   ├── framework/
│   │   ├── FrameWork.qml
│   │   └── Dialog.qml
│   └── parts/...
└── chunk_33/...
```

用 `python tools/dump_resources.py stock.img -o work/resources` 生成。
