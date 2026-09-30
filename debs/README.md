# 完整 deb 应用源

这里保存签名目录和 22 个完整软件包。Launcher 0.3.0 新增自动、单应用省内存和后台切换模式，保留电量/Wi-Fi 图标与桌面快捷方式筛选。

| 类别 | 应用 |
| --- | --- |
| 现有应用 | Launcher、Store、Reader、Gamer、MyAI、微信适配程序、Copilot |
| 工具与创作 | 计算器、日历、终端、Processing 2D、钢琴、拍立得、USB 键鼠 |
| 媒体与连接 | 网络电台、StreamPlayer、Bilibili、Mail、MoonPilot |
| 游戏 | 五子棋、DOS 游戏库、PS1 游戏库 |

新增 15 个应用各自为 `0.1.0-1`，源码、构建方法、许可、截图和功能边界见 [C1Max suite](https://github.com/typixdeck/c1max-suite)。现有 Reader 对应 CrossPoint 阅读功能，Gamer 对应 NES，Launcher 保持独立维护。应用分别安装，不要求一次装下整套；源码共享不会导致包文件相互覆盖。

本批新增包由官方 Raspberry Pi OS ARM64 Trixie 构建并检查依赖，只声明 `raspios-trixie`；Store 会按设备实际系统与资源判断兼容性。相机需要可用 V4L2 设备，HID 仅使用已经配置并授权的 gadget 节点，远端服务需要用户配置。游戏文件和 PS1 BIOS 由用户提供，DOSBox/Mednafen 由系统依赖安装。没有捆绑 ROM、BIOS、账号或设备数据。

Reader、Gamer、MyAI 和新增应用均包含实际程序。微信适配包包含完整安装/启动代码，腾讯原客户端由设备直接从官网下载。Copilot 仍为 0.2.5，刷新在线签名目录即可获取 DIY 0.4.3，无需为新增固件重装客户端。

应用仓库用根目录 `app.json` 和 `docs/screenshots/` 声明内容。先按照 [仓库规范](../docs/APP-REPOSITORY.md) 导入、审核实际 deb，再生成并签名整份目录：

```sh
python3 tools/publish-store.py validate --source debs
python3 tools/publish-store.py build --source debs --output dist/publication --repository typixdeck/store --mode raw --key /path/outside/repository/signing.pem
```

从工作区使用时把 `--source debs` 改为 `--source apps/store/debs`；在独立 Store 仓库直接执行以上命令。签名后将发布文件复制回本目录并提交，保留原始 deb 字节。不能只上传文件却漏掉清单更新和签名。私钥始终在仓库外。首次建钥、发布与设备信任配置见 [发布说明](../docs/PUBLISHING.md)。
