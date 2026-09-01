# amazingdata-macos

在 Apple Silicon Mac 上原生调用银河证券 AmazingData / TGW 数据接口。

官方 SDK 的二进制只支持 Windows 和 Linux x86_64。本项目把官方 SDK 放在一个
本地 Linux amd64 容器中，Mac 上的研究、交易和数据处理代码仍由原生 Python、
IDE 或虚拟环境运行，通过 `127.0.0.1` 的 HTTP/WebSocket 网关访问数据。

```text
macOS Python project
        |
        | HTTP / WebSocket (127.0.0.1:8765)
        v
Docker gateway: Python 3.14 + AmazingData + TGW (linux/amd64)
        |
        v
China Galaxy Securities data service
```

## 特点

- 业务 Python 程序不进入 Docker，项目文件也不需要挂载进容器。
- 提供接近官方习惯的 `BaseData / InfoData / MarketData` 调用方式。
- 支持通用查询、接口签名发现和实时行情 WebSocket。
- Apple Silicon 使用 Colima/Docker + Rosetta 运行官方 x86_64 库。
- Docker、Debian 和 Python 依赖默认使用国内镜像。
- 已验证的容器依赖版本被锁定，其他 Mac 的构建结果保持一致。
- 私有 wheel、账号、密码和数据缓存均不会进入 Git。

## 前置条件

- Apple Silicon Mac，建议 macOS 13 或更高版本。
- Docker Desktop，或 Colima + Docker CLI。
- 从官方渠道取得以下两个 wheel：
  - `tgw-*.whl`
  - `AmazingData-*.whl`
- 有效的 TGW 账号、密码、服务器地址、端口和相应数据权限。

本仓库不包含、也不授权重新分发官方 SDK wheel。

## 一键准备

```sh
git clone https://github.com/ykk648/amazingdata-macos.git
cd amazingdata-macos

./scripts/bootstrap.sh \
  /path/to/tgw-1.0.9.2-py3-none-any.whl \
  /path/to/AmazingData-1.1.9-cp314-none-any.whl
```

脚本会：

1. 将 wheel 复制到被 Git 忽略的 `vendor/`。
2. 创建 `.env` 配置模板。
3. 在需要时用 Rosetta 启动 Colima。
4. 构建并启动本地网关。

编辑 `.env`：

```dotenv
TGW_USER=your-account
TGW_PASSWORD=your-password
TGW_HOST=your-server
TGW_PORT=8600
```

重新创建服务：

```sh
./scripts/manage.sh restart
./scripts/manage.sh health
```

API 文档在 `http://127.0.0.1:8765/docs`。

## 在 Mac 项目中安装客户端

进入你自己的 Python 项目，而不是网关目录：

```sh
cd /path/to/your-python-project
python -m pip install -e /path/to/amazingdata-macos
```

使用 uv：

```sh
uv add --editable /path/to/amazingdata-macos
```

此后业务代码由 Mac 原生 Python 执行：

```python
import amazingdata_macos as ad

print("ready:", ad.login())

calendar = ad.BaseData().get_calendar(market="SH")
codes = ad.BaseData().get_code_list(security_type="EXTRA_ETF")
bars = ad.MarketData().query_kline(
    code_list=["510300.SH"],
    begin_date=20260101,
    end_date=20260131,
    period="day",
)
```

显式客户端写法：

```python
from amazingdata_macos import Client

client = Client("http://127.0.0.1:8765")
calendar = client.base_data.get_calendar(market="SH")
result = client.query(
    "InfoData",
    "get_margin_summary",
    begin_date=20260101,
    end_date=20260131,
)
```

客户端默认返回 SDK 数据本身。需要网关的 `rows` 等元数据时：

```python
envelope = client.query("BaseData", "get_calendar", market="SH", raw=True)
```

## 实时行情

安装可选 WebSocket 依赖：

```sh
python -m pip install -e '/path/to/amazingdata-macos[stream]'
```

```python
import asyncio
import amazingdata_macos as ad


async def main():
    ad.login()
    async with ad.subscribe(["510300.SH"], period="snapshot") as stream:
        async for tick in stream:
            print(tick)


asyncio.run(main())
```

官方 SDK 的实时订阅在同一进程中只能可靠维持一个底层订阅集合。生产使用时建议在
`.env` 的 `AMAZINGDATA_SUBSCRIBE_CODES` 中预先配置稳定的代码全集；每个 Mac 客户端
可以从这个全集中选择自己的子集。扩展全集后需重启网关。

## 运维命令

```sh
./scripts/manage.sh status
./scripts/manage.sh logs
./scripts/manage.sh health
./scripts/manage.sh restart
./scripts/manage.sh stop
```

不用时可进一步停止 Colima：

```sh
colima stop
```

## 安全与开源

- Docker 端口只绑定到 Mac 的 `127.0.0.1`。
- `.env`、wheel、本地数据和服务状态均被 `.gitignore` 排除。
- 可在 `.env` 设置 `AMAZINGDATA_API_KEY`；Mac 客户端读取同名环境变量。
- 不要把 TGW 端口或本网关直接暴露到公网。
- 本项目是非官方社区项目，与中国银河证券股份有限公司无隶属或背书关系。
- 项目代码使用 MIT License；官方 SDK 继续受其自身许可约束。

参考项目：[lamtinlok/Amazingdata-HTTP-Gateway](https://github.com/lamtinlok/Amazingdata-HTTP-Gateway)。
本仓库重新实现网关和 Mac 客户端，不包含参考项目的 GPL 源代码。
