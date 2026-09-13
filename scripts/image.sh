#!/usr/bin/env bash
# 由部署/构建脚本 source；准备镜像后提供 CTYUN_IMAGE 和 CTYUN_IMAGE_ID。

prepare_ctyun_image() {
    local mode="${CTYUN_IMAGE_MODE:-pull}"
    case "$mode" in
        pull) CTYUN_IMAGE="${CTYUN_IMAGE:-ghcr.io/agjvrkgj/ctyun-auto:latest}" ;;
        build|local) CTYUN_IMAGE="${CTYUN_IMAGE:-ctyun-auto-sign:v1}" ;;
        *) printf '[!] 无效的 CTYUN_IMAGE_MODE: %s（支持 pull/build/local）。\n' "$mode" >&2; return 1 ;;
    esac

    if ! docker info >/dev/null 2>&1; then
        printf '[!] Docker 服务不可用，请先检查 docker info。\n' >&2
        return 1
    fi

    case "$mode" in
        pull)
            printf '[*] 正在拉取预构建镜像: %s\n' "$CTYUN_IMAGE"
            if ! docker pull "$CTYUN_IMAGE"; then
                printf '%s\n' \
                    '[!] 镜像拉取失败。请检查网络、镜像标签及 GHCR 包是否已设为 Public。' \
                    '    可使用 bash install.sh --build 本地构建，或 --skip-build 使用已有镜像。' >&2
                return 1
            fi
            ;;
        build)
            printf '[*] 正在构建镜像: %s（首次安装 Chromium/Python 依赖可能较久，下方显示完整进度）\n' "$CTYUN_IMAGE"
            # BuildKit 使用逐行日志；旧版 Docker 也保留原生构建输出。
            if ! BUILDKIT_PROGRESS=plain docker build -t "$CTYUN_IMAGE" ./app; then
                printf '[!] 镜像构建失败，请查看上方最后一个失败步骤。尚未创建或替换容器。\n' >&2
                return 1
            fi
            ;;
        local)
            printf '[*] 使用本地已有镜像: %s（跳过拉取和构建）\n' "$CTYUN_IMAGE"
            ;;
    esac

    if ! CTYUN_IMAGE_ID=$(docker image inspect --format '{{.Id}}' "$CTYUN_IMAGE") || [ -z "$CTYUN_IMAGE_ID" ]; then
        printf '[!] 未找到可用镜像 %s，请先运行 bash build.sh，或指定 CTYUN_IMAGE。\n' "$CTYUN_IMAGE" >&2
        return 1
    fi
    # 后续输入账号可能耗时，使用 ID 固定本次准备的镜像，避免同名标签被其他构建修改。
    printf '[*] 镜像已准备好: %s\n' "$CTYUN_IMAGE_ID"
}
