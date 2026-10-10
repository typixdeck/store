# 验证说明

主验收环境是官方 Raspberry Pi OS ARM64 的 CM4、GTK3 与 Labwc。Launcher 默认仅显示 Desktop 快捷方式；Store 与 Reader 原生全屏，正常关闭后返回 Launcher。

在仓库根目录执行 `./build-deb.sh` 构建；有 tests 的应用执行 `PYTHONPATH=src python3 -m unittest discover -s tests -v`。Store 的公开签名应用源由仓库中的 deb 发布工具构建，安装通过系统授权。不会上传设备标识、安装清单或使用统计。

设备采集记录和用户数据不随源码发布。

## 0.3.4 应用源权限修复

在 Linux ARM64 Trixie 隔离容器中复现旧安装方式：root 所有的
`/etc/typix-store` 为 `0700`，JSON 为 `0644`，普通用户读取报
`PermissionError`。实际执行 deb 解包与配置后，目录恢复为 `0755`、配置内容
保持不变、普通用户可读、`dpkg --audit` 无输出；也检查重复配置与符号链接拒绝。

Launcher 安装器验证 `umask 077` 下的新建目录、已有源/公钥权限修复、自定义源
冲突拒绝、未知字段保留、`--check` 不提权，以及普通安装先验签再授权。
Store 单元测试覆盖目录不可遍历、配置/公钥不可读，构建后连同实际 deb 文件归属
检查共 93 项通过，发布工具 35 项通过。Launcher 147 项通过。GTK/Xvfb 800×600 检查错误提示与禁用
安装操作。上述容器验证不等同于在截图所示用户设备上完成修复。
