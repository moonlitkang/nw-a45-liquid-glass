# nw-a45-liquid-glass

把 Sony Walkman **NW-A45 / NW-A40** 的原厂菜单改造成 **iOS 26 风格液态玻璃**（半透明 + 圆角 + 边缘高光），并且保留全部原有功能。

这不是一份"替换几张图"的教程，而是一套**可复现、可校验、可回退**的固件改造工具链：从 `.UPG` 容器解密、ext4 定位、Qt 内嵌资源解析，到原位补丁、容器重打包、以及 15 项独立校验。

> 适用机型：NW-A40 / NW-A45 系列（固件 V1.10）。其他 Walkman 型号结构类似，但资源表偏移需要重新定位。

---

## 效果

| 改造前 | 改造后 |
|---|---|
| 不透明灰色九宫格 PNG 弹窗 | 半透明深蓝玻璃面板 + 18px 圆角 + 1px 边缘高光 |
| 菜单行 = 每帧解码并拉伸 PNG | 菜单行 = 纯色圆角矩形（**比原厂更快**） |
| 全局背景纯黑 `#000000` | 全局深蓝 `#0a1622` |

改动落在 5 个 QML 文件、5 个背景 PNG、1 个主题 JSON 上。**其余 1071 个资源、全部 ARM 机器码、内核、bootloader、CM4 音频固件逐字节未变。**

---

## 能做到什么 / 做不到什么

**已经做到（本次改造实际落地的）**

- 选项菜单（`…` 键弹出）→ 玻璃面板
- 上下文菜单（长按 / 右键菜单）→ 玻璃面板 + 玻璃标题栏
- 菜单行的普通态 / 选中态 / 分隔线 → 圆角半透明
- 导航栏 → 深蓝
- **全局主题色** → 所有引用 `viewstyle.bgcolor` 的界面（40+ 个 QML：库视图、播放控件、设置页、状态栏）一起变色
- 列表行 / 播放器面板背景 → 透明，让主题色透出

**原理上还能做（同一套工具链，只是要写新的替换规则）**

- 播放界面、文件浏览页、设置页的逐个控件
- 图标、字体、启动画面（boot logo）、关机动画
- 字符串与本地化文本
- 音效参数（EQ 曲线、DSEE 预设、Clear Phase / VPT / DC Phase 的系数表）

**做不到 / 有硬限制**

- ❌ **模糊（blur）**。NW-A45 是低端 MediaTek + Mali，模糊要走 shader 或 `layer.enabled`，代价是每帧一次离屏渲染。所以玻璃感靠 alpha + 圆角 + 高光边模拟。
- ❌ 任意布局重排。QML 在固定大小槽位里，**只能净减小体积**，不能变大（见 `docs/04`）。
- ❌ 加新功能（新解码格式、新协议）。那要动原生代码，不在本工具链范围。
- ⚠️ 不能保证"零风险刷入"。见 `docs/06`。

---

## 快速开始

```bash
# 1. 依赖
pip install -r requirements.txt

# 2. 从官方升级包解出 system.img（或者用你已有的）
#    官方包 NW-A40_V1_10.exe 里含 NW_WM_FW.UPG，再用 tools/upg.py 解出 sector 6

# 3. 把密钥放进 keys.local.json（见下）
cp keys.example.json keys.local.json

# 4. 打补丁
python tools/patch_ui.py patch stock.img --out patched.img

# 5. 独立验证（15 项）
python tools/verify.py image patched.img stock.img
python tools/verify.py upg   patched.UPG stock.UPG patched.img

# 6. 重打包容器
python tools/patch_ui.py pack stock.img stock.UPG --out patched.UPG
```

### 关于密钥

`tools/upg.py` 需要三组密钥材料才能解开 `.UPG` 容器。**本仓库不附带这些值**，请自行放入 `keys.local.json`（已在 `.gitignore` 中）：

```json
{
  "kas": "<hex>",
  "aes_passkey": "<16 ascii>",
  "aes_iv": "<16 ascii>"
}
```

也可以用环境变量 `WALKMAN_UPG_KAS` / `WALKMAN_UPG_PASSKEY` / `WALKMAN_UPG_IV`。

**没有密钥时，除容器打包外的所有工具仍然可用** —— 资源提取、QML 补丁、镜像校验都不需要密钥。

---

## 工具链

```
tools/
├── config.py          配置与密钥加载
├── ext4fs.py          极简 ext4 读写（只读目录、原位写文件）
├── qtres.py           Qt 5 内嵌资源（RCC）读写
├── upg.py             .UPG 容器解包 / 重打包
├── minpng.py          手写最小 PNG（用于塞进固定槽位）
├── theme.py           玻璃配色表 + QML 替换规则
├── dump_resources.py  把二进制里所有资源导出成文件树
├── patch_ui.py        主补丁脚本（patch / pack 两个子命令）
└── verify.py          15 项独立校验
```

---

## 文档

| 文档 | 内容 |
|---|---|
| [docs/01-upg-container.md](docs/01-upg-container.md) | `.UPG` 容器格式、加密结构、分块压缩 |
| [docs/02-ext4-and-qt-resources.md](docs/02-ext4-and-qt-resources.md) | ext4 定位、Qt 资源树、如何在无符号 ARM 二进制里找到资源表 |
| [docs/03-ui-theme-architecture.md](docs/03-ui-theme-architecture.md) | 播放器 UI 架构、QML 目录、`viewstyle` 全局主题机制 |
| [docs/04-patch-strategy.md](docs/04-patch-strategy.md) | 固定槽位补丁策略（净减小、断言、不静默回退） |
| [docs/05-performance.md](docs/05-performance.md) | 渲染开销审计：为什么这个改动比原厂更快 |
| [docs/06-risks-and-recovery.md](docs/06-risks-and-recovery.md) | 变砖风险分析、分区表、恢复方案 |
| [docs/07-capabilities.md](docs/07-capabilities.md) | 播放器上能做 / 不能做哪些改造 |

---

## 实测校验结果

本工具链产出的镜像与容器，通过 **15/15** 项独立校验：

```
== image checks ==
  [PASS] same image size                    838,860,800 == 838,860,800
  [PASS] only expected files differ         11 block(s) across
                                            ['/vendor/sony/bin/HgrmMediaPlayerApp',
                                             '/vendor/sony/bin/ScreenSettings.json']
  [PASS] ext4 superblock + group descriptors unchanged
  [PASS] patched file sizes unchanged (no ext4 metadata churn)
  [PASS] patched QML sane (balanced, not truncated, no BorderImage)
  [PASS] no blur/shader/layer/opacity constructs
  [PASS] theme recoloured (#0a1622)
  [PASS] stock theme colour gone
  8/8 checks passed

== upg checks ==
  [PASS] container self-MD5
  [PASS] file count matches stock            9
  [PASS] sector layout identical to stock
  [PASS] system sector decodes to the expected patched image
  [PASS] device-side md5 manifest matches decoded image
  [PASS] all other sectors (0,2-5,7,8) byte-identical to stock
  [PASS] encrypted header identical to stock
  7/7 checks passed
```

关键数字：

| 项 | 值 |
|---|---|
| 镜像差异 | **11 个 4K 块**（约 4 MB 范围），全部落在 2 个预期文件内 |
| ext4 元数据差异 | **0** |
| 未修改 sector | **7 / 9**（0,2,3,4,5,7,8 逐字节相同） |
| 改动资源 | **5 个 QML + 3 个 PNG + 1 个主题 JSON** |
| 未改动资源 | **1071 个** |

## 血泪教训（都写进代码里了）

1. **固定槽位只能变小。** 第一版补丁让 QML 变长了，写回时尾部 `}` 被截断 → QML 语法错误 → **菜单打不开**。现在脚本对每次替换都做断言，塞不下直接中止。
2. **压缩资源重压缩后可能变大。** 第一版遇到这种情况静默回退成原版，导致"改了一半"。现在改为中止。
3. **`.UPG` 里有个设备端 MD5 清单（sector 1）。** `update.sh` 会 grep 它，命中就走 `fwpup -z`（写后校验）。忘了同步这个清单，刷写会在中途失败。
4. **`opacity` 属性会触发离屏渲染。** 半透明要用单条 ARGB 颜色（`#AARRGGBB`），不要 `color` + `opacity` 两个属性。
5. **容器里整个 sector 是一整条 CBC 流**，不是每块独立加密；分块之间**没有** 16 字节对齐。假设错了会得到"前 16 字节看起来对、后面全是噪声"的数据。

---

## 免责声明

- 本项目**仅供个人学习与自有设备改造**。修改固件可能使设备无法启动，风险自负。
- 仓库**不包含** Sony 的任何固件、密钥或受版权保护的资源。所有代码为原创实现。
- 请先备份原厂固件与设备数据。刷写前确认你有可用的恢复路径（见 `docs/06`）。
- Sony、Walkman 是 Sony Corporation 的商标。本项目与 Sony 无任何关联。

## 许可

MIT（见 `LICENSE`）。
