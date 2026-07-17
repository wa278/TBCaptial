# TBCaptial 操作指南

本指南覆盖当前可执行能力：创建和激活 Conda 环境、验证环境、下载 AKShare
数据、验证下载产物，以及处理东方财富接口断连。

所有正式入口都位于 `scripts/`，Makefile 只负责提供短命令。Python 脚本不要求用户在
命令行临时拼接代码。

## 1. 快速开始

在仓库根目录运行：

```bash
make env
make env-verify
make akquant-backend
make acceptance
make akshare-download
make akshare-verify
```

四条命令依次完成：

1. 发现或安装 Miniforge，并创建/更新 `tbcaptial` 环境；
2. 激活环境并执行依赖、Parquet 和 DuckDB smoke test；
3. 从固定源码构建并安装 AKQuant `v0.3.2`；
4. 运行环境、真实回测后端测试和静态检查；
5. 激活环境并下载默认 AKShare 数据切片；
6. 重新计算文件大小、SHA-256 和 Parquet 行数，验证最新下载清单。

## 2. Conda 环境

### 2.1 创建或更新环境

```bash
./scripts/create_conda_env.sh
```

该脚本可重复执行，依赖声明来自 `environment.yml`。项目依赖不会安装到 `base`。

### 2.2 在当前终端进入环境

```bash
source scripts/activate_conda_env.sh
```

激活脚本必须通过 `source` 执行，因为子进程无法改变当前终端的 Conda 环境。成功后会打印
Python 版本和解释器路径。

### 2.3 验证环境

```bash
make env-verify
```

或直接运行脚本入口：

```bash
source scripts/activate_conda_env.sh
python scripts/verify_conda_env.py
```

### 2.4 构建固定 AKQuant backend

推荐目录结构：

```text
workspace/
├── TBCaptial/
└── akquant/
```

本项目只接受 AKQuant `v0.3.2`、commit
`2924e0cff36669a3563ffb5cb139da0ba9254045`。新机器先准备相邻源码：

```bash
git clone https://github.com/akfamily/akquant.git ../akquant
git -C ../akquant checkout --detach 2924e0cff36669a3563ffb5cb139da0ba9254045
git -C ../akquant describe --tags --exact-match
git -C ../akquant status --short
```

第三条应输出 `v0.3.2`，第四条应无输出。然后构建、安装并核验：

```bash
make akquant-backend
source scripts/activate_conda_env.sh
python -c 'import akquant; print(akquant.__version__)'
```

安装脚本拒绝 commit/tag 不符或带未提交修改的源码；使用 commit 时间固定 wheel 的
`SOURCE_DATE_EPOCH`，输出 SHA-256，并把本机 wheel 放到 `var/vendor/akquant/`。wheel 是平台相关
产物，不提交 Git。若源码不在相邻目录：

```bash
AKQUANT_SOURCE_DIR=/absolute/path/to/akquant make akquant-backend
```

第一次构建需要从 crates.io 下载 Rust/Polars 依赖，耗时明显长于增量构建。

### 2.5 一条命令完成环境和 backend

```bash
make setup
```

修改 `environment.yml` 后应重新执行 `make env` 和 `make akquant-backend`，确保固定 wheel 仍安装
在同步后的环境中。不要把临时 `pip install` 当作依赖声明。

### 2.6 回测 backend 验收

```bash
make acceptance
```

它依次执行环境 smoke test、全部离线 Pytest、Ruff lint/format check 和严格 Mypy。回测测试使用
真实 AKQuant 和内存合成行情，不访问 AKShare、Tushare 或网络。也可单独运行：

```bash
pytest -q tests/backtest
pytest -q tests/backtest/test_akquant_backend.py -k next_open
pytest -q tests/backtest/test_akquant_china_market.py -k t_plus_one
```

## 3. 下载 AKShare 数据

### 3.1 默认下载

```bash
./scripts/download_akshare_data.sh
```

等价的 Make 命令：

```bash
make akshare-download
```

Shell 脚本会自动定位仓库、激活 `tbcaptial` 环境，再执行 Python 下载器。默认下载：

- AKShare 交易日历；
- `000001`、`600000`、`300750` 的未复权日线；
- 沪深 300（`sh000300`）基准日线；
- 股票日线起始日期为 `2024-01-01`，结束日期默认为运行当天。

这是 P0 验收切片，不是全市场数据回填。

### 3.2 自定义股票和日期

```bash
./scripts/download_akshare_data.sh \
  --start-date 20230101 \
  --end-date 20261231 \
  --symbols 000001 600000 300750 \
  --benchmark sh000300
```

通过 Make 传参：

```bash
make akshare-download \
  AKSHARE_ARGS="--start-date 20230101 --symbols 000001 600000"
```

查看全部参数：

```bash
./scripts/download_akshare_data.sh --help
```

### 3.3 日线来源策略

默认值是 `--daily-source auto`：

1. 首只股票优先尝试 AKShare 的东方财富 `stock_zh_a_hist`；
2. 达到重试上限仍断连时，触发本次任务的熔断；
3. 当前及后续股票切换到 AKShare 的新浪 `stock_zh_a_daily`；
4. 每个 Raw 批次记录实际 endpoint、参数、重试次数和内容 hash，不静默混源。

强制跳过东方财富：

```bash
./scripts/download_akshare_data.sh --daily-source sina
```

只允许东方财富，失败时终止且不切源：

```bash
./scripts/download_akshare_data.sh --daily-source eastmoney
```

请求默认间隔 2 秒，可调大但不建议设为 0：

```bash
./scripts/download_akshare_data.sh --request-interval 5 --retries 3 --timeout 60
```

## 4. 数据位置与清单

默认数据根目录为 `var/data`，该目录已被 Git 忽略。主要结构：

```text
var/data/
├── raw/source=akshare/endpoint=<name>/ingest_date=<date>/batch=<id>/
│   ├── data.parquet
│   ├── request.json
│   ├── response.json
│   └── manifest.json
├── manifests/downloads/<run_id>.json
└── staging/
```

成功下载后会生成状态为 `COMPLETED` 的 run manifest。中途失败不会生成完成清单；已经写入的
Raw 数据保持不变，重新运行会创建新批次而不是覆盖旧文件。

当前清单只代表 Raw 下载完成，不代表已经完成 Silver 标准化、质量门和正式 snapshot 发布。

## 5. 验证下载结果

验证最新一次完成的下载：

```bash
./scripts/verify_akshare_download.sh
```

等价命令：

```bash
make akshare-verify
```

验证指定清单：

```bash
./scripts/verify_akshare_download.sh \
  --manifest var/data/manifests/downloads/<run_id>.json
```

校验内容包括：

- manifest 中的相对路径不能逃出数据根目录；
- 所有文件存在且字节数一致；
- 所有文件 SHA-256 一致；
- 每个 `data.parquet` 的实际行数与批次清单一致。

## 6. 东方财富持续断连

典型异常是：

```text
ProxyError: HTTPSConnectionPool(host='push2his.eastmoney.com', port=443)
RemoteDisconnected('Remote end closed connection without response')
```

这类异常已多次出现在 AKShare 官方仓库。东财可能对频繁请求、出口 IP 或请求特征临时拒绝
连接；`ProxyError` 也可能表示 Requests 自动使用的环境代理被远端断开，不等于 AKShare 包未
安装。

处理顺序：

1. 默认使用 `auto`，让任务在东财失败后熔断到新浪；
2. 已知东财不可用时直接指定 `--daily-source sina`；
3. 降低调用频率，只做历史回填和日常增量，不重复全量拉取；
4. 若必须使用东财，可等待限制解除后再以 `--daily-source eastmoney` 单独测试；
5. 不关闭 TLS 校验，不把 Cookie、代理账号或 Token 提交到仓库；
6. 大规模或生产用途应采购有服务契约的数据源，并通过 TBCaptial provider adapter 接入。

项目不默认安装第三方代理补丁。AKShare 维护者已提醒谨防 issue 中的代理广告；第三方补丁还
可能要求把请求和授权 Token 发送到外部网关，不适合作为本项目的数据可信边界。

参考：

- [AKShare issue #7069](https://github.com/akfamily/akshare/issues/7069)：与本项目相同的
  `push2his` `ProxyError`；
- [AKShare issue #6986](https://github.com/akfamily/akshare/issues/6986) 和
  [#6100](https://github.com/akfamily/akshare/issues/6100)：短时间重复请求后被主动断开的报告；
- [AKShare issue #7036](https://github.com/akfamily/akshare/issues/7036)：维护者关于代理广告和
  大规模数据源的提醒；
- [Requests 官方文档](https://requests.readthedocs.io/en/latest/user/advanced/#proxies)：默认读取
  `http_proxy`、`https_proxy`、`no_proxy` 和 `all_proxy`。

## 7. 常用命令

```bash
# 环境
make setup
make env
make env-verify
make akquant-backend
source scripts/activate_conda_env.sh

# 数据
make akshare-download
make akshare-verify

# 强制使用新浪日线，避开东财断连
./scripts/download_akshare_data.sh --daily-source sina

# 项目测试
make test

# 环境 + 测试 + Ruff + Mypy 完整验收
make acceptance
```

## 8. AKQuant 常见问题与升级规则

### 8.1 commit、tag 或工作区不匹配

先做只读检查：

```bash
git -C ../akquant rev-parse HEAD
git -C ../akquant describe --tags --exact-match
git -C ../akquant status --short
```

先自行保存需要保留的修改，再恢复到固定的干净 commit；不要为了通过检查删除未确认的工作。

### 8.2 crates.io 下载失败

确认网络、DNS、代理和 `https://index.crates.io` 可访问后重新运行：

```bash
make akquant-backend
```

Cargo 会复用已经下载和编译的缓存。

### 8.3 `import akquant` 失败

```bash
source scripts/activate_conda_env.sh
which python
make akquant-backend
python -c 'import akquant; print(akquant.__version__)'
```

### 8.4 受限 macOS 的 CPU 信息 warning

PyArrow/Polars 可能因沙箱禁止 `sysctlbyname` 而打印 CPU cache/指令 warning。只要
`make acceptance` 最终通过，Parquet/DuckDB 和回测断言都成功，该提示不改变验收结论。

### 8.5 升级规则

不得把版本改成 `latest`。升级必须同时固定 tag/commit、重建并记录 wheel SHA-256、确认公开
适配层不泄漏 AKQuant 类型、运行全部 backend 契约测试，并在 `PLAN_LATEST.md` 记录行为变化。
任何一项失败都继续使用当前 `v0.3.2`。
