# NW-A45 Liquid Glass

把 Sony Walkman NW-A45 的菜单界面改成半透明玻璃质感（iOS 26 "Liquid Glass" 风格），
并且**只动 UI 资源，不碰任何一行机器码**。

这个仓库是完整的方法与工具链：固件容器解析、ext4 读写、Qt 内嵌资源解析、
QML 原地替换、主题改色、重新打包、以及一套独立的校验器。

> 目标机型：NW-A40 / NW-A45 系列，固件 Ver 1.10
> 界面栈：Qt 5.6 + QtQuick 2.3，主程序 `/vendor/sony/bin/HgrmMediaPlayerApp`

---

## 效果

| 元素 | 原厂 | 改造后 |
|---|---|---|
| 选项菜单面板 | 不透明 PNG 九宫格 | `#cc0e2033` 半透明圆角面板 + 1px 高光边 |
| 菜单行 | 不透明 PNG | `#ad122842` 半透明圆角 |
| 选中行 | 按下态 PNG | `#e01e4a72` 高亮 + 亮边 |
| 上下文菜单 | 不透明 PNG | `#d1102438` 标题栏 / `#c70e2033` 主体 |
| 分隔线 | PNG | `#803a5a7a` 1px |
| 全屏压暗层 | 无 | `#400a1420`（25%） |
| 导航栏 | `black` | `#0a1622` |
| 全局主题 | `#000000` | `#0a1622` |
| 列表行/面板底图 | 纯黑 PNG | 全透明 PNG |

---

## 仓库结构

```
tools/
  ext4fs.py          ext4 镜像读写（原地写，不改文件长度）
  qtres.py           Qt 5 内嵌资源（RCC）解析与原地替换
  upg.py             .UPG 固件容器 解包 / 重打包
  minpng.py          手写极小 PNG（Pillow 压不到槽位以内）
  theme.py           调色板 + QML 替换规则（想改配色改这里）
  patch_ui.py        主入口：patch / pack
  verify.py          独立校验器（不复用构建期任何结论）
  dump_resources.py  把二进制里的资源全导出成文件
docs/
  01-upg-container.md        固件容器格式
  02-ext4-and-qt-resources.md  ext4 与 Qt 资源格式（含三个经典坑）
  03-ui-architecture.md      播放器 UI 与主题系统结构
  04-patch-strategy.md       原地替换的五条铁律
  05-performance.md          在低端 SoC 上的渲染开销分析
  06-risks-and-recovery.md   变砖风险与恢复路径
  07-capabilities.md         现在能在播放器上做到哪些事
keys.example.json            密钥模板（真实密钥不入库）
```

---

## 快速开始

```bash
pip install -r requirements.txt

# 1) 先导出资源，看看原厂长什么样
python tools/dump_resources.py system.img -o work/resources

# 2) 打补丁（只改镜像，不打包）
python tools/patch_ui.py patch system.img --out system_glass.img

# 3) 校验
python tools/verify.py image system_glass.img system.img

# 4) 需要可安装的固件包时（要先配好 keys.local.json）
cp keys.example.json keys.local.json   # 填入密钥
python tools/patch_ui.py pack system.img NW_WM_FW.UPG --out NW_WM_FW_glass.UPG
python tools/verify.py upg NW_WM_FW_glass.UPG NW_WM_FW.UPG system_glass.img
```

### 关于密钥

`.UPG` 容器用的是设备家族相关的 AES 密钥。**本仓库不附带**，请放到
`keys.local.json`（已被 `.gitignore` 排除）或通过环境变量
`WALKMAN_UPG_KAS` / `WALKMAN_UPG_PASSKEY` / `WALKMAN_UPG_IV` 传入。

不含密钥时，**除第 4 步以外的所有功能都能正常使用**——资源导出、QML 替换、
主题改色、镜像校验都不需要密钥。详见 [docs/01](docs/01-upg-container.md)。

---

## 设计原则

改造只做一件事：**把固定大小的资源槽位里的 QML 文本换成另一段更短的文本**。

因此有五条硬性约束（[docs/04](docs/04-patch-strategy.md) 详述）：

1. **净减小体积** —— 槽位是固定的，写超了就会截断，截断的 QML 是语法错误，
   菜单会直接打不开。
2. **元素类型一起换** —— 只改属性不改元素类型，等于什么都没改。
3. **每处替换必须断言** —— 匹配不上就报错中止，绝不静默跳过。
4. **不引入昂贵构造** —— 禁用 `FastBlur` / `GaussianBlur` / `DropShadow` /
   `ShaderEffect` / `layer.enabled` / `opacity:`。
5. **写完必须回读校验** —— 每个文件都重新解出来查括号平衡、查是否被截断。

另外：**绝不做任何改变文件长度的写入**。system.img 的 ext4 元数据（超级块、
块位图、inode 表）与原始镜像逐字节一致，差异只落在两个文件的数据块里。

---

## 校验

`tools/verify.py` 是独立实现的，不复用构建期的任何中间结果：

```
$ python tools/verify.py image system_glass.img system.img
  [PASS] same image size
  [PASS] only expected files differ   -- 11 blocks, 2 files
  [PASS] ext4 superblock + group descriptors unchanged
  [PASS] patched file sizes unchanged
  [PASS] patched QML sane (balanced, not truncated, no BorderImage)
  [PASS] no blur/shader/layer/opacity constructs
  [PASS] theme recoloured (#0a1622)
  [PASS] stock theme colour gone
8/8 checks passed

$ python tools/verify.py upg NW_WM_FW_glass.UPG NW_WM_FW.UPG system_glass.img
  [PASS] container self-MD5
  [PASS] file count matches stock
  [PASS] sector layout identical to stock
  [PASS] system sector decodes to the expected patched image
  [PASS] device-side md5 manifest matches decoded image
  [PASS] all other sectors byte-identical to stock
  [PASS] encrypted header identical to stock
7/7 checks passed
```

---

## 风险

**改造前请先读完 [docs/06](docs/06-risks-and-recovery.md)。**

简版：

* 只改了 2 个文件、11 个 4 KiB 块；bootloader / recovery / TrustZone / 内核 /
  CM4 音频固件全部原封不动。
* 即使 system 分区写坏，recovery 仍在，用官方升级器可以完整刷回原厂固件。
* 唯一需要额外小心的是 `update.sh` 在**缺少匹配的校验信息时会先清零分区**，
  所以设备端 `md5.txt` 清单必须与 system.img 同步更新（工具已自动处理）。

**没有实机测试过就不要刷。** 静态校验能证明「结构完好、语法正确」，
不能证明「渲染效果符合预期」。

---

## 法律与免责

* 本项目仅用于**个人设备的所有权范围内修改**与逆向学习。
* 不包含 Sony 的固件本体、密钥或任何受版权保护的素材；`theme.py` 里的
  替换规则是重新编写的，`dump_resources.py` 输出的是**你自己设备上**的固件内容。
* 刷机有风险，可能导致设备无法开机或数据丢失。后果自负。
* 与 Sony 无任何关联，未获其授权或认可。

代码以 MIT 许可发布，见 [LICENSE](LICENSE)。
