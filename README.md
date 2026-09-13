# 天翼云电脑保活并完成每日任务获取积分

本项目用于在 Docker 容器中保活云电脑使其长期开机，保活不会中断使用，并自动完成积分任务，每天可获取300积分。

## 新版本更新

- **自动兑换奖励**
- **挂机积分任务**
- **优化海外卡顿**
- **定时任务优化**

## 自动兑换奖励

- 支持自动兑换奖励。
- 每天可获取 `300` 积分。
- 升级 `8c16g` 配置需要 `300` 积分。
- 推荐策略：设置每月兑换一次（可使用 `-1` 表示每月最后一天），可长期维持 `8c16g` 配置（长期8c16g状态）。


## 来源说明

本项目中使用的保活程序来自 `CtYun` 项目：

- https://github.com/leleji/CtYun

当前仓库通过基础镜像 `su3817807/ctyun:latest` 使用该程序（容器内运行 `dotnet CtYun.dll`），本仓库主要补充了定时执行积分任务的能力和增加了24小时重启保活程序。

## 项目结构

```text
.
├─ install.sh              # 一键部署入口（安装依赖、下载代码、调用部署脚本）
├─ deploy.sh               # 交互式部署脚本（构建镜像、启动容器）
├─ deploy_cron.sh          # 带 cron 参数的部署脚本（可配置定时任务）
└─ app/
   ├─ Dockerfile           # 运行环境构建与 cron 任务配置
   ├─ entrypoint.sh        # 容器入口：启动 cron + 保活循环运行 CtYun.dll
   ├─ login_script.py      # AI对话积分任务脚本
   └─ pc_login.py          # 云电脑挂机任务 + 自动兑换脚本
```
## 快速开始

### 一键部署（Linux）

通过 SSH 登录服务器，在 **root 终端**中执行（普通用户可先运行 `sudo -i`）：

```bash
curl -fsSL https://raw.githubusercontent.com/agjvrkgj/ctyun-auto/main/install.sh -o "$HOME/ctyun-install.sh" && bash "$HOME/ctyun-install.sh"
```

如果系统尚未安装 `curl`，先执行适合当前系统的命令：

```bash
# Debian / Ubuntu
apt-get update && apt-get install -y curl ca-certificates

# 使用 DNF 的系统
dnf install -y curl ca-certificates
```

脚本会自动安装缺少的 Git、curl、CA 证书和 Docker，将本仓库下载到 `/opt/ctyun-auto`，然后引导输入账号、密码和数据目录，构建镜像并启动容器。密码输入不回显。已有 Docker 会直接复用；使用 systemd 的系统会尝试设置 Docker 开机启动。

依赖安装支持 `apt-get`、`dnf`、`yum`；自动安装 Docker 的系统与版本范围以 [Docker 官方安装脚本](https://github.com/docker/docker-install)为准。服务器需要能访问 GitHub、Docker 软件源及镜像仓库。整个应用的架构支持取决于基础镜像 `su3817807/ctyun:latest`。

更多用法（先执行上面的下载命令）：

```bash
# 自定义挂机任务和 AI 对话任务的执行时间
bash "$HOME/ctyun-install.sh" --cron

# 自定义项目目录和分支
bash "$HOME/ctyun-install.sh" --dir /opt/ctyun-auto --branch main

# 查看帮助
bash "$HOME/ctyun-install.sh" --help
```

重复运行时，仅对来源和分支匹配、没有本地修改的项目进行快进更新；不会强制覆盖代码。已有同名容器会询问是否替换。请继续选择原数据目录，以保留挂载的登录数据；目前兑换配置保存在容器内的 `/app/redeem_config.json`，**替换容器前应单独备份，或在部署后重新配置**。`deploy_cron.sh -y` 保留原有的自动确认行为。

一键部署仍需完成账号输入和可能出现的短信验证。首次登录、兑换配置和积分任务沿用下方的交互流程。

### 手动部署（已安装 Docker）

在项目根目录执行：

```bash
git clone https://github.com/agjvrkgj/ctyun-auto.git
cd ctyun-auto/
bash deploy.sh
```

按提示输入：

- `APP_USER`：账号
- `APP_PASSWORD`：密码
- 数据目录：容器挂载目录（默认 `~/data`）

脚本会构建镜像 `ctyun-auto-sign:v1` 并启动容器 `ctyun_sign_<APP_USER>`。

## 首次运行说明

- 如日志提示输入短信验证码，直接在当前终端输入并回车。
- 当日志出现“保活任务启动”后，可按 `Ctrl+P` 再按 `Ctrl+Q` 让容器脱离终端并后台运行。
- 若误按 `Ctrl+C` 导致退出，可执行 `docker start ctyun_sign_<APP_USER>`。

## 常用命令

```bash
# 查看实时日志
docker logs -f ctyun_sign_<APP_USER>

# 停止/启动容器
docker stop ctyun_sign_<APP_USER>
docker start ctyun_sign_<APP_USER>

# 自动兑换奖励配置
docker exec -it ctyun_sign_<APP_USER> python3 /app/pc_login.py --config-redeem
```

验证码识别api方案来自 https://github.com/sml2h3/ddddocr
