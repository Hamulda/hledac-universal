#!/bin/bash
set -euo pipefail

CDN="https://releases.fx.sh"
BIN_DIR="${FX_INSTALL_DIR:-$HOME/.local/bin}"

err() { printf '\033[1;31merror: %s\033[0m\n' "$*" >&2; exit 1; }

SHIMMER_PID=""
shimmer_start() {
  local label="$1"
  local len=${#label}
  local base=243 peak=255 radius=8 pad=8 gap=6
  local pos=$(( -pad ))
  local max_pos=$(( len + pad + gap ))

  (
    while true; do
      local out="\r\033[K"
      for (( i=0; i<len; i++ )); do
        local d=$(( pos > i ? pos - i : i - pos ))
        local shade=$base
        if (( d < radius )); then
          local t_num=$(( radius - d ))
          local range=$(( peak - base ))
          shade=$(( base + (range * t_num * t_num) / (radius * radius) ))
          (( shade > peak )) && shade=$peak
        fi
        out+="\033[38;5;${shade}m${label:$i:1}"
      done
      out+="\033[0m"
      printf "%b" "$out" >&2

      pos=$(( pos + 1 ))
      (( pos > max_pos )) && pos=$(( -pad ))
      sleep 0.05
    done
  ) &
  SHIMMER_PID=$!
}

shimmer_stop() {
  if [ -n "$SHIMMER_PID" ]; then
    kill "$SHIMMER_PID" 2>/dev/null || true
    wait "$SHIMMER_PID" 2>/dev/null || true
    SHIMMER_PID=""
    printf '\r\033[K' >&2
  fi
}

detect_platform() {
  local os arch

  case "$(uname -s)" in
    Linux*)  os="linux" ;;
    Darwin*) os="macos" ;;
    *)       err "unsupported OS: $(uname -s)" ;;
  esac

  case "$(uname -m)" in
    x86_64|amd64)  arch="x86_64" ;;
    arm64|aarch64) arch="aarch64" ;;
    *)             err "unsupported architecture: $(uname -m)" ;;
  esac

  echo "${os}-${arch}"
}

get_latest_version() {
  local url="${CDN}/latest.txt"
  if command -v curl &>/dev/null; then
    curl -fsSL "$url" | tr -d '[:space:]'
  elif command -v wget &>/dev/null; then
    wget -qO- "$url" | tr -d '[:space:]'
  else
    err "curl or wget required"
  fi
}

download() {
  local url="$1" dest="$2"
  if command -v curl &>/dev/null; then
    curl -fsSL "$url" -o "$dest"
  elif command -v wget &>/dev/null; then
    wget -qO "$dest" "$url"
  fi
}

TMP_DIR=""
cleanup() {
  shimmer_stop
  [ -n "$TMP_DIR" ] && rm -rf "$TMP_DIR"
}
trap cleanup EXIT

is_interactive() { [ -t 0 ] && [ -t 2 ]; }

main() {
  local platform version archive_url

  platform="$(detect_platform)"

  if [ -n "${1:-}" ]; then
    version="$1"
  else
    version="$(get_latest_version)"
  fi

  if [ -z "$version" ]; then
    err "could not determine latest version"
  fi

  if is_interactive; then
    shimmer_start "installing..."
  fi

  archive_url="${CDN}/${version}/fx-${platform}.tar.gz"

  TMP_DIR="$(mktemp -d)"

  download "$archive_url" "$TMP_DIR/fx.tar.gz"

  tar -xzf "$TMP_DIR/fx.tar.gz" -C "$TMP_DIR"

  mkdir -p "$BIN_DIR"
  mv "$TMP_DIR/fx" "$BIN_DIR/fx"
  chmod +x "$BIN_DIR/fx"

  if is_interactive; then
    shimmer_stop
  fi

  local display_version="${version#v}"
  printf "installed fx %s\n" "$display_version" >&2

  if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
    local shell_name rc_file=""
    shell_name="$(basename "${SHELL:-/bin/sh}")"

    case "$shell_name" in
      zsh)  rc_file="$HOME/.zshrc" ;;
      bash)
        if [ -f "$HOME/.bash_profile" ]; then
          rc_file="$HOME/.bash_profile"
        else
          rc_file="$HOME/.bashrc"
        fi
        ;;
      fish) rc_file="$HOME/.config/fish/config.fish" ;;
    esac

    local path_line="export PATH=\"${BIN_DIR}:\$PATH\""
    if [ "$shell_name" = "fish" ]; then
      path_line="set -gx PATH ${BIN_DIR} \$PATH"
    fi

    if [ -n "$rc_file" ]; then
      if ! grep -qF "$BIN_DIR" "$rc_file" 2>/dev/null; then
        {
          echo ""
          echo "# fx CLI"
          echo "$path_line"
        } >> "$rc_file"
      fi
    fi

    if is_interactive; then
      local export_cmd="export PATH=\"${BIN_DIR}:\$PATH\""
      if [ "$shell_name" = "fish" ]; then
        export_cmd="set -gx PATH ${BIN_DIR} \$PATH"
      fi

      printf "run this to use fx now? (y/n) " >&2
      local answer
      read -r answer </dev/tty 2>/dev/null || answer="n"
      printf '\033[A\033[K' >&2

      case "$answer" in
        [yY]*)
          printf "\033[38;5;248m> %s\033[0m\n" "$export_cmd" >&2
          printf "paste the command above, then type fx\n" >&2
          ;;
        *)
          printf "restart your shell or run: %s\n" "$export_cmd" >&2
          ;;
      esac
    else
      printf "to use fx, add to PATH: %s\n" "$BIN_DIR" >&2
    fi
  fi

  if ! is_interactive; then
    echo "$BIN_DIR/fx"
  fi
}

main "$@"
