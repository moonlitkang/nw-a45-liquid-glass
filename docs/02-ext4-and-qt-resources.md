# 02 · ext4 与 Qt 内嵌资源

这一篇记录解析过程中踩到的三个坑。它们都是那种「看起来快成功了、
结果输出一堆乱码」的类型。

---

## 一、ext4：原地写，不碰元数据

`system.img` 是标准 ext4（4096 字节块，extent 格式）。要改的东西只有一个文件：

```
/vendor/sony/bin/HgrmMediaPlayerApp      8,644,592 字节，32 位 ARM ELF
```

**关键约束：写入不能改变文件长度。**

ext4 里文件长度记录在 inode 的 `i_size` 上。如果我们改了长度，就得同时更新
inode 表、块位图，必要时还要分配新块——任何一步出错都会让文件系统损坏，
而且这种损坏**在刷机时不会立刻暴露**，等设备启动到挂载分区才炸。

所以 `tools/ext4fs.py` 的 `write_file()` 只做一件事：把数据写进
`i_block` 里已经登记过的 extent 对应的物理块，不足部分补零，
`i_size` 一个字节都不动。

结果是：补丁后的镜像与原始镜像相比，**只有 11 个 4 KiB 块不同**，
全部落在 `HgrmMediaPlayerApp` 和 `ScreenSettings.json` 的数据块里，
超级块、组描述符、块位图、inode 位图、inode 表逐字节一致。

---

## 二、Qt 内嵌资源（RCC）：`qCompress` 的 4 字节头

### 结构

Qt 5 的资源编译进二进制后是三张表：

```
tree table   14 字节/节点，大端
    u32 name_offset
    u16 flags           bit0 = 压缩, bit1 = 目录
    目录:  u32 child_count, u32 child_offset
    文件:  u32 locale,      u32 data_offset

name table   u16 长度 | u32 hash | UTF-16BE 文本
data table   u32 stored_size | payload
```

### 坑 1：压缩资源多一个 4 字节头

rcc 用的是 Qt 的 `qCompress()`，而 `qCompress()` **会在前面加一个 4 字节
大端未压缩长度**：

```
00 00 08 7e | 00 00 21 fe | 78 9c ...
^ stored    ^ uncompressed ^ zlib
   2174          8702
```

`stored_size = 4 + len(zlib)`，payload = `[u32 BE 未压缩长度][zlib 流]`。

如果直接把整个 payload 丢给 `zlib.decompress()`，会得到：

```
zlib.error: Error -3 while decompressing data: unknown compression method
```

因为开头两个字节是 `00 00`，不是 zlib 的 `78 9c`。**必须先跳过 4 字节。**

未压缩资源没有这个头，`stored_size` 就是文件长度。

### 坑 2：`stored_size` 同时就是槽位大小

下一个资源紧跟在 `data_offset + 4 + stored_size` 之后。所以替换后的 payload
**不能超过原来的 `stored_size`**。这不是「最好别超」，是「超了就覆盖掉
隔壁资源」。

### 坑 3：在 ARM Thumb 二进制里找表指针

二进制被 strip 过，没有符号。但编译器生成的 `qRegisterResourceData()`
包装函数有固定形态：

```
0a 49 01 20 0a 4a 0b 4b 80 b5 00 af
```

即 `ldr r1, [pc, #..]` / `movs r0, #1` / `ldr r2, [pc, #..]` /
`ldr r3, [pc, #..]` / `push {r7, lr}` / `add r7, sp, #0`。

顺着这个模式能找到所有资源表，但**三个 `add` 指令各自的 PC 值不同**：

| 指令 | 偏移 | PC 基准 |
|---|---|---|
| 第 1 个 LDR | +0 | +0x10 |
| 第 2 个 LDR | +4 | +0x12 |
| 第 3 个 LDR | +6 | +0x14 |

（Thumb 的 PC 是「当前指令地址 + 4」，而对齐规则让这三条各差 2。）

如果统一按 `+0x10` 算，三个指针里只有一个是对的，另外两个会指到
莫名其妙的地方——表现为「有时能解出资源，有时解出一堆乱码」。

NW-A45 上主 UI 的资源表在 ELF 内偏移：

```
tree  = 0x548E88
names = 0x549820
data  = 0x54B2F8
```

但 `tools/qtres.py` 的 `find_chunks()` 会自己重新找一遍（共找到 34 张表），
不依赖硬编码。

---

## 三、导出结果

`tools/dump_resources.py` 会把 34 张表里的资源按原始 qrc 路径全部导出：

```
work/resources/chunk_8/components/menu/OptionMenu.qml
work/resources/chunk_8/components/menu/OptionMenuList.qml
work/resources/chunk_8/components/menu/ContextMenu.qml
work/resources/chunk_8/components/menu/ContextMenuList.qml
work/resources/chunk_8/components/NavigationBar.qml
work/resources/chunk_0/assets/images/...
...
```

主 UI（chunk 8）里有 271 个 QML 文件，全部是可读源码——没有字节码，
没有混淆。这一点让整个改造从「逆汇编」降级成了「改文本」。
