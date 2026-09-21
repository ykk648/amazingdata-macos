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

### 交易日历

厂商 SDK 的 `BaseData.get_calendar(date=...)` 把 `date` 当作**日历上界**，而该默认值在
模块导入时就求值一次。进程内省略 `date` 会把日历永久冻结在网关启动当天，`MarketData`
再拿 `self.calendar` 截断 K 线查询区间，于是跨天后日线/5 分钟线会静默少几天，只有重启
网关才恢复。

网关因此始终显式传 `date = max(今天(北京时间), 本次请求的 end_date)`，仅在覆盖不足时
重新取日历。`GET /health` 会暴露排查所需的字段：

```sh
curl -s http://127.0.0.1:8765/health
# trading_day        今天（北京时间 YYYYMMDD）
# calendar_target    网关日历已覆盖到的日期上界
# calendar_last_day  日历中最后一个交易日
# calendar_stale     为 true 表示日历没有覆盖到今天
```

`calendar_stale=true` 说明日历已过期，通常是 TGW 登录异常，可查 `./scripts/manage.sh logs`。

### 单席位账号与 live 部署抢座

TGW 账号有**并发连接上限**（本账号实测为 1）。厂商 `AmazingData.login` 在登录失败且
日志出现 `Connections of this user exceed the max limitation` 时会带 `force_logout=True`
重试 5 次，把已经在线的会话踢掉；**被踢那一侧的原生库会直接结束进程**——退出码 0、
Python 层没有任何 traceback、uvicorn 也没有 shutdown 行，只有
`Remote end closed connection without response` 留给客户端。本机网关与 ECS live 部署
共用同一账号时，双方都默认抢座，于是互相踢号，这就是「故障率很高」的根因。

网关上做了三件事：

1. **不抢座**：`AMAZINGDATA_FORCE_LOGOUT=false`（默认）时，登录只用
   `force_logout=False`，拿不到席位就记录 `seat is held by another host` 并排队等待，
   等对方释放后再登录。`GET /health` 的 `seat_held_by_other` 会置为 true。
2. **空闲让座**：连续 `AMAZINGDATA_IDLE_RELEASE_SECONDS` 秒没有查询（实时订阅或查询
   在飞时不算空闲）就调用 `logout` 释放席位，`released_for_idle=true`、`ready=false`；
   下一次真的来了请求，`/v1/query` 会先自动重新登录。`available` 字段表示「进程会服务
   这次请求」，客户端探活看的是它，所以空闲让座不会被误判成故障。
3. **不因等座自杀**：看门狗把「等 ECS 让座」和「会话坏了」区分开，前者不计失败，
   不会触发 `restart` 循环；`compose.yaml` 同时改成 `restart: unless-stopped`，
   因为原生库被踢时会绕过 Python 直接结束进程，只能靠 Docker 拉起。

座位被别处占用时的状态：

```sh
curl -s http://127.0.0.1:8765/health | python -m json.tool
# ready                     会话是否在线
# available                 是否会服务请求（空闲让座后依然为 true）
# seat_held_by_other        席位被共用账号的另一台机器占着
# released_for_idle         空闲主动让座中
# idle_seconds              距上次查询的空闲秒数
```

`./scripts/manage.sh logs` 里对应的关键行：

```
TGW seat is held by another host sharing this account; waiting instead of forcing a logout
watchdog tick: waiting for the TGW seat held by another host
TGW session released (idle 601s)
```

客户端侧（`data_lib/providers/amazingdata/`）配合做了节流与重试：请求之间默认间隔
`AMAZINGDATA_REQUEST_INTERVAL_MS=400` 毫秒，连接类错误先救网关再退避重试（默认 3 次），
单次请求超时 120 秒（`get_calendar` 首触可能接近 47 秒，60 秒会误判成“SDK 返回 None”
并毒化整批符号）。

**仍然要避免的**：本机研究任务和 ECS 的 09:55 / 14:40 / 16:30 定时任务在同一时段跑。
本机不抢座，所以重叠时本机只会等座到超时，然后由研究档的可失败开关降级。要么错开时间，
要么只在一边跑 AmazingData。

## 安全与开源

- Docker 端口仅绑定到 `127.0.0.1`。
- `.env`、wheel、本地数据和服务状态均被 `.gitignore` 排除。
- 可在 `.env` 设置 `AMAZINGDATA_API_KEY`，客户端读取同名环境变量。
- 不要把 TGW 端口或本网关直接暴露到公网。
- 本项目是非官方社区项目，与中国银河证券股份有限公司无隶属或背书关系。
- 项目代码使用 MIT License；官方 SDK 继续受其自身许可约束。

参考项目：[lamtinlok/Amazingdata-HTTP-Gateway](https://github.com/lamtinlok/Amazingdata-HTTP-Gateway)。
本仓库重新实现网关和 Mac 客户端，不包含参考项目的 GPL 源代码。
