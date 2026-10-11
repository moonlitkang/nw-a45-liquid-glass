# 01 · `.UPG` 容器格式

Sony Walkman 的固件升级包是 `NW_WM_FW.UPG`，由 PC 端 `SoftwareUpdateTool.exe`
通过 USB 推送到设备，设备端由 `update.sh` 消费。

## 整体布局

```
偏移        长度        内容
0x000       0x010       明文 MD5(从 0x010 到文件末尾)
0x010       0x0C0       AES-128-ECB 加密的头部
0x0C0       ...         AES-128-CBC 加密的数据区
```

### 头部（解密后 0xC0 字节）

```
[0x00:0x10]  签名，应为派生出的 sig（32 字节 ASCII 十六进制文本）
[0x10:0x14]  file_count，u32 小端
[0x14:...]   file_count × 16 字节记录，每条 = offset(u32 LE) + size(u32 LE)
```

`file_count` 实测为 **9**，9 个 sector 依次对应：

| # | 内容 | 说明 |
|---|---|---|
| 0 | boot / 引导脚本区 | 未改动 |
| 1 | **设备端 MD5 清单** | 见下节，**必须同步** |
| 2 | bootloader | 未改动 |
| 3 | 内核 | 未改动 |
| 4 | recovery | 未改动 |
| 5 | TrustZone / tee | 未改动 |
| 6 | **system.img（ext4）** | 本次唯一修改的镜像 |
| 7 | CM4 音频协处理器固件 | 未改动 |
| 8 | 杂项 | 未改动 |

### 数据区

**整个 sector 是一整条 AES-128-CBC 流**，用固定 IV，不是每 16 字节独立加密。

> 这里踩过坑：按"每块独立 CBC"去解，会得到前 16 字节看起来正常、后面全是噪声
> 的数据 —— 因为第一条链的 IV 对了，后续链的 IV 全错。

密钥派生（三个常量由设备族密钥材料推出）：

```
ks  = AES-128-CBC(PASSKEY, IV).decrypt(KAS)
key = ks[:16]      # 数据密钥
sig = ks[16:32]    # 头部签名
```

这三组值不在本仓库中，见 README「关于密钥」。

---

## sector 6：system 镜像的压缩格式

解密后的 system sector 是**紧密排列**的块流：

```
[u32 小端 压缩长度][zlib 数据]  × 204800
```

- 每块解压后**恰好 4096 字节**（一个 ext4 块）
- 204800 × 4096 = **838,860,800** 字节，正好是完整镜像
- 记录之间**没有**任何对齐或填充

> 第二个坑：按 16 字节对齐去解析，第 2 块就会解压失败。AES 块大小是 16，
> 但压缩块是紧凑排列的 —— 两者无关。整个流只在最后补一次零到 16 的倍数。

## sector 1：设备端 MD5 清单

解密后是一段纯文本，形如：

```
838860800 <32位md5> system.img
...
```

设备端 `update.sh` 的逻辑大致是：

```sh
grep system.img md5.txt && fwpup -z ...
```

**当清单里存在该行时，会走 `fwpup -z`（写入 + 校验）。** 所以只要重新生成了
`system.img`，就**必须**把这一行的 MD5 同步更新，否则：

- 校验失败 → 刷写报错中止
- 更糟：中止发生在后续 sector（CM4 / NVP）写入之前，可能留下不一致状态

本仓库的 `Upg.set_md5_manifest_entry()` 负责这件事，并且会断言新行长度与旧行一致
（避免改变 sector 1 的长度从而破坏布局）。

---

## 重打包流程

```python
upg = Upg(stock_upg_bytes)

# 1. 重新编码 system 镜像
body = Upg.encode_block_stream(patched_img)     # 4K 块 + zlib + 紧凑排列 + 补零
upg.sector_encrypt(6, body + zeros_to_slot)     # 保持 sector 长度不变

# 2. 同步设备端清单
upg.set_md5_manifest_entry("system.img", md5(patched_img), len(patched_img))

# 3. 重算整包 MD5 并写出
upg.save(out_path)
```

关键约束：**每个 sector 的长度必须保持不变**。头部里的 offset/size 记录是固定的，
改长度会让后面所有 sector 错位。所以编码后的数据超过原槽位时要直接报错，不能截断。

实测：838,860,800 字节镜像 → 109,367,216 字节编码后数据，原槽位 109,371,392 字节，
余量约 4 KB（正好够最后的补零）。

---

## 校验清单

`tools/verify.py upg` 会独立重算以下 7 项：

1. 整包 MD5 自校验 `md5(blob[16:]) == blob[:16]`
2. 头部签名正确、`file_count` 与 stock 一致
3. 9 条 offset/size 记录与 stock 完全一致（布局未变）
4. sector 6 解密 + 逐块解压 == 补丁镜像（逐字节）
5. sector 1 清单里的 MD5 == sector 6 实解出的 MD5
6. sector 0/2/3/4/5/7/8 与 stock **逐字节相同**
7. 加密头部 `[0x10:0x10C0]` 与 stock 逐字节相同
