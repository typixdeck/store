# 应用仓库与 Store 识别

每个应用使用独立仓库，文件名固定。`app.json` 是发布入口；Store 发布工具读取它，不运行仓库里的脚本。

```text
app.json                 应用信息、完整包版本/SHA256、截图索引
README.md                功能、真实截图、安装与快捷键
src/                     当前程序源码
packaging/               desktop、系统 helper 等打包文件
tests/                   有意义的功能与边界测试
docs/screenshots/        真实运行截图，PNG/JPEG
build-deb.sh             本地构建入口
dist/                    构建产物，Git 忽略
```

Launcher 保留 `packaging/debian/`、`config/` 等现有目录，不为了统一名称移动已使用的入口。历史快照、用户数据、账号、私钥、设备采集结果不进入应用仓库。截图只展示安全的演示内容，不能包含登录二维码、服务器凭据或个人书籍内容。

字段格式见 [app.schema.json](../schemas/app.schema.json)。`application` 沿用 deb 提交清单的应用描述；`release.file` 必须是 `dist/` 内的完整 deb，版本和 SHA256 必须对应实际文件；截图必须位于 `docs/screenshots/`，1–8 张，每张不超过 8 MiB。构建后更新 `app.json` 的版本和 SHA256，再提交源码。构建脚本不会自动签名或上传。

## 从多个仓库导入

先在各应用仓库本地构建或放入已经审核的 deb，然后在 Store 仓库运行：

```sh
python3 tools/import-apps.py ../launcher ../reader ../gamer --output build/submissions
# 或发现直接子目录中的 app.json：
python3 tools/import-apps.py --apps-root ../checked-apps --output build/submissions-new
```

输出目录必须尚不存在。工具校验实际 deb 的版本、架构、依赖、OS、SHA256、desktop 入口和完整程序载荷，拒绝重复包、目录穿越、符号链接与入口包装器。任一应用失败时不生成半份提交目录。只会读取明确指定的本地仓库，不访问任意下载链接，不执行仓库构建脚本，也不扫描用户的已安装应用。

审核生成的 `manifest.json`，再按 [发布说明](PUBLISHING.md) 签名并发布：

```sh
python3 tools/publish-store.py build \
  --source build/submissions --output build/publication \
  --repository typixdeck/store --mode raw --key /absolute/private/signing.pem
```

合并到正式 `debs/` 时必须保留其他已发布应用，并重新校验、生成整份签名目录。私钥必须留在仓库外。客户端仍只信任管理员固定的签名 Store 源，不会因为某个 GitHub 仓库出现 `app.json` 就自动安装它。签名目录保留应用仓库和截图索引；当前 Store 界面显示文字详情，README 展示截图。

微信条目是完整的 Typix 微信安装适配程序；腾讯客户端由设备从官网另外下载，不能把适配包描述成内含微信原版。MyAI 0.3 包含完整 ARM64 客户端；旧入口包不会通过此发布检查。
