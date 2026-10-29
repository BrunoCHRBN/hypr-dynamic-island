# 🏝️ Hypr Dynamic Island

Uma **Dynamic Island** moderna, inteligente e fluida para o compositor **Hyprland** (Wayland), inspirada na interface de cápsula dinâmica do iOS/macOS, implementada nativamente com **Python**, **GTK4** e **gtk4-layer-shell**.

Projetada com foco em **física de mola a 144 Hz**, **zero travamentos na thread de UI (non-blocking)** e consumo ultrabaixo de memória e CPU (~70 MB de RSS ocioso).

---

## ✨ Recursos

- 🎯 **Cápsula Adaptativa Inteligente:**
  - Em repouso com música: exibe arte do álbum, título/artista elipsados com precisão, equalizador animado e progresso em linha sutil.
  - Em repouso sem música: cápsula ultra-compacta exibindo relógio e data.
  - Expansão automática com detecção de hover adaptativa ou fixação por clique / tecla de atalho.
  - Ocultamento inteligente em tela cheia real (jogos, reprodutores de vídeo em F11) com pausa automática de timers.

- 🎵 **Mídia & Player MPRIS Avançado:**
  - Compatível com Spotify, navegadores (Firefox, Chrome), VLC, MPV e reprodutores MPRIS.
  - **Interpolação Local de Progresso:** Atualização contínua a microssegundos sem chamadas repetitivas ao D-Bus.
  - Paleta de cores dinâmica: extração da cor dominante da capa do álbum com cache inteligente.
  - Seletor de players persistente: alterne entre múltiplos reprodutores sem que a seleção seja revertida.

- 🎛️ **Central de Controle Expandida (4 Páginas com Navegação por Scroll / Clique):**
  1. **Mídia (Hero Card):** Capa em alta definição, barra de progresso arrastável, controle de volume exclusivo do player e controles completos.
  2. **Produtividade & Live Activities:** Cronômetros de foco, Pomodoro integrado via CLI e atalhos de rotina.
  3. **Sistema & Quick Toggles:**
     - Métricas em tempo real de CPU e Memória RAM.
     - Alternância de saída de áudio com detecção dinâmica (Fones Analógicos ↔ Monitor HDMI).
     - Quick Toggles: Hotspot Wi-Fi, VPN Cloudflare-WARP e bloqueador de anúncios AdGuard Home.
     - Integração reativa com **KDE Connect**: status de bateria, carregamento e toque do celular (Find My Phone).
  4. **Histórico de Notificações:** Central com histórico cronológico de notificações recebidas, botão de cópia e limpeza.

- 🔔 **HUDs & Notificações Flutuantes:**
  - HUD instantâneo ao ajustar volume ou silenciar microfone.
  - Banner dinâmico ao detectar gravação de tela ativa (`wf-recorder`).
  - Bridge integrada para **Dunst** (`island-notify`) com sanitização automática de HTML e entidades de texto.

- 🎨 **Estilização Desacoplada com Hot-Reload:**
  - Folha de estilo em [`src/island.css`](src/island.css) baseada na paleta Catppuccin Mocha.
  - **Live Reload:** Modifique e salve o arquivo CSS para ver as mudanças aplicadas instantaneamente sem reiniciar a aplicação.

- ⚙️ **Configuração Simples (`config.toml`):**
  - Ajuste cidade do clima (wttr.in), monitor alvo, margens, nomes de conexão e intervalos de sincronização.

---

## 📂 Estrutura do Repositório

```text
hypr-dynamic-island/
├── bin/
│   ├── island            # Launcher principal com carregamento seguro do Layer-Shell
│   ├── island-activity   # Utilitário CLI para Pomodoro e Live Activities
│   ├── island-dnd        # Alternador do modo Não Perturbe (DND)
│   ├── island-notify     # Ponte de notificações (CLI / Dunst)
│   └── island-toggle     # Alternador de abertura da ilha via IPC UNIX Socket
├── config/
│   └── config.example.toml # Arquivo de configuração de exemplo
├── src/
│   ├── island.py         # Daemon e interface gráfica GTK4 da Dynamic Island
│   └── island.css        # Estilos, transições e tema visual
├── systemd/
│   └── island.service    # Unidade systemd de usuário para inicialização automática
├── install.sh            # Script de instalação automatizado
├── LICENSE               # Licença MIT
└── README.md
```

---

## 🚀 Instalação

### 1. Dependências do Sistema

No **Arch Linux** / **EndeavourOS**:

```bash
sudo pacman -S gtk4 gtk4-layer-shell python-gobject pipewire wireplumber networkmanager
```

*Opcionais:*
- `kdeconnect` (para monitoramento de bateria do celular)
- `dunst` (para redirecionamento de notificações)

### 2. Instalação Automática

Clone o repositório e execute o instalador:

```bash
git clone https://github.com/BrunoCHRBN/hypr-dynamic-island.git
cd hypr-dynamic-island
chmod +x install.sh
./install.sh
```

O instalador irá:
1. Copiar `island.py` e `island.css` para `~/.config/hypr/scripts/`.
2. Instalar os utilitários em `~/.local/bin/`.
3. Criar a configuração inicial em `~/.config/island/config.toml`.
4. Registrar e recarregar a unidade do systemd de usuário.

---

## ⚙️ Configuração

O arquivo de configuração vive em `~/.config/island/config.toml`:

```toml
[geral]
# Cidade usada para previsão do tempo (wttr.in)
cidade = "Araraquara"

# Conector do monitor principal (ex: DP-1, DP-2, HDMI-A-1)
monitor = "DP-2"

# Margem em pixels a partir do topo da tela
margem_topo = 8

[rede]
# Conexão de VPN gerenciada pelo NetworkManager
vpn_conexao = "Cloudflare-WARP"

# URL da API local do AdGuard Home
adguard_url = "http://127.0.0.1:3000"

[audio]
# Padrões para detecção de sinks do PipeWire/PulseAudio
sink_fones = "analog-stereo"
sink_monitor = "hdmi-stereo"

[mpris]
# Intervalo de interpolação da barra de progresso (ms)
tick_posicao_ms = 500

# Frequência de ressincronização real via D-Bus (segundos)
resync_intervalo_s = 5.0
```

---

## ⌨️ Integração com o Hyprland

Adicione as seguintes linhas ao seu `~/.config/hypr/hyprland.conf`:

```ini
# Inicialização automática da Dynamic Island
exec-once = systemctl --user start island.service

# Atalho para expandir / fixar a ilha (ex: Super + B)
bind = $mainMod, B, exec, ~/.local/bin/island-toggle

# Atalho para alternar Não Perturbe (DND)
bind = $mainMod, D, exec, ~/.local/bin/island-dnd
```

### Integração com Notificações do Dunst (Opcional)

Para que as notificações do sistema sejam direcionadas ao HUD da Dynamic Island, inclua em `~/.config/dunst/dunstrc`:

```ini
[global]
always_run_script = true
script = /home/SEU_USUARIO/.local/bin/island-notify
```

---

## 🛠️ Comandos CLI

A Dynamic Island expõe um socket UNIX ultrarrápido em `$XDG_RUNTIME_DIR/island.sock`:

- **Alternar expansão:**
  ```bash
  island-toggle
  ```
- **Iniciar timer Pomodoro:**
  ```bash
  island-activity timer 25m "Estudo Focado"
  ```
- **Parar timer ativo:**
  ```bash
  island-activity stop-timer
  ```
- **Exibir notificação personalizada na ilha:**
  ```bash
  island-notify "Título" "Mensagem customizada"
  ```
- **Alternar modo Não Perturbe:**
  ```bash
  island-dnd
  ```

---

## 📄 Licença

Distribuído sob a licença [MIT](LICENSE). Desenvolvido por **Bruno Vinicius da Silva Santos**.
