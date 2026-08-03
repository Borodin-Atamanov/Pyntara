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
# 4.1 Запрос пароля production.vault с таймаутом 11 с и тремя попытками, при неудаче переход на default.vault.
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
# uv cache stays under the FHS cache directory, bootstrap contract section 5.
UV_INSTALL_URL="https://astral.sh/uv/install.sh"
export UV_CACHE_DIR="$CACHE_DIR"

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

# Guard so the test harness can inject a mock main via source (bootstrap contract section 10).
if ! declare -f main &>/dev/null; then
main() {
    check_root
    ensure_fhs_dirs
    log "Install log started: $LOG_FILE"
    install_dependencies
    install_uv
}
fi

# Run only on direct execution so tests can source this file safely.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi

