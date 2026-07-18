# TBCaptial 操作指南

本指南覆盖当前可执行能力：创建和激活 Conda 环境、验证环境、下载 AKShare
数据、验证下载产物、运行三因子本地研究回测，以及处理东方财富接口断连。

所有正式入口都位于 `scripts/`，Makefile 只负责提供短命令。Python 脚本不要求用户在
命令行临时拼接代码。

## 1. 快速开始

在仓库根目录运行：

```bash
make setup
make acceptance
make akshare-download
make akshare-verify
make factor-backtests
```

五条命令依次完成：

1. 初始化固定 AKQuant submodule，发现或安装 Miniforge，创建/更新环境并构建 backend；
2. 运行环境、真实回测后端测试和静态检查；
3. 激活环境并下载默认 AKShare 数据切片；
4. 重新计算文件大小、SHA-256 和 Parquet 行数，验证最新下载清单；
5. 读取已校验 Raw 清单，运行三个因子示例并生成离线图形报告。

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

AKQuant 通过 Git submodule 固定在 TBCaptial 仓库内：

```text
TBCaptial/
└── third_party/
    └── akquant/  # git@github.com:wa278/akquant.git
```

本项目只接受 commit `2924e0cff36669a3563ffb5cb139da0ba9254045`，构建后的 Python 包版本
必须是 `0.3.2`；不依赖 Git tag。新机器推荐直接递归克隆：

```bash
git clone --recurse-submodules git@github.com:wa278/TBCaptial.git
cd TBCaptial
```

已有普通 clone 使用下面的幂等入口初始化并校验 submodule：

```bash
make submodules
```

然后构建、安装并核验：

```bash
make akquant-backend
source scripts/activate_conda_env.sh
python -c 'import akquant; print(akquant.__version__)'
```

初始化脚本检查 submodule URL、commit 和干净工作区；安装脚本再次检查 remote/commit，使用
commit 时间固定 wheel 的 `SOURCE_DATE_EPOCH`，输出 SHA-256，并把本机 wheel 放到
`var/vendor/akquant/`。wheel 是平台相关产物，不提交 Git。

第一次构建需要从 crates.io 下载 Rust/Polars 依赖，耗时明显长于增量构建。

### 2.5 一条命令完成环境和 backend

```bash
make setup
```

该命令按顺序完成 submodule 初始化、Conda 环境创建/更新和 AKQuant backend 构建安装。

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

### 4.1 补齐公司、公司行为和财务历史

日线行情接口不会返回季度财务、分红、股本和公司资料。对本地已经存在日线的全部股票执行
可续跑扩展下载：

```bash
make akshare-complete
```

扩展表包括公司概况、IPO 信息、历史分红、股本变更、行业变更、东方财富的报告期/单季度主要
财务指标和主营构成，以及新浪的资产负债表、利润表和现金流量表。三大财务报表保留各自完整
历史和原始宽字段，不会错误复制到每个交易日。不同东财接口使用不同域名；脚本默认 2 秒节流，
不能因某一历史行情域名断连而假定全部东财接口都不可用或都可用。只跑一只股票或指定数据集：

```bash
./scripts/download_akshare_complete.sh \
  --symbols 000001 \
  --datasets profile dividend financial_indicator_em main_business_em \
  balance_sheet income_statement cash_flow
```

脚本默认扫描本地日线请求得到股票集合，并按“接口 + 参数”跳过已经成功落盘的相同任务，因而
可以安全重跑。每个任务立即写入不可变 Raw；部分接口失败时继续其他任务，并在
`var/data/manifests/enrichment/` 写入状态为 `PARTIAL` 的清单和失败原因。修复网络后直接再次运行
同一命令即可只补失败项。

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

## 6. 预览数据

预览最新一次完成下载的每个数据集，默认显示最后 5 行：

```bash
make akshare-preview
```

查看本地完成的下载任务：

```bash
./scripts/preview_akshare_data.sh --list-runs
```

预览指定股票的最新 10 行：

```bash
./scripts/preview_akshare_data.sh --dataset daily --symbol 000001 --rows 10
```

显示最早的数据，或选择明确的下载清单：

```bash
./scripts/preview_akshare_data.sh --dataset calendar --head --rows 10
./scripts/preview_akshare_data.sh \
  --manifest var/data/manifests/downloads/<run_id>.json \
  --dataset benchmark --rows 5
```

预览某次公司/财务扩展清单中的指定股票：

```bash
./scripts/preview_akshare_data.sh \
  --manifest var/data/manifests/enrichment/<run_id>.json \
  --dataset enrichment --symbol 000001 --rows 3
```

宽财务表默认最多显示 20 列；明确查看全部列时增加 `--max-columns 0`。

预览器只读取 manifest 明确列出的 Parquet，不合并多个 Raw run，也不修改数据。默认选择最新完成
的 run；正式研究仍应等待 Silver 和 snapshot，而不是直接依赖 Raw 预览结果。

汇总本地全部 AKShare Raw（包括中途任务已经成功落盘、但未进入完成清单的批次）：

```bash
make akshare-summary
```

机器可读的 JSON 输出：

```bash
./scripts/summarize_akshare_data.sh --json
```

汇总器按 `股票代码 + 交易日期` 计算日线有效去重行数，同时保留物理 Raw 行数、接口分布、
完整下载任务数、日期范围、文件占用和孤立 Raw 批次统计。它是只读盘点工具，不会合并、覆盖或
删除不可变 Raw；孤立批次仍需后续 Silver 发布流程显式选择，不能视为正式 snapshot。

## 7. 三因子本地研究回测

### 7.1 运行前提

先完成 Conda/AKQuant 安装，并确认本地有可用的 Raw 下载清单：

```bash
make setup
make akshare-verify
```

回测脚本只读本地文件，运行期间不访问 AKShare、Tushare 或其他网络数据源。不指定
`--manifest` 时，它会选择时间最新、状态为 `COMPLETED`、且至少包含 3 只每只不少于
120 行日线的 Raw download manifest，不会把多次 Raw 下载隐式拼接在一起。下载仍在进行时，
“最新”输入可能变化；需要复现的研究必须显式传入 manifest，生成的 run manifest 也会固定记录
实际输入路径、run id 和 SHA-256。

### 7.2 运行默认切片

```bash
make factor-backtests
```

一条等价的直接入口：

```bash
./scripts/run_factor_backtests.sh
```

脚本会打印实际输入清单、股票集合、日期区间、输入警告、三个策略的指标摘要、产物目录和
`report.html` 路径。

### 7.3 固定输入和参数

可复现的研究记录应显式指定下载清单：

```bash
./scripts/run_factor_backtests.sh \
  --manifest var/data/manifests/downloads/<run_id>.json
```

限定日期和初始资金：

```bash
./scripts/run_factor_backtests.sh \
  --manifest var/data/manifests/downloads/<run_id>.json \
  --start-date 2024-06-01 \
  --end-date 2026-06-30 \
  --initial-cash 1000000
```

也可通过 Make 传递参数：

```bash
make factor-backtests \
  FACTOR_BACKTEST_ARGS="--manifest var/data/manifests/downloads/<run_id>.json --start-date 2024-06-01"
```

默认起始日为 2023-08-28，这是当前固定 0.05% 卖出印花税参数的生效日；指定更早日期会被
费用语义门禁拒绝，待后续支持分段生效费率后才能覆盖更早时期。指定区间按基准交易日历建立
完整横截面，manifest 中全部股票都必须覆盖同一首尾范围，且至少需要 120 个观测日。股票内部
缺失日会以昨收 OHLC、`volume=0` 的不可交易占位 bar 表示并计入警告，而不会删除其他股票的
交易日。可用参数以脚本的 `--help` 输出为准：

```bash
./scripts/run_factor_backtests.sh --help
```

### 7.4 策略和成交语义

| 策略 | 信号与目标权重 | 调仓周期 |
| --- | --- | --- |
| `momentum_60d` | 60 日横截面收益；选取收益为正且最强的 1 只，目标仓位 90% | 20 交易日 |
| `reversal_5d` | 5 日横截面反转；选取近期收益为负且跌幅最大的 1 只，目标仓位 85% | 5 交易日 |
| `low_volatility_20d` | 全部标的按 20 日实现波动率的倒数分配，总目标仓位 90% | 20 交易日 |

策略只使用当前及历史收盘价，T 日收盘后提交目标权重，AKQuant 在下一事件的开盘价尝试成交。
公共执行设置包含 T+1、100 股整手和显式交易费用；策略将总目标仓位限制为
85% 或 90%，其余保留为现金缓冲。任何因子回看窗口只要含 `volume=0` 占位 bar，该标的就不
参与当次排序。由于 next-event 模式下卖单资金要到下一开盘成交后才可用，同一收盘提交的换仓
买单仍可能被资金风控拒绝；策略会在下一收盘重申同一目标一次，并在再下一开盘尝试成交，不会
使用下一日价格重算该次目标。报告按实际成交计算，拒单与期末未完成订单必须单独审阅。沪深 300
只用作净值对比，不进入可交易股票池。当前 `volume_limit_pct=1.0`，即最多使用整根日线成交量，
且未加滑点；两项都会写入 run manifest，但仍是偏乐观的演示参数。

### 7.5 产物和图形报告

每次运行原子发布到 `var/runs/factors/<run_id>/`：

```text
var/runs/factors/<run_id>/
├── report.html
├── manifest.json
├── metrics.csv
├── metrics.parquet
├── equity_curves.parquet
├── drawdowns.parquet
└── strategies/
    ├── momentum_60d/
    ├── reversal_5d/
    └── low_volatility_20d/
```

`report.html` 内嵌 Plotly JavaScript，可以断网后直接用浏览器打开，包含因子和基准的归一化
净值、回撤和绩效摘要表。`metrics.csv` 适合直接阅读，其中同时记录订单、成交、拒单和期末未
完成订单数量；Parquet 用于后续程序化分析。
每个策略目录还包含 `equity.parquet`、`drawdown.parquet`、`positions.parquet`、
`orders.parquet`、`executions.parquet`、`decisions.json` 和 `strategy.json`。

`manifest.json` 保存 Raw 下载 run id 及输入清单 SHA-256、股票/基准/日期区间、执行设置、
策略参数与指标、TBCaptial 代码 commit/工作区状态和源码树 hash、`environment.yml` hash、
Python/关键包版本、AKQuant 版本/commit、警告，以及每个产物的字节数和 SHA-256。当前仍没有
记录完整 Conda 包清单或保存 dirty worktree 的源码副本，因此这不是 `PLAN_LATEST.md` 定义的
正式 run manifest。

### 7.6 必须阅读的限制

当前三因子工作流仅用于验证数据读取、策略调度、AKQuant adapter、绩效计算和产物发布
能否连通。不得把当前结果解读为可投资因子证据，原因包括：

- 输入是 Raw download manifest，只完成文件 hash/行数和基本 OHLCV 校验，没有经过 Silver 标准化、完整质量门和正式 snapshot 发布；
- 股价是未复权价格，现金分红、送转和拆分可被错认为因子收益或损失；
- 每次只读取一个 manifest 中列出的当前样本；即使股票数多于 3，也不是历史时点全市场股票池，同时存在幸存者偏差、人工选样偏差和行业集中；
- `volume=0` 占位 bar 只能保留日历和阻止当日新增交易，不能区分真实停牌与上游数据缺口；
- 高仓位换股可能先拒绝买单、再于下一收盘重申目标，因此实际换仓可延迟到 T+2 开盘，必须结合 `orders.parquet` 审阅；
- 输入缺少完整的历史上市/退市、ST、停复牌、涨跌停、复权因子和公司行为数据，无法构成完整的可交易性仿真；
- 日线开盘成交是简化模型，不代表开盘瞬间深度、排队和实际滑点；
- 样本数和期间过小，策略参数没有样本外验证，任何收益、Sharpe 或超额收益都不具备统计显著性。

在完成 Silver、PASS snapshot、历史时点股票池、公司行为和 A 股执行守卫前，所有结果
只供研究与工程链路验证，不得用于策略挑选、资金分配或实盘决策。

## 8. 东方财富持续断连

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

## 9. 常用命令

```bash
# 环境
make setup
make env
make env-verify
make akquant-backend
source scripts/activate_conda_env.sh

# 数据
make akshare-download
make akshare-preview
make akshare-verify

# 三因子本地研究回测
make factor-backtests

# 强制使用新浪日线，避开东财断连
./scripts/download_akshare_data.sh --daily-source sina

# 项目测试
make test

# 环境 + 测试 + Ruff + Mypy 完整验收
make acceptance
```

## 10. AKQuant 常见问题与升级规则

### 10.1 submodule commit、remote 或工作区不匹配

先做只读检查：

```bash
git submodule status
git -C third_party/akquant remote get-url origin
git -C third_party/akquant rev-parse HEAD
git -C third_party/akquant status --short
```

期望 remote 为 `git@github.com:wa278/akquant.git`，commit 为
`2924e0cff36669a3563ffb5cb139da0ba9254045`，状态无输出。先自行保存需要保留的修改，再运行
`make submodules` 恢复仓库记录的 gitlink；不要为了通过检查删除未确认的工作。

### 10.2 crates.io 下载失败

确认网络、DNS、代理和 `https://index.crates.io` 可访问后重新运行：

```bash
make akquant-backend
```

Cargo 会复用已经下载和编译的缓存。

### 10.3 `import akquant` 失败

```bash
source scripts/activate_conda_env.sh
which python
make akquant-backend
python -c 'import akquant; print(akquant.__version__)'
```

### 10.4 受限 macOS 的 CPU 信息 warning

PyArrow/Polars 可能因沙箱禁止 `sysctlbyname` 而打印 CPU cache/指令 warning。只要
`make acceptance` 最终通过，Parquet/DuckDB 和回测断言都成功，该提示不改变验收结论。

### 10.5 升级规则

不得把版本改成 `latest`。升级必须更新并固定 submodule commit、重建并记录 wheel SHA-256、确认公开
适配层不泄漏 AKQuant 类型、运行全部 backend 契约测试，并在 `PLAN_LATEST.md` 记录行为变化。
任何一项失败都继续使用当前 `v0.3.2`。
