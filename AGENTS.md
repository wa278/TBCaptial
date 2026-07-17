# TBCaptial Agent Guide

本文件是仓库内自动化 Agent 的工作说明。修改代码前先阅读 `README.md`、本文件，以及与任务
直接相关的设计或操作文档。仓库名保留历史拼写 `TBCaptial`，Python 包名和 Conda 环境名均为
`tbcaptial`，不要擅自重命名。

## 项目目标与边界

TBCaptial 是面向 A 股日频研究和回测的 Python 模块化单体。第一阶段数据流为：

```text
AKShare（主）/ Tushare（辅）
  -> Raw
  -> Silver
  -> 不可变 snapshot
  -> TBCaptial 策略/目标持仓
  -> AKQuant backend + A 股执行约束
  -> 回测产物
```

当前仓库已经具备 Conda 环境、AKShare Raw 小切片下载与校验脚本，以及最小 AKQuant `v0.3.2`
回测 adapter。Silver、正式 snapshot publisher、完整数据质量门和端到端 CLI 尚未全部实现；不要
把 Raw download manifest 描述成正式 snapshot。

第一阶段只做单机本地文件、A 股现货日频、只做多和下一交易日开盘执行。不要引入微服务、
云存储、实盘交易、分钟/Tick、做空或杠杆，除非设计文档和用户任务明确扩展范围。

## 仓库目录与文件职责

### 根目录文档

- `README.md`：项目入口、范围、架构摘要、环境约定和文档导航。
- `OPERATIONS.md`：面向操作者的可复制命令；环境、AKQuant 构建/验收、数据下载/校验和东财断连处理以此为准。
- `PLAN_LATEST.md`：第一阶段唯一执行计划；实现顺序、接口、数据契约和回测语义的当前依据。
- `PLAN.md`：长期架构背景、里程碑、风险和后续阶段，不替代 `PLAN_LATEST.md` 的近期决策。
- `ARCHITECTURE.txt`：ASCII 系统拓扑、数据生命周期、回测事件流和复现边界。
- `ENVIRONMENT_ACCEPTANCE.md`：当前开发机已验收的 Conda、Python 和依赖版本事实。
- `AGENTS.md`：本文件；约束 Agent 的仓库操作和交付标准。

### 根目录配置

- `environment.yml`：唯一依赖源声明；固定 `tbcaptial`、Python 3.11 和直接依赖。
- `pyproject.toml`：包元数据及 Ruff、Mypy、Pytest 配置；不作为第二套依赖清单。
- `Makefile`：稳定的短命令入口。新增可操作工作流时，同时提供 `scripts/` 入口和 Make target。
- `.env.example`：环境变量名称示例，不得写真实 Token、Cookie、代理凭据或机器秘密。
- `.gitignore`：忽略本地环境缓存、构建产物和 `var/` 数据；不要强制提交这些文件。

### `scripts/`

- `create_conda_env.sh`：发现或引导安装 Miniforge，幂等创建/更新 `tbcaptial` 并执行 smoke test。
- `activate_conda_env.sh`：在当前 shell 激活环境；必须通过 `source` 使用。
- `verify_conda_env.py`：离线验证 Python、关键包、ZSTD Parquet 和 DuckDB。
- `init_submodules.sh`：初始化 AKQuant submodule，校验固定 remote/commit 和干净工作区。
- `install_akquant_backend.sh`：校验 AKQuant submodule commit，构建固定 wheel、安装并记录制品。
- `download_akshare_data.sh`：AKShare 下载的一条命令入口；定位项目、激活环境后调用 Python。
- `download_akshare_data.py`：下载交易日历、P0 股票日线和基准，保留 Raw、请求信息和 hash；
  东财失败时按参数熔断到新浪。
- `preview_akshare_data.sh`：数据预览的一条命令入口；自动激活环境并传递筛选参数。
- `preview_akshare_data.py`：按明确 download manifest 只读预览 Parquet，支持数据集、股票、头尾
  和行数筛选，不跨 Raw 批次隐式拼接。
- `verify_akshare_download.sh`：下载校验的一条命令入口；自动激活环境。
- `verify_akshare_download.py`：验证 download manifest、路径边界、文件大小、SHA-256 和 Parquet 行数。

Shell wrapper 应只负责定位仓库、激活环境和传递参数；业务、校验和 manifest 逻辑放在 Python。
所有新增 shell 文件使用 `set -euo pipefail`，所有用户参数使用 `"$@"` 原样传递。

### `src/tbcaptial/`

- `__init__.py`：顶层包和版本。
- `backtest/__init__.py`：回测公共类型的显式导出面。
- `backtest/akquant_backend.py`：固定 AKQuant `v0.3.2` adapter；定义不可变 bar/slice、受控策略
  context、目标持仓/权重桥接、A 股费用配置和 backend-neutral 结果。

只有内部 adapter/bridge 可以依赖或继承 AKQuant 类型。策略和未来的领域层不得直接 import
AKQuant。公共接口不接受“最新目录”或未来全量 DataFrame 作为正式数据边界；正式路径最终只接收
已经校验的 `snapshot_id`。

### `tests/`

- `tests/backtest/test_akquant_backend.py`：目标持仓、次日开盘成交、整手、费用、完整横截面和策略
  contract 测试。
- `tests/backtest/test_akquant_china_market.py`：直接验证 AKQuant 中国市场 T+1 可卖数量语义。
- `tests/data/test_akshare_scripts.py`：离线验证东财失败后的任务级熔断、新浪 fallback、Raw manifest
  和下载校验器。

新增模块应在 `tests/` 下建立对应目录。联网 provider 测试不能作为默认离线 test suite 的硬依赖；
使用固定 fixture 测转换和质量规则，把真实联网探测放在显式脚本中。

### `var/`（运行时，本地且不进 Git）

- `var/data/raw/`：AKShare/Tushare 原始响应及请求、响应、批次清单。
- `var/data/manifests/downloads/`：Raw 下载 run manifest；不是正式 snapshot。
- `var/data/staging/`：同一文件系统的未发布临时写入。
- `var/data/silver/`：未来的标准化事实数据。
- `var/data/snapshots/`：未来的不可变正式 snapshot manifest。
- `var/catalog/`：可从 manifest 重建的本地 DuckDB catalog。
- `var/vendor/akquant/`：本机构建并固定的 AKQuant wheel。
- `var/runs/`：本地回测运行产物。

Raw、已发布 Silver 和被 manifest 引用的文件视为不可变。上游修订必须创建新批次/版本，不得
原地覆盖；不要删除用户已有数据或清理 `var/`，除非用户明确要求且引用关系已验证。

## 唯一支持的操作入口

```bash
# 创建/更新环境并安装固定 AKQuant backend
make setup

# 单独初始化并校验 AKQuant submodule
make submodules

# 创建/更新环境
make env

# 验证环境
make env-verify

# 当前终端进入环境
source scripts/activate_conda_env.sh

# 下载默认 AKShare P0 切片
make akshare-download

# 验证最新下载
make akshare-verify

# 预览最新下载
make akshare-preview

# 安装固定 AKQuant backend
make akquant-backend

# 离线测试
make test

# Ruff、format check 和 Mypy
make quality

# 环境、测试和质量总验收
make acceptance
```

自定义下载参数通过脚本直接传递，或使用 `AKSHARE_ARGS`：

```bash
./scripts/download_akshare_data.sh --daily-source sina --start-date 20240101
make akshare-download AKSHARE_ARGS="--daily-source sina --symbols 000001 600000"
```

任何新增的用户可执行能力都必须：

1. 有 `scripts/` 下的稳定入口；
2. 自动发现/激活正确环境，不依赖用户猜测解释器；
3. 参数可由命令行传入，失败返回非零状态；
4. 在 `OPERATIONS.md` 中给出可复制示例；
5. 适合时增加 Make target；
6. 运行后输出产物路径和可核验摘要。

## 数据源与网络规则

- 只有 ingestion 代码可以访问 AKShare/Tushare；策略和回测不得联网。
- AKShare 是主源；Tushare 只能显式补充、交叉校验或整分区 fallback，不能逐行静默混源。
- 每个 Raw 批次记录实际 provider、endpoint、非敏感参数、版本、时间、重试和 hash。
- `stock_zh_a_hist` 的东财端点可能主动断连。默认采用低频重试、单次任务熔断和 AKShare 新浪
  `stock_zh_a_daily` fallback；manifest 必须记录实际 endpoint。
- 不默认安装第三方代理补丁，不把流量、Cookie 或 Token 发往陌生网关；不要设置
  `verify=False` 绕过 TLS。
- 控制请求频率，优先增量更新和本地缓存。扩大到全市场前必须加入断点续传、配额、质量门和
  明确的数据授权。

## 环境和依赖规则

- Conda 是唯一支持的环境入口；不要新增 `requirements.txt`、Poetry、Pipenv 或项目 `.venv`。
- 新直接依赖只修改 `environment.yml`；Conda 可用的包优先走 `conda-forge`，pip 子段保持最后。
- Python 保持 3.11 系列，除非完成兼容测试并更新环境验收记录。
- AKQuant 固定 `v0.3.2` 和 commit `2924e0cff36669a3563ffb5cb139da0ba9254045`；不得用未固定
  最新版替换。
- 不把 `/Users/wa/...` 等机器绝对路径写进源代码或部署契约；验收文档可记录本机事实。

## 编码和验证要求

- Python 目标版本 3.11，行长 100；遵循 `pyproject.toml` 的 Ruff 和严格 Mypy 配置。
- 公共状态优先使用不可变 dataclass、显式类型和明确异常；不要静默吞掉 schema 或 provider 变化。
- 金额、成交量和复权单位必须按 provider 分别转换；Raw 层不做业务修正。
- 时间边界必须明确。T 日收盘信号只能影响 T+1 开盘，不得引入未来函数。
- A 股执行语义至少保留 T+1、100 股整数手、停牌/涨跌停拒单和带生效日期的费用规则。
- 修改前检查工作树，保留用户无关改动。不要用 destructive Git 命令或覆盖已有数据。

提交或交付前按改动范围运行：

```bash
source scripts/activate_conda_env.sh
ruff check .
mypy src
pytest
bash -n scripts/*.sh
```

数据脚本改动还要运行：

```bash
./scripts/download_akshare_data.sh --help
./scripts/verify_akshare_download.sh
```

涉及真实 provider 行为时，先用 1–3 只股票的小切片验收；不要为测试直接触发全市场高并发。

## 完成定义

一项任务只有同时满足以下条件才算完成：

- 实现与 `PLAN_LATEST.md` 当前边界一致；
- 用户操作有脚本入口和 `OPERATIONS.md` 说明；
- 离线测试和相关 smoke test 通过；
- 数据产物有路径、行数和 hash/manifest，可区分 Raw 与正式 snapshot；
- fallback、警告和已知限制被显式记录；
- 没有覆盖用户改动、泄露秘密或提交本地市场数据。
