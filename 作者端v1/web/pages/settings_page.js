window.AuthorPageModules = window.AuthorPageModules || {};

window.AuthorPageModules.settings = {
    init(app) {
        this._app = app;
    },

    async onEnter(_app) {
        try {
            const data = await window.pywebview?.api?.get_voicepack_workspace?.();
            if (!data) return;

            const workspaceEl = document.getElementById("settings-workspace-dir");
            const libEl = document.getElementById("settings-library-dir");
            if (workspaceEl) workspaceEl.textContent = String(data.workspace_dir || "-");
            if (libEl) libEl.textContent = String(data.library_dir || "-");
        } catch (_e) {
            // 工作区信息加载失败时静默降级
        }
    }
};
