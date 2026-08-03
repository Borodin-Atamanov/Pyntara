#!/usr/bin/env bash
# Interactive Pyntara installer using dialog.
# Typical usage: 
# curl --fail --location --retry 15 --retry-delay 3 --retry-all-errors --retry-connrefused -o insta.sh https://raw.githubusercontent.com/Borodin-Atamanov/Pyntara/main/inst.sh && sudo bash inst.sh
set -euo pipefail

# Обязательно кратко писать, о успешном выполнении каждой части скрипта: Просто обычное предложение на английском о том, что произошло. Использовать переменные в выводе, чтоыб пользователь получал максимум полезно информации о происходящем.
# ПЛАН РЕАЛИЗАЦИИ БУТСТРАПА
# Это намерение, а не описание текущего поведения. Код по этому плану ещё не написан.
# Источник требований: docs/contracts/bootstrap.md, docs/contracts/interactive-ui.md, docs/spec/install-modes.md.
#
# Фаза 1. Каркас и служебные функции
# 1.1 Проверка root
# В начале main проверю EUID: если процесс не root, выведу сообщение об ошибке и завершусь с ненулевым кодом; при успехе выведу краткое сообщение о выполнении этой части (контракт п.1).
# 1.2 Каталоги FHS
# Создам /var/cache/pyntara, /var/lib/pyntara, /var/log/pyntara командой install -d (контракт п.5).
# 1.3 Логирование
# Функция log: пишет строку с меткой времени YYYY-MM-DD-HH-MM-SS в /var/log/pyntara/install.log и дублирует вывод в терминал, чтобы лог и экран совпадали (контракт п.9).
# 1.4 Замер времени
# Функция run_timed: выполняет переданную команду под time и фиксирует длительность в логе (контракт п.7).
# 1.5 Условное объявление функций
# Каждую функцию объявлю под guard if ! declare -f имя &>/dev/null; then ... fi, чтобы тесты могли подменять её моком через source (контракт п.10).
#
# Фаза 2. Окружение
# 2.1 Оптимистичный apt
# Функция install_packages: первая попытка apt-get install -y без обновления индекса; если пакеты не нашлись, выполню apt-get update и повторю установку; все apt-команды с DEBIAN_FRONTEND=noninteractive (контракт п.2).
# 2.2 Пакеты
# Установлю по порядку: dialog, python3, python3-venv, git, curl, ca-certificates (контракт п.3).
# 2.3 uv
# Функция install_uv: официальный скрипт установки от Astral (контракт п.3).
#
# Фаза 3. Доставка исходников и запуск
# 3.1 Получение репозитория
# Функция fetch_source: git clone --depth 1 в /var/cache/pyntara; если каталог уже содержит файлы, выполню git fetch и reset к последней ревизии вместо повторного клонирования (контракт п.4).
# 3.2 Окружение Python
# Функция setup_python: uv sync в каталоге репозитория; если lockfile актуален, запущу с флагом --locked, иначе без него (контракт п.6).
# 3.3 Запуск
# Функция run_pyntara: uv run pyntara без ограничения по времени (контракт п.6, п.8).
#
# Фаза 4. Интерактив через dialog, отдельный этап
# 4.1 Запрос пароля production.vault с таймаутом VAULT_PASSWORD_TIMEOUT (333 с) и тремя попытками, при неудаче переход на default.vault. Сообщения — обычный текст с таймаутом MESSAGE_TIMEOUT. Отсутствие production.vault — явная ошибка и немедленный fallback на default.vault. Пароль вводится read -s, а не dialog --passwordbox: dialog рисует окно в stderr и ломается там, где stdout не терминал.
# 4.2 Выбор режима minimal/server/desktop с автоопределением по системе и авто-выбором через 11 с.
# 4.3 Выбор задач чекбоксами с авто-подтверждением через 30 с.
# 4.4 Вопрос про force-режим (11 с, по умолчанию нет) и чекбоксы force-задач.
# 4.5 Требования: docs/contracts/interactive-ui.md.
#
# Фаза 5. Тесты бутстрапа
# 5.1 Создам tests/test_inst.sh: source inst.sh, подмена функций моками (guard из п.10).
# 5.2 Проверка не-root: завершается с ошибкой.
# 5.3 Проверка install_packages: обновляет индекс только при неудаче первой попытки.
# 5.4 Проверка fetch_source: при повторном запуске делает fetch+reset, а не clone.
# 5.5 Проверка log: пишет в файл и в терминал с меткой времени.
# 5.6 Прогон тестов: bash tests/test_inst.sh; для Python-части по docs/guides/developer-guide.md.


# --- Implementation: phase 1.1 (root check) ---
# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f check_root &>/dev/null; then
check_root() {
    # Bootstrap contract section 1: must run as root, otherwise exit with an error.
    if [[ "$EUID" -ne 0 ]]; then
        echo "Error: Pyntara installer must run as root. Restart with: sudo bash inst.sh" >&2
        exit 1
    fi
    echo "Running as root"
}
fi

# Implementation: phase 1.2 (FHS directories)
# FHS base directories, bootstrap contract section 5.
# Overridable via environment so tests never touch real system paths.
CACHE_DIR="${PYNTARA_CACHE_DIR:-/var/cache/pyntara}"
STATE_DIR="${PYNTARA_STATE_DIR:-/var/lib/pyntara}"
LOG_DIR="${PYNTARA_LOG_DIR:-/var/log/pyntara}"

# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f ensure_fhs_dirs &>/dev/null; then
ensure_fhs_dirs() {
    # Bootstrap contract section 5: create cache, state and log directories.
    install -d "$CACHE_DIR" "$STATE_DIR" "$LOG_DIR"
    echo "FHS directories ready: $CACHE_DIR, $STATE_DIR, $LOG_DIR"
}
fi

# Implementation: phase 1.3 (logging)
# Single log file for the whole installer run, bootstrap contract section 9.
# Overridable via environment so tests never touch real system paths.
LOG_FILE="${PYNTARA_LOG_FILE:-$LOG_DIR/install.log}"

# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f log &>/dev/null; then
log() {
    # Bootstrap contract section 9: timestamped message written to log file and terminal.
    local message="$1"
    local timestamp
    timestamp="$(date +%Y-%m-%d-%H-%M-%S)"
    echo "[$timestamp] $message" | tee -a "$LOG_FILE"
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f run_logged &>/dev/null; then
run_logged() {
    # Bootstrap contract section 9: run a command, stream output to terminal and log file.
    # stderr is merged into stdout so errors are captured too.
    # The if guard captures the command exit code without triggering errexit.
    if "$@" 2>&1 | tee -a "$LOG_FILE"; then
        return 0
    fi
    return "${PIPESTATUS[0]}"
}
fi

# Implementation: phase 1.4 (timing)
# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f run_timed &>/dev/null; then
run_timed() {
    # Bootstrap contract section 7: run a command, time it, log duration and exit code.
    local start rc elapsed
    start="$(date +%s)"
    # The if guard captures the command exit code without triggering errexit.
    if "$@" 2>&1 | tee -a "$LOG_FILE"; then
        rc=0
    else
        rc="${PIPESTATUS[0]}"
    fi
    elapsed="$(($(date +%s) - start))"
    log "Finished in ${elapsed}s with exit code ${rc}: $*"
    return "$rc"
}
fi

# Implementation: phase 2.1 (optimistic apt)
# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f apt_install &>/dev/null; then
apt_install() {
    # Bootstrap contract section 2: try install without update first,
    # refresh the index and retry only if the first attempt fails.
    # All apt operations are noninteractive.
    local packages=("$@")
    if DEBIAN_FRONTEND=noninteractive run_timed apt-get install -y "${packages[@]}"; then
        log "Packages installed without index refresh: ${packages[*]}"
        return 0
    fi
    log "First install attempt failed, refreshing package index"
    DEBIAN_FRONTEND=noninteractive run_timed apt-get update
    DEBIAN_FRONTEND=noninteractive run_timed apt-get install -y "${packages[@]}"
    log "Packages installed after index refresh: ${packages[*]}"
}
fi

# Implementation: phase 2.2 (package set)
# Minimal runtime dependencies, bootstrap contract section 3, in required order.
RUNTIME_PACKAGES=(dialog python3 python3-venv git curl ca-certificates)

# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f install_dependencies &>/dev/null; then
install_dependencies() {
    # Bootstrap contract section 3: install only the packages that are missing.
    # dpkg -s reports installed state, so repeated runs install nothing new.
    local missing=()
    local pkg
    for pkg in "${RUNTIME_PACKAGES[@]}"; do
        if ! dpkg -s "$pkg" &>/dev/null; then
            missing+=("$pkg")
        fi
    done
    if [[ "${#missing[@]}" -eq 0 ]]; then
        log "All runtime packages already installed: ${RUNTIME_PACKAGES[*]}"
        return 0
    fi
    log "Installing runtime packages: ${missing[*]}"
    apt_install "${missing[@]}"
}
fi

# Implementation: phase 2.3 (uv package manager)
# Official Astral installer, bootstrap contract section 3.
# uv cache lives in its own subdirectory of the FHS cache directory
# (bootstrap contract section 5). It must not equal the cache root itself:
# the git clone lives at $CACHE_DIR/repo, and uv refuses a project directory
# that is inside its own cache directory.
UV_INSTALL_URL="https://astral.sh/uv/install.sh"
export UV_CACHE_DIR="$CACHE_DIR/uv-cache"

# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f install_uv &>/dev/null; then
install_uv() {
    # Install uv only when missing. The Astral installer runs in a subprocess
    # so its own environment changes never leak into this shell.
    if command -v uv &>/dev/null; then
        log "uv already installed: $(command -v uv)"
        return 0
    fi
    local installer="$CACHE_DIR/uv-install.sh"
    log "Downloading uv installer to $installer"
    run_timed curl -LsSf -o "$installer" "$UV_INSTALL_URL"
    log "Running uv installer"
    # The installer runs in a subprocess so its own environment changes never
    # leak into this shell. UV_CACHE_DIR is set for the child explicitly.
    env UV_CACHE_DIR="$CACHE_DIR" bash "$installer" 2>&1 | tee -a "$LOG_FILE"
    # The Astral installer places uv into $HOME/.local/bin, which is not on
    # root's PATH by default, so add it explicitly for later phases.
    export PATH="$HOME/.local/bin:$PATH"
    log "uv installed: $(command -v uv)"
}
fi

# Implementation: phase 3.1 (source delivery via git)

# Repository and branch are configurable so tests never touch the real remote.
REPO_URL="${PYNTARA_REPO_URL:-https://github.com/Borodin-Atamanov/Pyntara.git}"
REPO_BRANCH="${PYNTARA_REPO_BRANCH:-main}"
SOURCE_DIR="${PYNTARA_SOURCE_DIR:-$CACHE_DIR/repo}"

# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f fetch_source &>/dev/null; then
fetch_source() {
    # Bootstrap contract section 4: clone the repo, or update an existing clone.
    # A broken clone (no .git) is removed and recreated instead of failing.
    if [[ ! -d "$SOURCE_DIR" || -z "$(ls -A "$SOURCE_DIR")" ]]; then
        log "Cloning repository: $REPO_URL branch $REPO_BRANCH"
        run_timed git clone --depth 1 -b "$REPO_BRANCH" "$REPO_URL" "$SOURCE_DIR"
        log "Repository cloned to $SOURCE_DIR"
    elif [[ ! -d "$SOURCE_DIR/.git" ]]; then
        log "Removing broken clone at $SOURCE_DIR"
        rm -rf "$SOURCE_DIR"
        log "Cloning repository: $REPO_URL branch $REPO_BRANCH"
        run_timed git clone --depth 1 -b "$REPO_BRANCH" "$REPO_URL" "$SOURCE_DIR"
        log "Repository cloned to $SOURCE_DIR"
    else
        log "Updating existing repository at $SOURCE_DIR"
        run_timed git -C "$SOURCE_DIR" fetch
        run_timed git -C "$SOURCE_DIR" reset --hard "origin/$REPO_BRANCH"
        log "Repository updated to origin/$REPO_BRANCH"
    fi
}
fi

# Implementation: phase 3.2 (python environment via uv)

# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f setup_python &>/dev/null; then
setup_python() {
    # Bootstrap contract section 6: uv sync in the repo directory.
    # Use --locked when the lockfile is current, plain sync otherwise.
    # cd runs in a subshell so the caller's working directory never changes.
    if [[ ! -d "$SOURCE_DIR" ]]; then
        log "Source directory missing: $SOURCE_DIR"
        return 1
    fi
    if [[ ! -f "$SOURCE_DIR/uv.lock" ]]; then
        # No lockfile yet: sync generates it. Avoids a confusing uv error
        # from `lock --check` on the very first run.
        log "No uv.lock found, syncing without --locked"
        ( cd "$SOURCE_DIR" && run_timed uv sync )
    elif ( cd "$SOURCE_DIR" && run_timed uv lock --check ); then
        log "Lockfile is current, syncing with --locked"
        ( cd "$SOURCE_DIR" && run_timed uv sync --locked )
    else
        log "Lockfile outdated, syncing without --locked"
        ( cd "$SOURCE_DIR" && run_timed uv sync )
    fi
    log "Python environment ready in $SOURCE_DIR"
}
fi

# Implementation: phase 3.3 (launch pyntara)

# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f run_pyntara &>/dev/null; then
run_pyntara() {
    # Bootstrap contract section 6: launch uv run pyntara.
    # Bootstrap contract section 8: no time limit on the pyntara process.
    # cd runs in a subshell so the caller's working directory never changes.
    # The if guard captures the exit code without triggering errexit.
    if [[ ! -d "$SOURCE_DIR" ]]; then
        log "Source directory missing: $SOURCE_DIR"
        return 1
    fi
    local rc=0
    log "Starting Pyntara from $SOURCE_DIR"
    if ( cd "$SOURCE_DIR" && run_timed uv run pyntara "$@" ); then
        rc=0
    else
        rc=$?
    fi
    log "Pyntara finished with exit code $rc"
    return "$rc"
}
fi

# Implementation: phase 4.1 (vault password prompt)
# Interactive screens start here, after the Python environment is ready
# (interactive-ui contract section 1). All paths are overridable via
# environment so tests never touch real system files.
PRODUCTION_VAULT="${PYNTARA_PRODUCTION_VAULT:-$SOURCE_DIR/secrets/production.vault}"
DEFAULT_VAULT="${PYNTARA_DEFAULT_VAULT:-$SOURCE_DIR/secrets/default.vault}"
DEFAULT_VAULT_PASSWORD_FILE="${PYNTARA_DEFAULT_PASSWORD_FILE:-$SOURCE_DIR/secrets/default.password}"
# Countdown for every dialog choice screen, per interactive-ui contract
# section 2.1.
DIALOG_TIMEOUT="${PYNTARA_DIALOG_TIMEOUT:-11}"
# Password entry is not a choice screen: a passphrase must be typed or copied
# from a password manager, so it gets its own generous timeout instead of the
# fast choice-screen countdown.
VAULT_PASSWORD_TIMEOUT="${PYNTARA_VAULT_PASSWORD_TIMEOUT:-333}"
# Plain-text messages are held on screen for this many seconds. Set in one
# place for all phase-4 messages; tests set it to 0 to avoid waiting.
MESSAGE_TIMEOUT="${PYNTARA_MESSAGE_TIMEOUT:-11}"

# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f show_message &>/dev/null; then
show_message() {
    # Print a message as plain text and hold it until Enter or the timeout.
    # log() tees a timestamped line to the terminal and the install log, so
    # the message is both visible and preserved. Plain text with a pause is
    # used instead of dialog --msgbox so messages never take over the screen.
    # read is plain bash, not custom termios code, so the interactive UI
    # contract that forbids termios in the installer is respected.
    log "$1"
    read -r -t "$MESSAGE_TIMEOUT" || true
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f prompt_password_input &>/dev/null; then
prompt_password_input() {
    # One password attempt, read from the terminal without echo. read -s is
    # plain bash, not custom termios code, so the interactive-ui contract
    # restriction still holds. dialog --passwordbox is deliberately not used:
    # dialog renders its box on stderr and misbehaves where stdout is not a
    # terminal (e.g. under sudo), which made the field unreadable and input
    # impossible. The prompt goes to stderr, so it is visible while stdout
    # stays clean. Exit codes mirror dialog: 0 OK, 5 timeout, 1 cancel/EOF.
    # read -t fires SIGALRM on timeout and returns 142 (128+14); dialog
    # reported a timeout as 5, so map 142 to 5 to keep prompt_vault_password
    # semantics unchanged.
    VAULT_ATTEMPT_PASSWORD=""
    local rc
    read -r -s -t "$VAULT_PASSWORD_TIMEOUT" -p "$1" VAULT_ATTEMPT_PASSWORD
    rc=$?
    if [[ "$rc" -eq 142 ]]; then
        return 5
    fi
    if [[ "$rc" -ne 0 ]]; then
        return 1
    fi
    return 0
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f check_vault_password &>/dev/null; then
check_vault_password() {
    # Verify a candidate password by asking the Python engine to open the
    # KeePass database. The shell must not decrypt vaults itself (bootstrap
    # contract section 12): decryption happens in pyntara check-vault via
    # pykeepass. The password travels through stdin, never as an argument,
    # so it cannot leak into the process list or the run_timed log line.
    local password="$1"
    ( cd "$SOURCE_DIR" && printf '%s' "$password" | run_timed uv run pyntara check-vault --vault "$PRODUCTION_VAULT" )
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f fallback_to_default_vault &>/dev/null; then
fallback_to_default_vault() {
    # Export the well-known fallback password so the Python engine can decrypt
    # default.vault. A missing password file is fatal: without it no vault can
    # be opened, so the installer must stop with an explicit error.
    if [[ ! -f "$DEFAULT_VAULT_PASSWORD_FILE" ]]; then
        show_message "ERROR: default vault password file not found at $DEFAULT_VAULT_PASSWORD_FILE. Cannot fall back to default vault."
        return 1
    fi
    local password
    # The file holds one password on the first line; read only that line.
    password="$(head -n 1 "$DEFAULT_VAULT_PASSWORD_FILE")"
    export PYNTARA_VAULT_PASSWORD="$password"
    export PYNTARA_VAULT_SOURCE="default"
    show_message "Using default vault $DEFAULT_VAULT with password from $DEFAULT_VAULT_PASSWORD_FILE"
}
fi

# Guard so the test harness can inject a mock via source (bootstrap contract section 10).
if ! declare -f prompt_vault_password &>/dev/null; then
prompt_vault_password() {
    # Ask for the production vault password, 3 attempts with a
    # VAULT_PASSWORD_TIMEOUT-second timeout each (interactive-ui contract
    # section 4). The final
    # choice is exported as PYNTARA_VAULT_PASSWORD together with
    # PYNTARA_VAULT_SOURCE so the Python engine knows which vault to open.
    # A missing production vault is reported loudly and falls back to the
    # default vault immediately: asking for a password of a database that
    # does not exist would waste the user's time for nothing.
    if [[ ! -f "$PRODUCTION_VAULT" ]]; then
        show_message "ERROR: production vault not found at $PRODUCTION_VAULT. Falling back to default vault."
        fallback_to_default_vault
        return $?
    fi

    local attempt rc
    for attempt in 1 2 3; do
        if prompt_password_input "Enter the password for the production vault ($PRODUCTION_VAULT). This vault stores the secrets used to configure this system. Attempt $attempt of 3."; then
            rc=0
        else
            rc=$?
        fi
        # prompt_password_input returns 5 when no key was pressed within the
        # timeout. The contract demands an immediate fallback in this case,
        # because nobody is interacting with the installer anymore.
        if [[ "$rc" -eq 5 ]]; then
            show_message "No key pressed within $VAULT_PASSWORD_TIMEOUT seconds. Falling back to default vault."
            fallback_to_default_vault
            return $?
        fi
        # Cancel, ESC or an empty password means this attempt produced no
        # password at all. Count it as a failed attempt and keep going.
        if [[ "$rc" -ne 0 || -z "$VAULT_ATTEMPT_PASSWORD" ]]; then
            show_message "No password entered. Attempt $attempt of 3 failed."
            continue
        fi
        # A non-empty password is verified by the Python engine right away.
        # A wrong password is the main reason the contract grants 3 attempts,
        # so the check must happen here, not later inside the engine.
        if check_vault_password "$VAULT_ATTEMPT_PASSWORD"; then
            export PYNTARA_VAULT_PASSWORD="$VAULT_ATTEMPT_PASSWORD"
            export PYNTARA_VAULT_SOURCE="production"
            show_message "Production vault password accepted, using $PRODUCTION_VAULT"
            return 0
        fi
        show_message "Wrong password for $PRODUCTION_VAULT. Attempt $attempt of 3 failed."
    done

    # All 3 attempts failed to produce a correct password: fall back.
    show_message "All 3 attempts failed. Falling back to default vault."
    fallback_to_default_vault
    return $?
}
fi

# Guard so the test harness can inject a mock main via source (bootstrap contract section 10).
if ! declare -f main &>/dev/null; then
main() {
    check_root
    ensure_fhs_dirs
    log "Install log started: $LOG_FILE"
    install_dependencies
    install_uv
    fetch_source
    setup_python
    # Interactive phase 4.1: the vault password screen runs after the Python
    # environment is ready (interactive-ui contract section 1). A vault
    # failure is fatal: the engine cannot provision without secrets.
    if ! prompt_vault_password; then
        log "Vault setup failed, aborting"
        exit 1
    fi
    run_pyntara "$@"
    log "Bootstrap finished"
}
fi

# Run only on direct execution so tests can source this file safely.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi

