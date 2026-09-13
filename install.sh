#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL='https://github.com/agjvrkgj/ctyun-auto.git'
INSTALL_DIR='/opt/ctyun-auto'
BRANCH='main'
DEPLOY_SCRIPT='deploy.sh'
DOCKER_INSTALLER=''

log() { printf '[*] %s\n' "$*"; }
die() { printf '[!] %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
天翼云电脑一键部署（在 Linux 的 root 终端中运行）

用法: bash install.sh [选项]
  --dir PATH       项目安装目录，须为绝对路径（默认 /opt/ctyun-auto）
  --branch NAME    使用的仓库分支（默认 main）
  --cron           使用可自定义定时任务的 deploy_cron.sh
  -h, --help       显示帮助

自动安装 Git、curl、证书及缺少的 Docker，下载项目后进入交互式部署。
已有 Docker 不会重新安装；已有项目仅允许在干净的同名分支上快进更新。
账号、密码和首次短信验证均在终端输入。
EOF
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --dir|--branch)
                [ "$#" -ge 2 ] && [ -n "$2" ] || die "$1 缺少参数。"
                case "$1" in
                    --dir) INSTALL_DIR="$2" ;;
                    --branch) BRANCH="$2" ;;
                esac
                shift 2
                ;;
            --cron) DEPLOY_SCRIPT='deploy_cron.sh'; shift ;;
            -h|--help) usage; exit 0 ;;
            *) die "未知选项: $1（使用 --help 查看帮助）。" ;;
        esac
    done
    [[ "$INSTALL_DIR" == /* && "$INSTALL_DIR" != / && "$INSTALL_DIR" != *$'\n'* ]] ||
        die '安装目录必须为非根目录的绝对路径。'
    [[ "$BRANCH" != -* && "$BRANCH" != *$'\n'* ]] || die '分支名称无效。'
}

prepare_terminal() {
    # curl | bash 时 stdin 是脚本流，必须切回终端才能输入账号及短信验证码。
    if [ ! -t 0 ]; then
        { exec </dev/tty; } 2>/dev/null ||
            die '未找到交互终端。请通过 SSH 登录服务器后运行。'
    fi
    [ -t 0 ] || die '部署需要交互终端。'
}

install_dependencies() {
    if command -v git >/dev/null 2>&1 && command -v curl >/dev/null 2>&1 &&
        { [ -s /etc/ssl/certs/ca-certificates.crt ] || [ -s /etc/pki/tls/certs/ca-bundle.crt ]; }; then
        return
    fi
    log '安装 Git、curl 和 CA 证书...'
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y git curl ca-certificates
    elif command -v yum >/dev/null 2>&1; then
        yum install -y git curl ca-certificates
    else
        die '未找到 apt-get/dnf/yum，请先手动安装 Git、curl 和 CA 证书。'
    fi
}

prepare_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        log '通过 Docker 官方安装脚本安装 Docker...'
        DOCKER_INSTALLER=$(mktemp)
        curl --fail --show-error --silent --location --retry 3 \
            --connect-timeout 15 --max-time 300 \
            https://get.docker.com -o "$DOCKER_INSTALLER"
        [ -s "$DOCKER_INSTALLER" ] || die 'Docker 安装脚本下载为空。'
        sh "$DOCKER_INSTALLER"
        rm -f -- "$DOCKER_INSTALLER"
        DOCKER_INSTALLER=''
    fi

    if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
        systemctl enable docker || log '未能设置 Docker 开机启动，请稍后检查服务配置。'
        if ! docker info >/dev/null 2>&1; then
            systemctl start docker
        fi
    elif ! docker info >/dev/null 2>&1 && command -v service >/dev/null 2>&1; then
        service docker start
    fi

    # 服务启动后可能需要短暂等待；失败时绝不继续重建容器。
    local attempt
    for attempt in {1..15}; do
        if docker info >/dev/null 2>&1; then
            return
        fi
        sleep 2
    done
    die 'Docker 服务不可用，请检查 docker info 及 Docker 服务日志后重试。'
}

prepare_repository() {
    git check-ref-format --branch "$BRANCH" >/dev/null || die '分支名称无效。'
    if [ -d "$INSTALL_DIR/.git" ]; then
        local origin current_branch
        origin=$(git -C "$INSTALL_DIR" remote get-url origin)
        case "$origin" in
            "$REPO_URL"|https://github.com/agjvrkgj/ctyun-auto|git@github.com:agjvrkgj/ctyun-auto.git) ;;
            *) die '安装目录属于其他仓库，请使用 --dir 指定空目录。' ;;
        esac
        current_branch=$(git -C "$INSTALL_DIR" symbolic-ref --short HEAD) ||
            die '安装目录处于分离 HEAD 状态，请指定新的安装目录。'
        [ "$current_branch" = "$BRANCH" ] || die '已有项目的分支与 --branch 不一致，请指定新的安装目录。'
        [ -z "$(git -C "$INSTALL_DIR" status --porcelain)" ] ||
            die '安装目录有本地修改或未跟踪文件，请先保存修改，或使用新的安装目录。'
        log "更新 $INSTALL_DIR（仅允许快进）..."
        git -C "$INSTALL_DIR" fetch origin "refs/heads/$BRANCH"
        git -C "$INSTALL_DIR" merge --ff-only FETCH_HEAD
    elif [ -e "$INSTALL_DIR" ] && { [ ! -d "$INSTALL_DIR" ] || [ -n "$(ls -A -- "$INSTALL_DIR")" ]; }; then
        die '安装目录非空且不是本项目的 Git 仓库，请使用 --dir 指定空目录。'
    else
        log "下载项目到 $INSTALL_DIR..."
        mkdir -p -- "$(dirname -- "$INSTALL_DIR")"
        git clone --branch "$BRANCH" --single-branch -- "$REPO_URL" "$INSTALL_DIR"
    fi
    [ -f "$INSTALL_DIR/app/Dockerfile" ] && [ -f "$INSTALL_DIR/$DEPLOY_SCRIPT" ] ||
        die '项目文件不完整，未找到 Dockerfile 或部署脚本。'
}

cleanup() {
    if [ -n "$DOCKER_INSTALLER" ]; then
        rm -f -- "$DOCKER_INSTALLER"
    fi
}

main() {
    parse_args "$@"
    [ "$(uname -s)" = Linux ] || die '此脚本仅支持 Linux。'
    [ "$(id -u)" -eq 0 ] || die '请先执行 sudo -i 切换到 root，再运行本脚本。'
    prepare_terminal
    trap cleanup EXIT
    # 不打印失败命令，避免把后续交互输入的敏感信息写入日志。
    trap 'printf "[!] 一键部署失败（第 %s 行），请检查上方错误信息。\n" "$LINENO" >&2' ERR
    install_dependencies
    prepare_docker
    prepare_repository
    cd -- "$INSTALL_DIR"
    log "环境准备完成，开始运行 $DEPLOY_SCRIPT..."
    bash "./$DEPLOY_SCRIPT"
}

# 整个入口在读取完整个脚本后执行，兼容 curl ... | bash。
if [[ "${BASH_SOURCE[0]:-$0}" == "$0" ]]; then
    main "$@"
fi
