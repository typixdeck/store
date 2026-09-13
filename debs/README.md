# 完整 deb 应用源

这里保存当前签名目录和六个可安装的软件包：Launcher 0.2、Store 0.3、Reader 0.3（含 OPDS）、Gamer 0.2、MyAI 0.3、微信安装适配程序 0.1。Reader、Gamer、MyAI 均包含实际程序；Gamer 的 ROM 与核心由用户提供。微信适配包包含完整安装/启动代码，腾讯原客户端由设备直接从官网下载，未在 GitHub 镜像。

应用仓库用根目录 `app.json` 和 `docs/screenshots/` 声明内容。先按照 [仓库规范](../docs/APP-REPOSITORY.md) 导入、审核实际 deb，再生成并签名整份目录：

```sh
python3 tools/publish-store.py validate --source debs
python3 tools/publish-store.py build --source debs --output dist/publication --repository typixdeck/store --mode raw --key /path/outside/repository/signing.pem
```

从工作区使用时把 `--source debs` 改为 `--source apps/store/debs`；在独立 Store 仓库直接执行以上命令。签名后将发布文件复制回本目录并提交，保留原始 deb 字节。不能只上传文件却漏掉清单更新和签名。私钥始终在仓库外。首次建钥、发布与设备信任配置见 [发布说明](../docs/PUBLISHING.md)。
