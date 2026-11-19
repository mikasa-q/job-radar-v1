#!/bin/sh
# Local single-instance startup. No key is read or passed by this script.
set -eu
cd -P -- "$(dirname -- "$0")"
fail() { printf '%s\n' "$*" >&2; exit 1; }

mode=${1:-offline}
[ "$#" -le 1 ] || fail "用法: $0 [--live|stop]"
case "$mode" in offline|--live|stop) ;; *) fail "用法: $0 [--live|stop]" ;; esac
command -v docker >/dev/null 2>&1 || fail '找不到 docker；请先启动 Docker Desktop 并完成首次设置。'
docker info >/dev/null 2>&1 || fail '无法连接 Docker 引擎；请先启动 Docker Desktop。'
docker compose version >/dev/null 2>&1 || fail '需要 Docker Compose v2.24.4 或更新版本。'
set -- --env-file /dev/null -f compose.yaml
if [ "$mode" = stop ]; then
    exec docker compose "$@" down
fi

export JOBRADAR_UID="$(id -u)" JOBRADAR_GID="$(id -g)"
export JOBRADAR_PORT="${JOBRADAR_PORT:-8080}"
case "$JOBRADAR_PORT" in *[!0-9]*|0*) fail 'JOBRADAR_PORT 必须是 1–65535 的十进制端口。' ;; esac
[ "${#JOBRADAR_PORT}" -le 5 ] && [ "$JOBRADAR_PORT" -le 65535 ] || fail 'JOBRADAR_PORT 必须在 1–65535。'

absolute_path() { case "$2" in /*) ;; *) fail "$1 必须是绝对路径。" ;; esac; }
if [ "$mode" = --live ]; then
    : "${JOBRADAR_EVIDENCE_DB:?--live 需指定已审批的 JOBRADAR_EVIDENCE_DB}"
    : "${JOBRADAR_DATASET_ID:?--live 需指定已审批的 JOBRADAR_DATASET_ID}"
    : "${JOBRADAR_LIVE_STORE:?--live 需指定 JOBRADAR_LIVE_STORE}"
    : "${JOBRADAR_INDEX_ROOT:?--live 需指定 JOBRADAR_INDEX_ROOT}"
    absolute_path JOBRADAR_LIVE_STORE "$JOBRADAR_LIVE_STORE"
    absolute_path JOBRADAR_INDEX_ROOT "$JOBRADAR_INDEX_ROOT"
    [ -d "$JOBRADAR_LIVE_STORE" ] && [ -f "$JOBRADAR_LIVE_STORE/state.json" ] \
        || fail 'Live 模式需要已有审批账本，启动不会初始化或改写审批。'
    [ "$JOBRADAR_LIVE_STORE" = "$(cd -P -- "$JOBRADAR_LIVE_STORE" && pwd -P)" ] \
        || fail 'JOBRADAR_LIVE_STORE 必须是原审批使用的 canonical 绝对路径。'
    [ -f "$JOBRADAR_INDEX_ROOT/CURRENT" ] || fail '找不到指定的冻结索引。'
    [ -f "$HOME/.claude-mem/.env" ] || fail '找不到 Live 模式的运行时密钥文件。'
    set -- "$@" -f compose.live.yaml
else
    export JOBRADAR_EVIDENCE_DB="${JOBRADAR_EVIDENCE_DB:-$HOME/Desktop/jobradar-v1-data/evidence.sqlite3}"
    export JOBRADAR_OFFLINE_STORE="${JOBRADAR_OFFLINE_STORE:-$HOME/.jobradar/docker-v1}"
fi
absolute_path JOBRADAR_EVIDENCE_DB "$JOBRADAR_EVIDENCE_DB"
[ -f "$JOBRADAR_EVIDENCE_DB" ] && [ -r "$JOBRADAR_EVIDENCE_DB" ] || fail '找不到可读的 JOBRADAR_EVIDENCE_DB。'

# Find the known local BGE snapshot without copying it or searching other model families.
if [ -z "${JOBRADAR_MODEL_DIR:-}" ]; then
    revision=7999e1d3359715c523056ef9478215996d62a620
    for candidate in "$HOME"/.codex/visualizations/models/BAAI--bge-small-zh-v1.5/"$revision" \
         "$HOME"/.codex/visualizations/*/*/*/*/jobradar-stage3/models/BAAI--bge-small-zh-v1.5/"$revision"; do
        [ -f "$candidate/model.safetensors" ] || continue
        [ -z "${JOBRADAR_MODEL_DIR:-}" ] || fail '找到多个 BGE 快照，请显式指定 JOBRADAR_MODEL_DIR。'
        JOBRADAR_MODEL_DIR=$candidate
    done
fi
if [ -n "${JOBRADAR_MODEL_DIR:-}" ]; then
    absolute_path JOBRADAR_MODEL_DIR "$JOBRADAR_MODEL_DIR"
    [ -r "$JOBRADAR_MODEL_DIR/model.safetensors" ] || fail '模型目录缺少可读的 model.safetensors。'
    export JOBRADAR_MODEL_DIR
    set -- "$@" -f compose.model.yaml
else
    printf '%s\n' '未找到本地 BGE 快照；V1 的 BM25+规则模式可以继续启动。' >&2
fi

if [ "$mode" = offline ]; then
    absolute_path JOBRADAR_OFFLINE_STORE "$JOBRADAR_OFFLINE_STORE"
    (umask 077; mkdir -p -- "$JOBRADAR_OFFLINE_STORE")
fi
docker compose "$@" up --build --wait --wait-timeout 90
printf '\nJobRadar (%s): http://127.0.0.1:%s/docs\n就绪检查: http://127.0.0.1:%s/v1/ready\n' "$mode" "$JOBRADAR_PORT" "$JOBRADAR_PORT"
