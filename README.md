# TypixDeck Store 0.3.2 Alpha

原生 GTK3/PyGObject 全屏商店，支持从配置的公开 GitHub 仓库下载**完整 deb 软件包**，然后经过系统授权安装、更新、移除。安装后自动加入 Launcher，已安装应用可直接点击「启动」。没有浏览器、遥测或整机安装清单上传。

新增「已安装」与工具、网络、影音、游戏、系统等分类。应用 Logo 随包提供，未安装或离线时也能显示。以下截图是旧版界面；新版取消手动添加快捷方式和返回桌面按钮。

<!-- app-screenshots:start -->

![应用目录、兼容性与桌面快捷方式管理](docs/screenshots/catalog.png)

应用目录、兼容性与桌面快捷方式管理。

![按名称搜索应用并查看安装状态](docs/screenshots/search.png)

按名称搜索应用并查看安装状态。

<!-- app-screenshots:end -->

界面沿用 Launcher 的深色背景、青色焦点和应用 Logo。支持搜索、分类、详情、Tab、方向键、Enter、Esc、Ctrl+F 和 F5。介绍和技术详情滚动，启动、更新或安装、移除操作固定可见。延续 CM4、官方 Raspberry Pi OS ARM64 和约 800×600 逻辑显示要求。

先更新 Launcher 到 0.3.1，再更新 Store。更新后重新打开 Launcher，让新代码生效，再通过 Launcher 打开 Store；后台应用先保存并正常关闭。Store 的启动请求由 Launcher 统一处理：后台模式使用同一应用跟踪与全屏策略；单应用模式等待 Store 退出后直接启动所选应用，期间不重新显示 Launcher。暂时缺少新版 Launcher 时显示明确更新提示，不绕过其运行模式。

单应用模式请从 Launcher 打开 Store，以建立此次启动的接续通道；从终端直接打开的 Store 会提示重新从 Launcher 进入，不会退出启动器并覆盖正在启动的应用。后台模式也可以通过已运行的 Launcher 接收请求。

Store 开启时检查目录中已安装应用的标准入口，补充缺失的桌面符号链接；安装、更新成功后也会同步。自定义同名文件始终保留，有冲突时使用独立名称。用户删除已管理的快捷方式后，Store 记住隐藏选择，不在每次开启时重复创建；仍可从 Store 启动。卸载只删除 Store 自己创建、且指向原软件包入口的链接，保留用户文档和自定义入口。

安装源出错或过期时，仍可从已验签的本机目录定位已安装应用并启动；这些条目只供本机启动，不获得安装、更新或移除授权。启动器再次核对确切包的已安装状态、系统入口所有权和 dpkg 文件归属，不扫描整机应用清单。

## 应用目录

当前分发 22 个完整 deb：原有七个应用和新增 15 个 [C1Max Linux 应用](https://github.com/typixdeck/c1max-suite)。Launcher 0.3.1 与 Store 0.3.2 提供启动/分类联动；Copilot 0.2.6 配合 DIY 0.4.4 保留兼容版本的设置。12 个套件应用提供 0.2.0 完整 deb，哔哩哔哩提供修复 HTTPS 视频请求头的 0.2.1，其中 10 个原生 ARM64 应用已在 Linux Trixie 隔离环境完成 GTK/Xvfb 启动及界面检查；钢琴、DOS、PS1 提供实际 Python 源码包。新版尚待 CM4 实体触摸、音频及外部服务验收。USB 键鼠与拍立得 0.1.0 保持原样。线上版本与下载校验值以本仓库签名目录为准。新增应用按需单独安装，本批针对官方 Raspberry Pi OS ARM64 Trixie；完整清单和外部设备/服务条件见 [debs/README.md](debs/README.md)。

## GitHub 应用源

管理员配置文件固定为 `/etc/typix-store/github.json`。配置、公钥及所有上级路径必须 root 所有、非符号链接且不可由普通用户改写。没有配置时继续使用签名离线应用源，不猜测仓库。

本项目目标仓库的 raw 模式示例见 [config/github.example.json](config/github.example.json)：

```json
{
  "repository": "typixdeck/store",
  "mode": "raw",
  "ref": "main",
  "directory": "debs",
  "publicKey": "/usr/share/typix-store/keys/development.pem"
}
```

raw 模式从 `raw.githubusercontent.com/typixdeck/store/main/debs/` 读取平铺的 `catalog.json`、`catalog.json.sig` 和 deb 文件。发布者必须先生成、校验并签名目录，再一起提交实际 deb；只提交软件包不会自动生成商店条目。完整包提交入口和发布流程见 [debs/README.md](debs/README.md) 与仓库的发布工具。

也支持 GitHub Release assets：`mode` 省略或为 `release`，`release` 默认 `latest`，可以固定 tag。文件 URL 分别由 `/releases/latest/download/<filename>` 或 `/releases/download/<tag>/<filename>` 派生。目录中的任意 URL 不被使用。只允许 GitHub HTTPS 主机及其明确的下载跳转主机，不携带登录凭据或 API token。

当 GitHub 尚未发布目录、网络失败或远程验签失败时，优先使用仍在有效期内的签名缓存，并显示失败/离线提示；没有有效缓存时回到本机签名离线应用源。过期缓存不能安装，错误签名不会覆盖可信目录。未实际发布远程文件前，不声称在线下载已经可用。

## 签名和完整软件包约定

`catalog.json.sig` 为对 `catalog.json` 原始字节的 Ed25519 原始二进制签名，公钥为 PEM SubjectPublicKeyInfo。使用 `python3-cryptography` 先验签，再解析 JSON。私钥不进入客户端包或设备。

沿用 schemaVersion 1、camelCase 字段。GitHub Release 频道为 `github-release`，raw 频道为 `github-repository`，顶层 `repository` 必须与系统配置完全一致。`generatedAt` 和 `expiresAt` 包含时区且目录处于有效期。应用记录示例：

```json
{
  "id": "ai.typixdeck.reader",
  "package": "typix-reader",
  "currentVersion": "0.3.0-1",
  "name": {"zh-CN": "阅读器"},
  "desktopFile": "typix-reader.desktop",
  "versions": [{
    "version": "0.3.0-1",
    "artifact": {
      "filename": "typix-reader_0.3.0-1_all.deb",
      "arch": "all",
      "sizeBytes": 12345,
      "sha256": "<实际文件的 64 位十六进制 SHA-256>",
      "depends": "python3, python3-gi",
      "payload": "complete-deb",
      "installedSizeBytes": 65536
    },
    "compatibility": {
      "arch": ["arm64"],
      "os": ["raspios-bookworm", "raspios-trixie"],
      "minMemoryMB": 128,
      "minFreeDiskMB": 64,
      "display": ["wayland", "x11"],
      "requiredFeatures": []
    }
  }]
}
```

GitHub 条目必须声明 `artifact.payload=complete-deb`，并提供发布者从实际 deb 文件内容累计得到的 `installedSizeBytes`（正数，上限 8 GiB），兼容性最低空间应覆盖展开大小及 64 MiB 余量。下载后、root 暂存后都重新检查展开所需空间，避免小体积高压缩包耗尽安装磁盘。这个签名声明由发布工具和提交者负责；密码学不能证明任意应用的功能完整。不能把依赖手动部署 `/opt/...` 的入口包装器当作完整软件发布。`filename` 只能是单个 deb 文件名，`desktopFile` 只能是 `/usr/share/applications` 下入口 basename。

安装前核对文件大小、SHA-256、deb 的 `Package`、`Version`、`Architecture`、`Depends`、`X-Typix-Compatible-OS`。签名声明与包内字段冲突时拒绝。架构、官方系统、内存、磁盘与显示能力均在运行时探测，不按 CM 型号硬编码。

## 下载、授权暂存和安装

所有网络和文件校验运行于工作线程。目录最大 4 MiB，签名 64 字节，软件包最大 1 GiB。每次网络读取超时 10 秒，目录每文件总预算 30 秒，软件包总预算 10 分钟；使用单次网络读取后检查截止时间与取消，避免慢速响应长期占住任务。

下载写入普通用户的 `XDG_CACHE_HOME/typix-store/github/<source-hash>/downloads`。按签名 SHA 命名，保留目标 `.part` 续传；Range 必须得到完全匹配的 Content-Range，服务器忽略 Range 时从头覆盖，完成后再次验证全量哈希。损坏的完整 partial 会清理后重试。每源只保留一个目录快照和当前软件包下载，检查空间后才继续；取消、网络中断和低存储都可重试。

普通用户缓存不能直接传给 PackageKit。下载完成后，GUI 通过 `pkexec /usr/libexec/typix-store-stage` 请求一次独立授权：

1. helper 用 `/usr/bin/python3 -I`，忽略用户 Python 路径和当前目录；配置、信任公钥、目标目录固定在系统路径。
2. 仅接受 catalog、签名、deb 的路径和应用 id；先检查当前 `PKEXEC_UID` 所有的普通文件，再用 `O_NOFOLLOW` 文件描述符核对 inode、owner、类型与大小，有界复制到 root 临时目录。
3. 独立重验系统配置、签名、仓库、频道、完整包标记、大小、SHA-256、deb 字段和本机架构/官方 OS/内存/空间。
4. 验证后原子写入 root 所有的 `/var/cache/typix-store/verified/<sha>.deb`。暂存串行锁和 2 GiB 缓存上限限制资源；缓存满时要求管理员清理已完成缓存，不自动删除正在使用的包。

helper 没有网络请求，不执行 dpkg 安装或系统修复，不修改授权规则。暂存结果写系统审计日志；GUI 再校验返回的固定路径和内容，然后通过既有 Gio 异步 PackageKit `InstallFiles` 请求安装授权。可有两次系统授权提示，GUI 不保留 root。

取消暂存等待不会杀 root helper；它最多完成已授权的有界暂存，取消后不会调用 PackageKit。进入 PackageKit 后，仅在 daemon 的 `AllowCancel=true` 时请求 `Cancel`，不杀 dpkg。移除只解析目标已安装包，`allow_deps=false`、`autoremove=false`，保留用户文档，不连带移除依赖。Launcher 和 Store 自身禁用移除入口。

授权失败、依赖缺失、锁冲突、未完成 dpkg 数据库、低空间、超时和服务断开均有提示。不会自动重放不确定事务、自动修复数据库或扩大升级范围。PackageKit 在安装系统依赖时可能需要网络。

## 本机离线源与预览模式

默认路径仍为：

```text
/usr/share/typix-store/repository/{catalog.json,catalog.json.sig,packages/*.deb}
/usr/share/typix-store/keys/development.pem
```

本机频道为 `development-offline`。历史 `config/catalog.json` 示例不会打进 Store deb。离线包路径、签名及公钥必须由 root 管理才能开启系统事务。用户目录显示“用户目录预览，系统安装待管理员部署”，禁用安装、更新、移除；保留浏览和已安装应用的启动入口。

桌面目录通过 `xdg-user-dir DESKTOP` 获取。创建指向系统 `.desktop` 的链接，保留已有同名文件；自动同步仅移除已记录、且仍指向原入口的管理链接，自定义文件交由文件管理器处理。只查询当前签名目录应用的安装状态，不收集整机安装清单。

## 构建和验证

```sh
./build-deb.sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m unittest discover -s tools/tests -v
```

产物 `dist/typix-store_0.3.2-1_all.deb`，架构 `all`，声明 Python/GTK3/librsvg/cryptography/PackageKit/pkexec/xdg-user-dirs 依赖，并建议配套 Launcher ≥ 0.3.1，以及官方 Raspberry Pi OS Bookworm、Trixie 兼容字段。

0.3.2 修正 Store 更新时的图标文件冲突：目录中 22 个应用的 logo 保留在 Store 私有目录，商店直接读取这些文件；每个应用的标准桌面图标仍由自己的 deb 管理。Store 不覆盖其他软件包的图标，也不使用 `Replaces` 或强制覆盖选项。发布工具在签名之前检查所有 deb 的非目录路径，拒绝多个包占用同一个文件或链接，即使内容相同；共享目录允许。已安装应用、用户数据和桌面快捷方式不需要移除。

本机测试覆盖签名、过期、真实 deb 元数据、能力与空间、GitHub URL/跳转约束、网络响应和取消、Range 恢复、损坏缓存重试、未发布目录回退、文件描述符安全与暂存二次验证、PackageKit 错误/取消/超时。网络响应测试使用受控响应，不能替代真实 GitHub 发布后 E2E。CM4 上实际 PackageKit 安装/移除、root 暂存、在线完整包下载及 GTK 验收证据由集成交付记录单独给出。

URL 规则依据 [GitHub Release 链接文档](https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases) 和 [GitHub 仓库内容文档](https://docs.github.com/en/rest/repos/contents)。

## 仓库目录

| 路径 | 用途 |
| --- | --- |
| `app.json` | 应用描述、完整 deb 版本与 SHA-256、截图索引 |
| `README.md` | 功能、真机截图、安装与使用说明 |
| `src/` | 当前程序源码或启动入口 |
| `packaging/` | desktop 与打包辅助文件 |
| `tests/` | 功能与边界验证 |
| `docs/screenshots/` | 可公开的真实运行截图 |
| `build-deb.sh` | 本地构建入口 |
| `dist/` | 构建生成的完整 deb；不提交 Git |

Store 另有 `debs/`（完整包提交与签名目录）、`tools/`（导入与发布工具）、`schemas/`（声明格式）和 `config/`（配置示例）。详情见 [应用仓库约定](docs/APP-REPOSITORY.md) 与 [发布流程](docs/PUBLISHING.md)。

构建后核对并更新 `app.json` 的版本、SHA-256 和截图索引。Store 发布工具读取声明并校验完整软件包；构建不会自动签名、上传或安装。应用仓库不包含用户数据、凭据、私钥或设备采集记录。
