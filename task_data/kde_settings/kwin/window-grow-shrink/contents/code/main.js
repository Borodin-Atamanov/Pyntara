// Pyntara KWin script: grow and shrink the active window by 5 pixels on
// each side and remember each new size as the restore size. The window
// may leave the screen, that is intended.
// Meta+Ctrl+Up grows, Meta+Ctrl+Down shrinks.
// Project: https://github.com/Borodin-Atamanov/Pyntara

function activeWindow() {
    const windows = workspace.windowList();
    for (const window of windows) {
        if (window.active) {
            return window;
        }
    }
    return null;
}

function rememberGeometry(window, rect) {
    // The restore geometry can only be written through setMaximize: its
    // third parameter is stored as geometryRestore. The window must not
    // already be in the target maximize mode, or the call early-returns
    // and skips the restore write; the leading unmaximize guarantees
    // that.
    window.setMaximize(false, false);
    window.setMaximize(true, true, rect);
    window.setMaximize(false, false);
}

function growActiveWindow() {
    const window = activeWindow();
    if (!window) {
        return;
    }
    const geometry = window.frameGeometry;
    const grown = {
        x: geometry.x - 5,
        y: geometry.y - 5,
        width: geometry.width + 10,
        height: geometry.height + 10
    };
    window.frameGeometry = grown;
    rememberGeometry(window, grown);
}

function shrinkActiveWindow() {
    const window = activeWindow();
    if (!window) {
        return;
    }
    const geometry = window.frameGeometry;
    const shrunk = {
        x: geometry.x + 5,
        y: geometry.y + 5,
        width: geometry.width - 10,
        height: geometry.height - 10
    };
    window.frameGeometry = shrunk;
    rememberGeometry(window, shrunk);
}

registerShortcut(
    "Grow Window by 5px",
    "Grow the active window by 5 pixels on each side and remember the new size",
    "Meta+Ctrl+Up",
    growActiveWindow
);

registerShortcut(
    "Shrink Window by 5px",
    "Shrink the active window by 5 pixels on each side and remember the new size",
    "Meta+Ctrl+Down",
    shrinkActiveWindow
);
