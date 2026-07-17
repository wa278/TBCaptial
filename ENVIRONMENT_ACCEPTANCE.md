# TBCaptial Conda 环境验收记录

状态：PASS（当前开发机环境 + AKQuant backend 基线）

验收日期：2026-07-17（Asia/Shanghai）

## 验收范围

本记录只验收当前实际开发机。按当前决定，不执行 `linux-64`、`osx-arm64` 或 `win-64` 的预求解和试装。新机器部署时重新运行同一创建脚本和 smoke test，并追加独立验收记录。

本次验收覆盖 Conda 基础环境、AKShare 主数据源依赖、Tushare 辅助依赖、本地 Parquet/DuckDB 存储、开发工具，以及从固定源码构建的 AKQuant `v0.3.2` backend。AKQuant 的源码绝对路径只作为本机验收事实，不进入环境声明；新平台按同一 tag/commit 重新构建并记录平台 wheel hash。

## 主机和 Conda

| 项目 | 实测值 |
| --- | --- |
| 操作系统 | macOS 14.5（23F79） |
| 架构 | x86_64 / `osx-64` |
| Miniforge | `26.3.2-2` |
| 安装器 SHA-256 | `a755192103de19bb2782685ac78820c2e00702e5f33e6e4f0a3bf3c214f45d69` |
| Conda | 26.3.2 |
| 环境名 | `tbcaptial` |
| 本机环境路径 | `/Users/wa/miniforge3/envs/tbcaptial` |
| Python | 3.11.15 |

绝对路径只记录本机事实，不是项目部署契约。

## 关键包实测版本

| 类别 | 包 | 版本 |
| --- | --- | --- |
| 主数据源 | AKShare | 1.18.64 |
| 辅助数据源 | Tushare | 1.4.29 |
| 本地查询 | DuckDB | 1.5.4 |
| Parquet | PyArrow | 22.0.0 |
| 表格/数值 | pandas / NumPy | 2.3.3 / 2.4.6 |
| 回测 backend | AKQuant | 0.3.2 |
| backend 运行 | Polars / Plotly / tqdm | 1.42.1 / 6.9.0 / 4.68.4 |
| backend 构建 | Rust / Maturin | 1.97.1 / 1.14.1 |
| 配置 | Pydantic / pydantic-settings | 2.13.4 / 2.14.2 |
| 测试 | Pytest / Hypothesis | 9.1.1 / 6.156.6 |
| 质量 | Ruff / Mypy | 0.15.22 / 1.20.2 |
| 运行辅助 | PyYAML / Tenacity / Typer | 6.0.3 / 9.1.4 / 0.27.0 |

`boto3` 和 `botocore` 均未安装；验收不访问云存储或云数据库。

## 验收项目

| 项目 | 结果 | 判定 |
| --- | --- | --- |
| Miniforge 安装器官方 SHA-256 校验 | PASS | 安装前 hash 一致 |
| 从 `environment.yml` 创建环境 | PASS | 新环境创建成功 |
| 重复执行创建脚本 | PASS | `env update --prune` 成功，具备幂等更新行为 |
| Bash/Zsh 脚本语法 | PASS | 两种 shell 均通过语法检查 |
| Bash/Zsh source 激活 | PASS | 环境名和 Python 路径正确 |
| 直接执行激活脚本 | PASS | 按设计拒绝并提示必须使用 `source` |
| 核心包 import | PASS | 数据、存储、配置、测试和质量包全部可导入 |
| ZSTD Parquet 写入/读取 | PASS | schema、行数和数据一致 |
| 本地 DuckDB 文件查询 | PASS | 能创建、查询和关闭本地数据库文件 |
| 创建脚本内置离线 smoke test | PASS | 创建和更新路径均自动执行成功 |
| AKQuant 源码身份 | PASS | tag `v0.3.2`、commit `2924e0cf...4045`、干净工作区 |
| AKQuant release wheel 构建/安装 | PASS | CPython abi3 `osx-64` wheel 可导入，版本为 0.3.2 |
| AKQuant wheel 重复构建 | PASS | 固定 commit epoch 后连续两次 SHA-256 均为 `a84c1e27...5fad` |
| 回测 backend 契约测试 | PASS | 真实 backend 的 next-open、T+1、整手、费用、多标的确定性共 9 项通过 |
| Python 质量门 | PASS | Ruff lint/format check 与严格 Mypy 通过 |

受限执行环境中 PyArrow 读取 CPU cache/指令信息时会打印 `sysctlbyname` 权限 warning，但 Parquet 与 DuckDB 验收成功；该 warning 不改变测试结果。

## 使用命令

```bash
./scripts/create_conda_env.sh
./scripts/install_akquant_backend.sh
source scripts/activate_conda_env.sh
make acceptance
```

激活脚本必须 `source`，不能作为子进程直接执行。

## 当前声明 hash

| 文件 | SHA-256 |
| --- | --- |
| `environment.yml` | `15a3711b11e8051ec49d333b3e5842166547f075ce47ce4e19f1a9b8ca6ddf18` |
| `scripts/create_conda_env.sh` | `22310ce3622441789811d8407c50135192c9cf844bb2dd8027b6f0a9453c5a9c` |
| `scripts/activate_conda_env.sh` | `28e4b4c7e88f00feb96114e7310190c5fbf251cadd06d73736234454c9c19409` |
| `scripts/verify_conda_env.py` | `198eceeb8d60e7182123bf76e4c2e64e3446f43ccc53740e75a84ef12c90440f` |
| `scripts/install_akquant_backend.sh` | `b7188fa17fdc5109de3f4165bfd5759b99a39737706f70d0028aad981f55473e` |
| 本机 AKQuant wheel | `a84c1e279738b0b15471d0182135a3307dd9b2b4a32a33c09148b4355b685fad` |

创建脚本在本记录生成后若有修改，正式运行以 Git commit、实时文件 hash 和当次 `conda list --explicit` 为准；本表只对应本次验收基线。
