#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if [ "$#" -gt 0 ]; then
    if [[ "$#" -eq 1 && ( "$1" == '--help' || "$1" == '-h' ) ]]; then
        printf '%s\n' '用法: bash build.sh' \
            '只构建镜像，不读取账号、不创建容器。默认镜像: ctyun-auto-sign:v1' \
            '自定义镜像名: CTYUN_IMAGE=my-ctyun:v1 bash build.sh'
        exit 0
    fi
    printf '[!] 未知参数。使用 bash build.sh --help 查看帮助。\n' >&2
    exit 1
fi

source ./scripts/image.sh
CTYUN_IMAGE_MODE=build
prepare_ctyun_image
printf '[*] 构建完成。部署时运行: CTYUN_IMAGE_MODE=local CTYUN_IMAGE=%q bash deploy.sh\n' "$CTYUN_IMAGE"
