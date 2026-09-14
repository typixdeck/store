# 完整 deb 提交与 Store 发布

Store 分发已构建、审查的完整 `.deb`。当前 `debs/` 包括 Launcher、Store、Reader（含 OPDS）、Gamer、完整 MyAI ARM64 客户端、微信安装适配程序及 Copilot 板载固件商店。Gamer 的合法 ROM 与核心由用户提供；腾讯微信原包由设备直接从官网下载，GitHub 只分发我们自己的完整安装/启动程序。

目标 GitHub 仓库为 `typixdeck/store`。公开源采用 `main` 分支根目录下的 `debs/`。在完整工作区使用 `apps/store/debs/`，在独立 Store 仓库使用 `debs/`；工具支持两种结构。Python 完整 deb 包含应用代码，公开目录只放已经获得公开发布授权的内容。

## 准备完整包和清单

独立 Store 仓库结构：

```text
store/
├── debs/
│   ├── manifest.json
│   ├── typix-<app>_<version>_<arch>.deb
│   ├── catalog.json
│   ├── catalog.json.sig
│   ├── public-key.pem
│   └── SHA256SUMS
└── tools/
    ├── publish-store.py
    ├── store_publication.py
    └── tests/test_store_publication.py
```

在 `debs/manifest.json` 中为每个实际包声明应用 ID、包名、原始文件名及 SHA-256、名称/摘要/描述、分类、`.desktop` 文件名、运行方式 `runtime`、实际代码与资源的 `requiredPayload` 路径，以及内存/显示能力要求。现有条目是可用示例；不要添加不存在的包或下载链接。版本、架构、依赖和发行版列表从包内 control 派生，文件名与包内字段冲突、hash 不同、清单外多出的 deb 都会拒绝。

包必须声明 `Package`、`Version`、`Architecture`、非空 `Depends` 和 `X-Typix-Compatible-OS`。当前支持 `Architecture: all` 或 `arm64`；发行版明确列为 `raspios-bookworm`、`raspios-trixie` 中实际支持的值。系统运行库放在 `Depends`，应用自己的主体代码和必需资源必须在 deb 内。

工具支持经过明确检查的 `python-module`、`python-script` 与 ARM64 `native` 三种运行方式。它检查 `.desktop Exec` 指向包内可执行文件，并核对 shell launcher 的 Python 模块/脚本实际位于包中；native 必须是 ARM64 ELF。`requiredPayload` 不能用指向包外路径的软链接代替代码。此结构检查不能代替应用运行测试或对代码内容的审查。

从独立 Store 仓库根目录运行（开发工具依赖 Python 3.11+、`cryptography` 和 `dpkg-deb`）：

```sh
python3 tools/publish-store.py validate --source debs
python3 -m unittest discover -s tools/tests -v
```

在 TypixDeck 大工作区中，把 `--source debs` 改为 `--source debs`。新增完整包时更新清单中的文件名、原始 hash、运行方式和代码/资源列表，重新验证；升级也必须提交原始完整新版本 deb。不要用 Git LFS 指针代替 `.deb` 内容，Store 下载的是实际 deb 字节。

## 密钥与签名

首次为新的发布身份生成私钥时，使用仓库外的路径：

```sh
python3 tools/publish-store.py keygen --key "$HOME/.local/share/typixdeck/signing/store.pem"
```

私钥要求权限 0600，禁止位于源码仓库或发布输出目录。已有信任关系应继续使用对应私钥，不要随意轮换。当前 CM4 的开发信任键对应工作区外的 `~/.local/share/typixdeck/signing/development.pem`；生成的 `public-key.pem` 是可公开公钥，私钥不会进入产物。

catalog 使用 schemaVersion 1 和精确原始字节的 Ed25519 签名；重新格式化 JSON 后必须重新签名。默认有效期 90 天，可通过 `--expires-days` 设置 1–365 天。过期目录需要重新生成、签名并提交。

## Git 仓库目录发布

在已经筛选好公开应用的独立 Store 仓库中生成：

```sh
python3 tools/publish-store.py build \
  --source debs --output debs \
  --repository typixdeck/store --mode raw \
  --key "$HOME/.local/share/typixdeck/signing/development.pem"
```

输出包括原始完整 deb、`catalog.json`、64 字节 `catalog.json.sig`、公钥、审查清单和 `SHA256SUMS`。没有创建新的应用包装包。raw catalog 的 `channel` 是 `github-repository`，`repository` 精确为 `typixdeck/store`。

核对将提交的文件后，把清单、目录、签名与 deb 放在同一个 Git 提交中：

```sh
git add debs tools docs
git diff --cached --stat
git commit -m "Publish reviewed complete Store applications"
git push origin main
```

上面是维护者的实际发布命令；构建工具本身不会执行 Git、上传或安装。`.deb` 属于此目录的受审查源文件，不应忽略；目录中的 `.gitignore` 显式保留 deb，忽略私钥扩展名。raw 模式的本工具政策为单包不超过 100 MiB，更大的完整包使用 Release 资产。

设备端源配置为 `/etc/typix-store/github.json`：

```json
{
  "repository": "typixdeck/store",
  "mode": "raw",
  "ref": "main",
  "directory": "debs",
  "publicKey": "/usr/share/typix-store/keys/development.pem"
}
```

公钥须通过受审查的设备管理流程部署并由系统拥有，不能信任刚下载的任意公钥来替换信任根。客户端只从配置生成 `raw.githubusercontent.com/typixdeck/store/main/debs/` 下的固定目录和 deb 文件地址，不接受 catalog 中任意指定的下载 URL。目标目录尚未提交时，网络 404 是正常的不可用状态，不能报告在线安装成功。

## GitHub Release 资产发布

若改用 Release，先生成不同 channel 的目录，不要直接复用 raw catalog：

```sh
python3 tools/publish-store.py build \
  --source debs --output dist/store-release \
  --repository typixdeck/store --mode release \
  --key "$HOME/.local/share/typixdeck/signing/development.pem"
gh release create store-20260913 dist/store-release/*.deb \
  dist/store-release/catalog.json dist/store-release/catalog.json.sig \
  dist/store-release/public-key.pem dist/store-release/manifest.json \
  dist/store-release/SHA256SUMS --repo typixdeck/store --draft \
  --title "Store applications 2026-09-13" --notes "Reviewed complete application packages."
```

审核草稿中的实际资产后，由维护者发布 Release。设备配置使用 `mode: "release"`、`release: "latest"` 或明确的发布 tag，并保留同一仓库与受信公钥。Release 目录的 `channel` 为 `github-release`。上述 tag 是本次发布命名示例；不会由本工具自动创建。

## 安装大小、资源边界与离线源

每个 artifact 声明 `payload: "complete-deb"`、包内依赖、原始 SHA-256 / 字节数，以及实际 tar 成员大小累计得到的 `installedSizeBytes`；不信任 control 的 `Installed-Size` 自报值。`minFreeDiskMB` 至少为展开大小向上取整后再加 64 MiB。

检查 control 的输出最多 64 KiB；完整数据流最多 2 GiB、100,000 个 tar header，必需代码/资源读取预算 64 MiB；特殊 PAX / GNU 长名称 header 在读取主体前限制单个 1 MiB、累计 4 MiB。control 和 tar 解码都有 30 秒墙钟截止，超时结束本次独立解包进程组。检查只读取，不提取到系统目录、不执行 maintainer scripts。

离线源与公开源共享完整包校验核心，但目录可以收录不同授权范围的应用。在大工作区执行：

```sh
python3 tools/build-offline-repository.py --source debs --version 0.3.0-1
```

仓库版本与各应用版本独立。修改离线源内容时必须升级仓库包版本，不能覆盖已发布的同版本包。离线 catalog 为 `development-offline`，只包含当前 manifest 中已审查的完整应用。当前公开 raw 目录有七个应用包；MyAI 为完整 ARM64 客户端，微信为官方客户端安装适配程序。设备安装、卸载继续走 Store 的可审计授权事务。
