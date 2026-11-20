#!/usr/bin/env bash
# Launch one isolated CLEWS/C2070 forestry UI instance.
#
# Usage: run_instance.sh <instance> <port> [role]
#
# The instance gets its own runtime directory and port. The runtime directory
# is created with mode 0700 under CLEWS_UI_BASE_DIR (default
# /var/lib/clews-ui). The server is always bound to loopback; Cloudflare
# Tunnel/Access stays the identity and ingress boundary.
#
# This script reads no credentials and writes no secrets. Optional environment:
#   CLEWS_UI_BASE_DIR              runtime base (default /var/lib/clews-ui)
#   CLEWS_UI_PYTHON                Python interpreter (default repo .venv)
#   CLEWS_UI_MAX_CONCURRENT_RUNS   analyst solver cap (default 2)
#
# Launch failures are fail-closed: bad instance names, out-of-range ports,
# busy ports, and an already-running instance are hard errors.

set -euo pipefail
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly DEFAULT_BASE_DIR="/var/lib/clews-ui"
readonly INSTANCE_PATTERN='^[a-z]([a-z0-9-]{0,18}[a-z0-9])?$'

die() {
  printf 'run_instance: %s\n' "$*" >&2
  exit 64
}

usage() {
  cat <<'EOF'
Usage: run_instance.sh <instance> <port> [role]

  instance  1-20 chars: lowercase letter, then letters/digits/hyphens;
            must start with a letter and must not end with a hyphen
  port      1024-65535; one port per instance
  role      analyst (default), model_builder, public, or energy_modeller.
            Only analyst gets --allow-runs.

Environment:
  CLEWS_UI_BASE_DIR              runtime base (default /var/lib/clews-ui)
  CLEWS_UI_PYTHON                Python interpreter (default repo .venv)
  CLEWS_UI_MAX_CONCURRENT_RUNS   analyst solver cap (default 2)
EOF
  exit 64
}

[[ $# -ge 2 && $# -le 3 ]] || usage

instance="$1"
port="$2"
role="${3:-analyst}"

[[ "${instance}" =~ ${INSTANCE_PATTERN} ]] || die \
  "unsafe instance name '${instance}': use 1-20 lowercase letters, digits, or hyphens; start with a letter and do not end with a hyphen"
[[ "${port}" =~ ^[0-9]+$ ]] || die "port '${port}' is not an integer"
port_number=$((10#${port}))
(( port_number >= 1024 && port_number <= 65535 )) || die \
  "port ${port_number} is outside the allowed range 1024-65535"

case "${role}" in
  analyst)
    max_concurrent_runs="${CLEWS_UI_MAX_CONCURRENT_RUNS:-2}"
    [[ "${max_concurrent_runs}" =~ ^[1-9][0-9]*$ ]] || die \
      "CLEWS_UI_MAX_CONCURRENT_RUNS must be a positive integer"
    (( max_concurrent_runs <= 64 )) || die \
      "CLEWS_UI_MAX_CONCURRENT_RUNS must not exceed 64"
    role_flags=(--allow-runs --max-concurrent-runs "${max_concurrent_runs}")
    ;;
  model_builder)
    # Writable scenario API, but solver execution stays an explicit host
    # decision. The shared analyst unit is the only shipped --allow-runs unit.
    role_flags=()
    ;;
  public|energy_modeller)
    role_flags=(--read-only)
    ;;
  *)
    die "unsupported role '${role}': use analyst, model_builder, public, or energy_modeller"
    ;;
esac

base_dir="${CLEWS_UI_BASE_DIR:-${DEFAULT_BASE_DIR}}"
[[ "${base_dir}" == /* ]] || die "CLEWS_UI_BASE_DIR must be an absolute path"
[[ "${base_dir}" != "/" ]] || die "CLEWS_UI_BASE_DIR must not be the filesystem root"
[[ ! -L "${base_dir}" ]] || die "CLEWS_UI_BASE_DIR must not be a symlink"

runtime_dir="${base_dir%/}/${instance}"
[[ ! -L "${runtime_dir}" ]] || die "runtime path must not be a symlink: ${runtime_dir}"
if [[ -e "${runtime_dir}" && ! -d "${runtime_dir}" ]]; then
  die "runtime path exists but is not a directory: ${runtime_dir}"
fi

if [[ ! -d "${base_dir}" ]]; then
  mkdir -m 0711 -- "${base_dir}" || die "cannot create runtime base: ${base_dir}"
fi
if [[ ! -d "${runtime_dir}" ]]; then
  mkdir -m 0700 -- "${runtime_dir}" || die "cannot create runtime directory: ${runtime_dir}"
fi
chmod 0700 -- "${runtime_dir}" || die "cannot set 0700 on runtime directory: ${runtime_dir}"
[[ -O "${runtime_dir}" ]] || die \
  "runtime directory is not owned by the current user: ${runtime_dir}"

# One lock per instance. The descriptor survives exec, so the server process
# holds it for its lifetime; a second wrapper for the same runtime exits here.
if command -v flock >/dev/null 2>&1; then
  exec 9>"${runtime_dir}/.instance.lock"
  flock -n 9 || die "another process holds the instance lock: ${runtime_dir}"
fi

python_bin="${CLEWS_UI_PYTHON:-${REPO_ROOT}/.venv/bin/python}"
if [[ ! -x "${python_bin}" ]]; then
  python_bin="$(command -v python3 || true)"
fi
[[ -n "${python_bin}" && -x "${python_bin}" ]] || die \
  "no executable Python interpreter; set CLEWS_UI_PYTHON"

"${python_bin}" -c 'import fastapi, uvicorn' >/dev/null 2>&1 || die \
  "Python interpreter lacks FastAPI/uvicorn: ${python_bin}"

if command -v pgrep >/dev/null 2>&1; then
  runtime_pattern="${runtime_dir//./[.]}"
  if pgrep -f -- "workflows[.]ui[.]server.*--port[ =]${port_number}([^0-9]|$)" >/dev/null; then
    die "port ${port_number} already belongs to a workflows.ui.server process"
  fi
  if pgrep -f -- "workflows[.]ui[.]server.*--runtime-dir[ =]${runtime_pattern}([^/]|$)" >/dev/null; then
    die "runtime ${runtime_dir} already belongs to a workflows.ui.server process"
  fi
fi

# Bind-test loopback before exec so a busy port fails here with a clear error.
"${python_bin}" - "${port_number}" <<'PY' || die "port is already in use on 127.0.0.1"
import socket
import sys

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("127.0.0.1", int(sys.argv[1])))
finally:
    sock.close()
PY

export CLEWS_UI_RUNTIME_DIR="${runtime_dir}"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1

exec "${python_bin}" -m workflows.ui.server \
  --host 127.0.0.1 \
  --port "${port_number}" \
  --role "${role}" \
  --runtime-dir "${runtime_dir}" \
  "${role_flags[@]}"
