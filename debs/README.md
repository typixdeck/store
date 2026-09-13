# 完整 deb 应用源

把可公开分发的完整 `.deb` 放在这里，并在 `manifest.json` 声明入口、实际代码与必要资源。当前提供完整 Gamer 前端；用户自行提供 ROM 和模拟器核心。

在仓库根目录执行：

```sh
python3 tools/publish-store.py validate
python3 tools/publish-store.py build --source debs --output dist/publication --repository typixdeck/store --mode raw --key /path/outside/repository/signing.pem
```

签名成功后将 `dist/publication/` 内原始 deb、目录、签名和公钥等发布文件复制回此目录，再提交 Git。私钥永远保留在仓库外。首次建钥、发布与设备信任配置见 [发布说明](../docs/PUBLISHING.md)。未获得公开分发许可的包不得提交到此公开仓库。
