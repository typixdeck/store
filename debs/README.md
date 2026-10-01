# 完整 deb 应用源

这里保存签名目录和 22 个完整软件包。包含 Launcher 0.3.1 / Store 0.3.2 的已安装启动与分类联动、Copilot 0.2.6，以及 13 个 C1Max 应用的完整 deb（12 个 0.2.0，哔哩哔哩 0.2.1）。22 个应用 Logo 随 Store 包提供。完整包与目录签名一起发布，线上版本以本分支实际文件为准。

| 类别 | 应用 |
| --- | --- |
| 现有应用 | Launcher、Store、Reader、Gamer、MyAI、微信适配程序、Copilot |
| 工具与创作 | 计算器、日历、终端、Processing 2D、钢琴、拍立得、USB 键鼠 |
| 媒体与连接 | 网络电台、StreamPlayer、Bilibili、Mail、MoonPilot |
| 游戏 | 五子棋、DOS 游戏库、PS1 游戏库 |

计算器、日历、五子棋、终端、Processing、网络电台、StreamPlayer、Bilibili、Mail、MoonPilot 的原生 ARM64 包，以及钢琴、DOS、PS1 的架构无关源码包，除哔哩哔哩为 `0.2.1-1` 外，其余均为 `0.2.0-1`。新版按 TypixDeck 内容尺寸布局，移除公共底部控制条。USB 键鼠与拍立得保持 `0.1.0-1`；拍立得源码和 deb 未改。源码、构建方法、许可、截图和功能边界见 [C1Max suite](https://github.com/typixdeck/c1max-suite)。现有 Reader 对应 CrossPoint 阅读功能，Gamer 对应 NES，Launcher 保持独立维护。应用分别安装，不要求一次装下整套；源码共享不会导致包文件相互覆盖。

10 个新版原生包在 Linux ARM64 Trixie 隔离构建环境完成真实 GTK/Xvfb 启动、退出和界面检查，全部 15 个套件包通过 APT 依赖模拟；钢琴、DOS、PS1 0.2.0 是带明确 Trixie 目标声明的架构无关源码包，已核对真实代码与元数据。新版尚未在本轮验证 CM4 实体触摸、音频、外部服务与系统安装。这批应用面向官方 Raspberry Pi OS ARM64 Trixie，只声明 `raspios-trixie`；Store 会按设备实际系统与资源判断兼容性。相机需要可用 V4L2 设备，HID 仅使用已经配置并授权的 gadget 节点，远端服务需要用户配置。游戏文件和 PS1 BIOS 由用户提供，DOSBox/Mednafen 由系统依赖安装。没有捆绑 ROM、BIOS、账号或设备数据。

Reader、Gamer、MyAI 和新增应用均包含实际程序。微信适配包包含完整安装/启动代码，腾讯原客户端由设备直接从官网下载。Copilot 0.2.6 使用签名 v2 固件目录，兼容来源的 DIY 0.4.4 升级保留设置；旧 v1 目录不变。请先升级 Copilot 再写 DIY 0.4.4，直接整包写入仍会清空 NVS。

应用仓库用根目录 `app.json` 和 `docs/screenshots/` 声明内容。先按照 [仓库规范](../docs/APP-REPOSITORY.md) 导入、审核实际 deb，再生成并签名整份目录：

```sh
python3 tools/publish-store.py validate --source debs
python3 tools/publish-store.py build --source debs --output dist/publication --repository typixdeck/store --mode raw --key /path/outside/repository/signing.pem
```

从工作区使用时把 `--source debs` 改为 `--source apps/store/debs`；在独立 Store 仓库直接执行以上命令。签名后将发布文件复制回本目录并提交，保留原始 deb 字节。不能只上传文件却漏掉清单更新和签名。私钥始终在仓库外。首次建钥、发布与设备信任配置见 [发布说明](../docs/PUBLISHING.md)。

Store 0.3.2 修复图标文件归属冲突；22 个目录 Logo 留在 Store 私有目录，其他应用的标准图标由各自软件包提供。签名前检查整份目录中的非目录路径，拒绝共享文件或链接。哔哩哔哩 0.2.1 补齐 HTTPS 播放请求头，同时校验服务器证书；已在 CM4 匿名公开视频中验证解码。
