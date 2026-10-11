# 01 · 固件容器格式（.UPG）

## 外层：官方升级器

Sony 的固件以 `NW-A40_V1_10.exe` 形式发布，这是一个 Sony Packaging Manager
(SPM) 自解压包。运行一次之后，它会把内容释放到

```
%ProgramData%\Sony Corporation\Sony Packaging Manager\PackagingTemp\<GUID>\
```

里面有：

| 文件 | 作用 |
|---|---|
| `SoftwareUpdateTool.exe` | 真正干活的升级器（负责 USB 传输） |
| `Data\Device\NW_WM_FW.UPG` | 固件本体 |
| `Data\Device\SWUpdate.xml` | 版本描述 |
| `AssistFile.ini` | 升级器行为开关 |

`AssistFile.ini` 里有一个关键项：

```ini
UseSonyCheck=0
```

`UseSonyCheck=0` 表示**升级器不做 Sony 签名校验**。这是能重刷同版本固件的前提。

## 内层：NW_WM_FW.UPG

```
偏移          长度        内容
0x000         0x010       明文 MD5(文件从 0x010 到 EOF)
0x010         0x0C0       AES-128-ECB 加密的头部
0x0C0         ...         AES-128-CBC 加密的数据区
```

### 头部（解密后）

```
0x00  16 字节   签名，ASCII 形式（派生自密钥）
0x10   4 字节   file_count，u32 小端
0x14  16*n      每条记录：offset(u32 LE) + size(u32 LE)
```

NW-A45 Ver 1.10 的 `file_count = 9`，9 个 sector 依次是：

| # | 偏移 | 大小 | 内容 |
|---|---|---|---|
| 0 | 0x0000C0 | 0x2000 | 引导相关 |
| 1 | 0x0020C0 | 0x1000 | **设备端 MD5 清单**（纯文本） |
| 2 | 0x0030C0 | 0x64D000 | 分区映像 |
| 3 | 0x6500C0 | 0x021000 | 分区映像 |
| 4 | 0x6710C0 | 0x044000 | 分区映像 |
| 5 | 0x6B50C0 | 0x0AF000 | 分区映像 |
| 6 | 0x7640C0 | 0x684E000 | **system.img（ext4，800 MiB）** |
| 7 | 0x6FB20C0 | 0x006000 | CM4 音频固件 |
| 8 | 0x6FB80C0 | 0x002000 | NVP 参数 |

### 加密方式

* 头部：AES-128-ECB。
* 数据区：**每个 sector 单独**用 AES-128-CBC 加密，IV 固定。
  不是「整个数据区一条 CBC 链」——这一点搞错的话，前 16 字节看起来是对的，
  后面全是噪声，非常容易浪费一整天。

密钥派生（`tools/upg.py`）：

```
ks  = AES-CBC(passkey, iv).decrypt(kas)
key = ks[0:16]
sig = ks[16:32]
```

`kas` / `passkey` / `iv` 三份密钥材料本仓库不附带，见 README「关于密钥」。

## sector 1：设备端 MD5 清单 —— 最容易漏掉的一步

sector 1 解密后是纯文本，形如：

```
838860800 9e7453cc84fbf98badd85dc4d911a060 system.img
...
```

设备端 `update.sh` 的逻辑大致是：

```sh
grep system.img md5.txt
if [ $? -eq 0 ]; then
    fwpup -z ...      # 写入 + 校验
else
    fwpup ...         # 只写
fi
```

也就是说：**清单里有这一行就走「写入并校验」分支**。如果你重建了 system 分区
却忘了同步这里的 MD5，写后校验必然失败，刷写可能中途中止——而中止意味着
后续的 cm4 / nvp 写入都不会执行。

`tools/upg.py` 的 `set_md5_manifest_entry()` 会在打包时自动刷新这一行，
并且断言**行长不变**（清单本身也是定长槽位）。

## sector 6：system 分区编码

解密后不是裸的 ext4 镜像，而是一个**紧密排列的块流**：

```
u32 LE 压缩长度 | zlib 数据      <- 解压后恰好 4096 字节
u32 LE 压缩长度 | zlib 数据
...
```

共 **204800** 块 = 204800 × 4096 = 838,860,800 字节 = 800 MiB。

**没有对齐**。不按 16 字节对齐，不按 4096 对齐，就是紧挨着排。
（因为要喂给 AES，所以整个块流末尾会补零到 16 字节的整数倍。）

打包时反向操作：逐 4096 字节压缩、拼成块流、补零、整体 CBC 加密、
塞回原槽位。因为重新压缩后的体积一定小于原槽位，所以不需要改头部记录。

## 完整的重打包流程

`tools/patch_ui.py pack` 做的事：

1. 复制原始 system.img，打 UI 补丁；
2. 逐 4096 字节压缩 → 块流 → 补零 → AES-CBC 加密 → 覆盖 sector 6；
3. 重算 system.img 的 MD5 → 改写 sector 1 的清单行 → 重新加密；
4. 重算整包 MD5 → 写入文件头 16 字节；
5. 回读校验：解密 sector 6 → 解压 → 与源镜像逐字节比较；
   解密 sector 1 → 确认清单里的 MD5 与镜像一致。

第 3 步和第 5 步是这个流程里唯一「不做就可能刷坏」的部分。
