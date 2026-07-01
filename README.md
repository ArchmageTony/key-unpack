# Key Unpack

Key Unpack 是一个用于压缩包密码管理和自动试解的 Python 工具。
当前版本包含 v1 的 core, CLI 和 PySide6 桌面 UI, 不包含压缩功能, 在线密码源,
以及自动递归多层解压。

## 当前范围

- 提供可复用的 core 解压 API, 后续 CLI 和 UI 共用同一套逻辑.
- 提供 CLI 命令, 覆盖解压, 密码管理和基础配置.
- 提供 PySide6 桌面 UI, 覆盖拖拽解压, 一键存储剪贴板密码, 密码管理, 设置和日志查看.
- 使用 JSONL 保存密码库, 支持去重, 临时密码, 一次性密码和原子写入.
- 解压前先通过 7-Zip 测试候选密码, 成功后只执行一次正式解压.
- 使用 JSONL 记录成功解压日志.
- 支持中文密码编码兼容候选, 默认启用.
- 支持批量解压, 输出逐文件任务事件, 部分失败时返回非零退出码.
- 支持常见分卷压缩包的主卷归一化, 避免重复处理子卷.

## 环境要求

- Python 3.11 或更高版本.
- 系统中存在 `7z`, `7zz`, `7za`, 或通过配置显式指定 7-Zip 路径.

core 和 CLI 没有第三方 Python 运行时依赖。桌面 UI 需要 PySide6。

## 本地安装

```bash
python -m pip install -e .
```

安装桌面 UI 依赖:

```bash
python -m pip install -e '.[ui]'
```

安装后可使用:

```bash
key-unpack --help
```

也可以直接使用模块入口:

```bash
python -m key_unpack --help
```

启动桌面 UI:

```bash
key-unpack-ui
python -m key_unpack.ui
```

## CLI 用法

查看帮助:

```bash
key-unpack --help
key-unpack extract --help
key-unpack password --help
key-unpack config --help
```

指定数据目录:

```bash
key-unpack --data-dir <dir> <command>
```

解压一个或多个压缩包:

```bash
key-unpack extract <archive>
key-unpack extract <archive1> <archive2>
key-unpack extract <archive> --output <dir>
key-unpack extract <archive> --overwrite skip
key-unpack extract <archive> --overwrite overwrite
key-unpack extract <archive> --overwrite rename
key-unpack extract <archive> --overwrite fail
key-unpack extract <archive> --sevenzip <path-to-7zip>
key-unpack extract <archive> --temp-dir <dir>
key-unpack extract <archive> --no-encoding-compat
key-unpack extract <archive> --json
```

管理密码:

```bash
key-unpack password add <password> --type permanent
key-unpack password add <password> --type one_time
key-unpack password add <password> --type temporary
key-unpack password import <password-file>
key-unpack password list
key-unpack password cleanup
```

管理配置:

```bash
key-unpack config set sevenzip_path <path-to-7zip>
key-unpack config set output_dir <dir>
key-unpack config set temp_dir <dir>
key-unpack config set default_password_type one_time
key-unpack config set temporary_password_days 7
key-unpack config set strip_imported_passwords true
key-unpack config set enable_password_encoding_compat true
key-unpack config set log_success_password true
key-unpack config set max_log_records 1000
key-unpack config set max_log_bytes 1048576
key-unpack config set command_timeout_seconds 300
key-unpack config list
```

## 数据文件

从源码运行时, 默认会在当前工作目录生成 `data/` 目录。CLI 也支持在子命令前
通过 `--data-dir <dir>` 指定数据目录。

生成的数据文件:

- `config.json`: 可迁移的通用设置.
- `local.json`: 本机路径相关设置.
- `passwords.jsonl`: 密码记录.
- `extract_log.jsonl`: 成功解压日志.
- `backups/`: 密码库修改前的滚动备份.

密码记录以明文 JSONL 保存。该工具只适合保存允许明文存储的压缩包密码, 不应
保存私人账号密码或其他敏感凭据。成功解压日志默认也可能记录命中的密码。

## 退出码

- `0`: 所有请求的操作均成功.
- `1`: 至少一个解压任务失败.
- `2`: 命令参数或配置错误.
- `130`: 用户中断.

## 开发约束

- core 不依赖 UI 库.
- UI 只调用 core API, 不重新实现解压, 密码轮询, 日志或 7-Zip 调用逻辑.
- 7-Zip 必须使用参数数组调用, 不拼接 shell 命令.
- 解压先写入任务临时目录, 再整理到最终输出目录.
- v1 不做自动多层解压.
