// 公有资源拖入遮罩组件：负责拖拽范围提示、格式检查与 drop 回调编排。
(function () {
    const registry = {};
    let active_type = "";

    function normalize_ext(file) {
        const text = String(file?.pywebviewFullPath || file?.path || file?._path || file?.name || "").toLowerCase();
        const index = text.lastIndexOf(".");
        return index >= 0 ? text.slice(index) : "";
    }

    function get_file_path(file) {
        return file?.pywebviewFullPath || file?.path || file?._path || "";
    }

    function get_allowed_file(files, allowed_exts) {
        const allowed = new Set((allowed_exts || []).map(ext => String(ext).toLowerCase()));
        return files.find(file => allowed.has(normalize_ext(file))) || null;
    }

    function is_point_inside(event, target) {
        if (!event || !target) return false;
        const rect = target.getBoundingClientRect();
        return event.clientX >= rect.left
            && event.clientX <= rect.right
            && event.clientY >= rect.top
            && event.clientY <= rect.bottom;
    }

    function build_overlay(config) {
        const overlay = document.createElement("div");
        overlay.className = "resource-drag-overlay";
        overlay.innerHTML = `
            <div class="resource-drag-overlay-panel">
                <div class="resource-drag-overlay-icon">
                    <i class="${config.icon || "ri-upload-cloud-2-line"}"></i>
                </div>
                <div class="resource-drag-overlay-title"></div>
                <div class="resource-drag-overlay-sub"></div>
            </div>
        `;
        overlay.querySelector(".resource-drag-overlay-title").textContent = config.title || t("drop.generic_title");
        overlay.querySelector(".resource-drag-overlay-sub").textContent = config.subtitle || t("drop.generic_subtitle");
        return overlay;
    }

    function t(key, params = {}) {
        if (window.app && typeof app.t === "function") return app.t(key, params);
        if (window.I18N && typeof I18N.t === "function") return I18N.t(key, params);
        return key;
    }

    function get_target(config) {
        return document.querySelector(config.target_selector || "");
    }

    function update_overlay_rect(config, target) {
        if (!config || !config.overlay || !target) return;
        const rect = target.getBoundingClientRect();
        const left = Math.max(0, rect.left);
        const top = Math.max(0, rect.top);
        const right = Math.min(window.innerWidth || rect.right, rect.right);
        const bottom = Math.min(window.innerHeight || rect.bottom, rect.bottom);
        config.overlay.style.left = `${left}px`;
        config.overlay.style.top = `${top}px`;
        config.overlay.style.width = `${Math.max(0, right - left)}px`;
        config.overlay.style.height = `${Math.max(0, bottom - top)}px`;
    }

    function set_active(resource_type, event) {
        const config = registry[resource_type];
        if (!config) return;
        if (typeof config.active_check === "function" && !config.active_check()) return;

        const target = get_target(config);
        if (!target) return;

        if (!config.overlay) {
            config.overlay = build_overlay(config);
        }
        if (config.overlay.parentElement !== document.body) {
            document.body.appendChild(config.overlay);
        }

        const in_range = is_point_inside(event, target);
        target.classList.add("resource-drag-overlay-host");
        target.classList.toggle("resource-drag-overlay-in-range", in_range);
        config.overlay.classList.toggle("is-in-range", in_range);
        update_overlay_rect(config, target);
        config.overlay.classList.add("is-active");
        active_type = resource_type;
    }

    function clear_active(resource_type) {
        const type = resource_type || active_type;
        const config = registry[type];
        if (!config) return;

        const target = get_target(config);
        if (target) {
            target.classList.remove("resource-drag-overlay-in-range");
        }
        if (config.overlay) {
            config.overlay.classList.remove("is-active");
            config.overlay.classList.remove("is-in-range");
        }
        if (!resource_type || active_type === resource_type) {
            active_type = "";
        }
    }

    function can_handle(config) {
        if (!config) return false;
        if (typeof config.active_check === "function") {
            return !!config.active_check();
        }
        return true;
    }

    function mark_backend_drop_handled(config) {
        if (!config || !config.backend_drop_fallback) return;
        window.__resource_drag_drop_handled_at = Date.now();
    }

    function mark_backend_drop_pending(resource_type) {
        window.__resource_backend_drop_pending_at = Date.now();
        window.__resource_backend_drop_type = resource_type || "";
    }

    window.ResourceDragOverlay = {
        register(config) {
            if (!config || !config.resource_type || !config.target_selector) return;
            registry[config.resource_type] = config;
        },

        bind(resource_type) {
            const config = registry[resource_type];
            if (!config || config.bound) return;
            config.bound = true;

            document.addEventListener("dragenter", (event) => {
                if (!can_handle(config)) return;
                event.preventDefault();
                set_active(resource_type, event);
            });

            document.addEventListener("dragover", (event) => {
                if (!can_handle(config)) return;
                event.preventDefault();
                set_active(resource_type, event);
            });

            document.addEventListener("dragleave", (event) => {
                if (!can_handle(config)) return;
                const related = event.relatedTarget;
                if (related && document.documentElement.contains(related)) return;
                clear_active(resource_type);
            });

            document.addEventListener("drop", async (event) => {
                if (!can_handle(config)) return;
                event.preventDefault();
                const target = get_target(config);
                const in_range = is_point_inside(event, target);
                clear_active(resource_type);
                if (!in_range) return;

                const files = Array.from((event.dataTransfer && event.dataTransfer.files) ? event.dataTransfer.files : []);
                const file = get_allowed_file(files, config.allowed_exts);
                if (!file) {
                    if (window.app && typeof app.showAlert === "function") {
                        app.showAlert(t("common.info"), config.invalid_message || t("common.file_format_unsupported"), "warn");
                    }
                    return;
                }

                const file_path = get_file_path(file);
                if (!file_path) {
                    let missing_path_handled = false;
                    if (typeof config.on_missing_path === "function") {
                        try {
                            missing_path_handled = await config.on_missing_path(event, files);
                        } catch (error) {
                            console.error("ResourceDragOverlay on_missing_path failed", error);
                            if (window.app && typeof app.showAlert === "function") {
                                app.showAlert(t("common.error"), config.missing_path_error_message || t("common.import_window_failed"), "error");
                            }
                            return;
                        }
                    }
                    if (missing_path_handled) return;
                    if (typeof config.on_file_drop === "function") {
                        try {
                            mark_backend_drop_handled(config);
                            await config.on_file_drop(file, event, files);
                        } catch (error) {
                            console.error("ResourceDragOverlay on_file_drop failed", error);
                            if (window.app && typeof app.showAlert === "function") {
                                app.showAlert(t("common.error"), config.drop_error_message || t("common.import_failed"), "error");
                            }
                        }
                        return;
                    }
                    if (config.backend_drop_fallback && window.__resource_backend_drop_ready) {
                        mark_backend_drop_pending(resource_type);
                        return;
                    }
                    if (window.app && typeof app.showAlert === "function") {
                        app.showAlert(t("common.info"), config.missing_path_message || t("common.current_env_no_drop_path"), "warn");
                    }
                    return;
                }

                if (typeof config.on_drop === "function") {
                    try {
                        mark_backend_drop_handled(config);
                        await config.on_drop(file_path, file);
                    } catch (error) {
                        console.error("ResourceDragOverlay on_drop failed", error);
                        if (window.app && typeof app.showAlert === "function") {
                            app.showAlert(t("common.error"), config.drop_error_message || t("common.import_failed"), "error");
                        }
                    }
                }
            });
        },

        clear(resource_type) {
            if (!resource_type) {
                Object.keys(registry).forEach(clear_active);
                active_type = "";
                return;
            }
            clear_active(resource_type);
        },
    };
})();
