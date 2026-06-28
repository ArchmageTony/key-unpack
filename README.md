# Key Unpack

Key Unpack 是一个用于公开分享资源解压密码管理和自动试解的 Python 工具。
当前版本只包含 v1 的 core 和 CLI, 不包含桌面 UI, 压缩功能, 在线密码源,
以及自动递归多层解压。

## 当前范围

- 提供可复用的 core 解压 API, 后续 CLI 和 UI 共用同一套逻辑.
- 提供 CLI 命令, 覆盖解压, 密码管理和基础配置.
- 使用 JSONL 保存密码库, 支持去重, 临时密码, 一次性密码和原子写入.
- 解压前先通过 7-Zip 测试候选密码, 成功后只执行一次正式解压.
- 使用 JSONL 记录成功解压日志.
- 支持中文密码编码兼容候选, 默认启用.
- 支持通过 7-Zip `-t#` 对 MP4 尾部拼接压缩包做一层外壳剥离.
- 支持批量解压, 输出逐文件任务事件, 部分失败时返回非零退出码.
- 支持常见分卷压缩包的主卷归一化, 避免重复处理子卷.

## 环境要求

- Python 3.11 或更高版本.
- 系统中存在 `7z`, `7zz`, `7za`, 或通过配置显式指定 7-Zip 路径.

当前项目没有第三方 Python 运行时依赖。

## 本地安装

```bash
python -m pip install -e .
```

安装后可使用:

```bash
key-unpack --help
```

也可以直接使用模块入口:

```bash
python -m key_unpack --help
```

## CLI 用法

解压一个或多个压缩包:

```bash
key-unpack extract <archive>
key-unpack extract <archive1> <archive2>
key-unpack extract <archive> --output <dir>
key-unpack extract <archive> --overwrite rename
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

配置自定义 7-Zip 路径:

```bash
key-unpack config set sevenzip_path <path-to-7zip>
```

常用解压选项:

```bash
key-unpack extract <archive> --sevenzip <path-to-7zip>
key-unpack extract <archive> --temp-dir <dir>
key-unpack extract <archive> --no-stego
key-unpack extract <archive> --no-encoding-compat
key-unpack extract <archive> --json
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

密码记录以明文 JSONL 保存。该工具只适合保存公开资源解压密码, 不应保存私人
账号密码或其他敏感凭据。成功解压日志默认也可能记录命中的密码。

## 退出码

- `0`: 所有请求的操作均成功.
- `1`: 至少一个解压任务失败.
- `2`: 命令参数或配置错误.
- `130`: 用户中断.

## 开发约束

- core 不依赖 UI 库.
- 7-Zip 必须使用参数数组调用, 不拼接 shell 命令.
- 解压先写入任务临时目录, 再整理到最终输出目录.
- v1 不做自动多层解压.
