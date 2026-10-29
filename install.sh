#!/usr/bin/env bash
# ==============================================================================
# Installer for Hypr Dynamic Island
# ==============================================================================
set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
BLUE="\033[34m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

log_info() { echo -e "${BLUE}${BOLD}[INFO]${RESET} $1"; }
log_ok() { echo -e "${GREEN}${BOLD}[OK]${RESET} $1"; }
log_warn() { echo -e "${YELLOW}${BOLD}[WARN]${RESET} $1"; }
log_err() { echo -e "${RED}${BOLD}[ERROR]${RESET} $1"; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${BOLD}===========================================${RESET}"
echo -e "${BOLD}      Hypr Dynamic Island Installer       ${RESET}"
echo -e "${BOLD}===========================================${RESET}"
echo ""

# 1. Dependency Check
log_info "Verificando dependências do sistema..."
deps=(python3 pactl wpctl nmcli)
missing=()

for cmd in "${deps[@]}"; do
    if ! command -v "$cmd" &>/dev/null; then
        missing+=("$cmd")
    fi
done

# Check for GTK4 and gtk4-layer-shell
if ! python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Gdk', '4.0')" &>/dev/null; then
    missing+=("python-gobject / gtk4")
fi

if [ ! -f /usr/lib/libgtk4-layer-shell.so ] && [ ! -f /usr/lib64/libgtk4-layer-shell.so ]; then
    log_warn "libgtk4-layer-shell.so não encontrado nos caminhos padrão."
    log_warn "No Arch/EndeavourOS: sudo pacman -S gtk4-layer-shell"
fi

if [ ${#missing[@]} -gt 0 ]; then
    log_warn "Dependências recomendadas ausentes: ${missing[*]}"
    log_warn "Certifique-se de instalar os pacotes necessários para o seu sistema."
else
    log_ok "Dependências básicas verificadas com sucesso."
fi

# 2. Directories
DEST_SCRIPTS="$HOME/.config/hypr/scripts"
DEST_BIN="$HOME/.local/bin"
DEST_CONFIG="$HOME/.config/island"
DEST_SYSTEMD="$HOME/.config/systemd/user"

mkdir -p "$DEST_SCRIPTS" "$DEST_BIN" "$DEST_CONFIG" "$DEST_SYSTEMD"

# 3. Installing Core Files
log_info "Instalando pacote modular e estilos em $DEST_SCRIPTS..."
rm -rf "$DEST_SCRIPTS/island"
cp -rf "$REPO_DIR/src/island" "$DEST_SCRIPTS/island"
cp -f "$REPO_DIR/src/island.css" "$DEST_SCRIPTS/island.css"
if [ -f "$REPO_DIR/src/island.py" ]; then
    cp -f "$REPO_DIR/src/island.py" "$DEST_SCRIPTS/island.py.monolithic"
fi
log_ok "Pacote modular 'island' e island.css instalados em $DEST_SCRIPTS."

# 4. Installing CLI Wrappers
log_info "Instalando executáveis em $DEST_BIN..."
for b in island islandctl island-toggle island-notify island-activity island-dnd; do
    cp -f "$REPO_DIR/bin/$b" "$DEST_BIN/$b"
    chmod +x "$DEST_BIN/$b"
done
log_ok "Utilitários CLI (incluindo islandctl) instalados em $DEST_BIN."

# 5. Installing Config
if [ ! -f "$DEST_CONFIG/config.toml" ]; then
    log_info "Criando arquivo de configuração em $DEST_CONFIG/config.toml..."
    cp "$REPO_DIR/config/config.example.toml" "$DEST_CONFIG/config.toml"
    log_ok "Configuração inicial criada."
else
    log_info "Configuração existente mantida em $DEST_CONFIG/config.toml."
fi

# 6. Installing Systemd Service
log_info "Configurando serviço systemd de usuário..."
cp -f "$REPO_DIR/systemd/island.service" "$DEST_SYSTEMD/island.service"
systemctl --user daemon-reload || true
log_ok "Serviço island.service registrado."

echo ""
echo -e "${GREEN}${BOLD}Instalação concluída com sucesso!${RESET}"
echo ""
echo -e "Para iniciar agora:"
echo -e "  ${BLUE}systemctl --user restart island.service${RESET}"
echo ""
echo -e "Para iniciar automaticamente com o Hyprland, adicione ao seu ${BOLD}hyprland.conf${RESET}:"
echo -e "  ${BLUE}exec-once = systemctl --user start island.service${RESET}"
echo -e "  ${BLUE}bind = \$mainMod, B, exec, island-toggle${RESET}"
echo -e "  ${BLUE}bind = \$mainMod, D, exec, island-dnd${RESET}"
echo ""
