// Pyntara KWin script: re-remember the restore geometry on maximize and
// tile events, so dragging a maximized or tiled window returns it to its
// current size instead of the old one. Manual resize is not handled:
// KWin already keeps the restore current on that path.
// Project: https://github.com/Borodin-Atamanov/Pyntara

const lastProcessed = new Map();
const SETTLE_MS = 700;

function trackable(window) {
    return window.normalWindow && window.resizeable
        && !window.specialWindow && !window.popupWindow && !window.deleted;
}

function rememberMaximized(window) {
    // Keep the window maximized but write the maximized geometry as the
    // restore, so dragging it returns to the maximized size. The leading
    // unmaximize guarantees the maximize call does not early-return.
    const geometry = window.frameGeometry;
    window.setMaximize(false, false);
    window.setMaximize(true, true, geometry);
}

function rememberTiled(window) {
    // Write the tile geometry as the restore. This runs through a full
    // maximize cycle, which un-tiles the window: the tile layout is lost
    // but the restore follows the tile size.
    const geometry = window.frameGeometry;
    window.setMaximize(false, false);
    window.setMaximize(true, true, geometry);
    window.setMaximize(false, false);
}

function onStateEvent(window) {
    if (!trackable(window)) {
        return;
    }
    const now = Date.now();
    const prev = lastProcessed.get(window);
    if (prev && now - prev < SETTLE_MS) {
        return;
    }
    if (window.maximizeMode === 3) {
        lastProcessed.set(window, now);
        rememberMaximized(window);
    } else if (window.tile) {
        lastProcessed.set(window, now);
        rememberTiled(window);
    }
}

function connectWindow(window) {
    if (!trackable(window)) {
        return;
    }
    window.maximizedChanged.connect(function () {
        onStateEvent(window);
    });
    window.tileChanged.connect(function () {
        onStateEvent(window);
    });
    window.quickTileModeChanged.connect(function () {
        onStateEvent(window);
    });
    window.closed.connect(function () {
        lastProcessed.delete(window);
    });
}

function main() {
    workspace.windowList().forEach(function (window) {
        connectWindow(window);
    });
    workspace.windowAdded.connect(function (window) {
        connectWindow(window);
    });
}

main();
