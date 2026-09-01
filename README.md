# amazingdata-macos

在 Apple Silicon Mac 上调用银河证券 AmazingData / TGW 数据接口。官方 wheel 在
本地 Docker 网关中运行，业务 Python 通过 `127.0.0.1:8765` 调用数据。

## 特点

- 默认兼容 `BaseData`、`InfoData`、`MarketData` 等常用 SDK 调用，并返回 pandas DataFrame。
- 支持通用查询和实时行情 WebSocket。
- Docker、Debian 与 Python 依赖默认使用国内镜像。
- wheel、账号、密码和数据缓存均不会进入 Git。

## 前置条件

- Apple Silicon Mac。
- Docker Desktop，或 Colima + Docker CLI。
- 从官方渠道取得 `tgw-*.whl` 与 `AmazingData-*.whl`。
- 有效的 TGW 账号、密码、服务器地址、端口和相应数据权限。

本仓库不包含、也不授权重新分发官方 SDK wheel。

使用 Homebrew 时：

```sh
brew install colima docker docker-compose docker-buildx
```

在 `~/.docker/config.json` 加入插件目录：

```json
{
  "cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]
}
```

Docker Desktop 已自带 Compose 和 Buildx。

## 一键准备

```sh
git clone https://github.com/ykk648/amazingdata-macos.git
cd amazingdata-macos

./scripts/bootstrap.sh \
  /path/to/tgw-1.0.9.2-py3-none-any.whl \
  /path/to/AmazingData-1.1.9-cp314-none-any.whl
```

脚本会保存 wheel、创建 `.env`，并构建启动网关。

编辑 `.env`：

```dotenv
TGW_USER=your-account
TGW_PASSWORD=your-password
TGW_HOST=your-server
TGW_PORT=8600
```

配置完成后重启并检查：

```sh
./scripts/manage.sh restart
./scripts/manage.sh health
```

API 文档在 `http://127.0.0.1:8765/docs`。

## 使用

在你的 Python 项目中安装：

```sh
cd /path/to/your-python-project
python -m pip install -e /path/to/amazingdata-macos
```

使用 uv：

```sh
uv add --editable /path/to/amazingdata-macos
```

常用调用保持 SDK 风格：

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

也可使用客户端：

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

需要网关的 `rows` 等元数据时：

```python
envelope = client.query("BaseData", "get_calendar", market="SH", raw=True)
```

## 实时行情

安装实时行情依赖：

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

在 `.env` 的 `AMAZINGDATA_SUBSCRIBE_CODES` 配置订阅代码全集；更新后重启网关。

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

- Docker 端口仅绑定到 `127.0.0.1`。
- `.env`、wheel、本地数据和服务状态均被 `.gitignore` 排除。
- 可在 `.env` 设置 `AMAZINGDATA_API_KEY`，客户端读取同名环境变量。
- 不要把 TGW 端口或本网关直接暴露到公网。
- 本项目是非官方社区项目，与中国银河证券股份有限公司无隶属或背书关系。
- 项目代码使用 MIT License；官方 SDK 继续受其自身许可约束。

参考项目：[lamtinlok/Amazingdata-HTTP-Gateway](https://github.com/lamtinlok/Amazingdata-HTTP-Gateway)。
本仓库重新实现网关和 Mac 客户端，不包含参考项目的 GPL 源代码。
