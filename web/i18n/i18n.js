(function () {
    const DEFAULT_LOCALE = "zh_cn";
    const SUPPORTED_LOCALES = ["zh_cn", "zh_tw", "en_us", "ru_ru", "de_de"];
    const HTML_LANG_MAP = {
        zh_cn: "zh-CN",
        zh_tw: "zh-TW",
        en_us: "en-US",
        ru_ru: "ru-RU",
        de_de: "de-DE"
    };
    const LOCALE_NAMES = {
        zh_cn: "简体中文",
        zh_tw: "繁體中文",
        en_us: "English",
        ru_ru: "Русский",
        de_de: "Deutsch"
    };
    const ONLINE_FEATURE_LOCALES = ["zh_cn", "zh_tw"];

    function normalize_locale(locale) {
        return SUPPORTED_LOCALES.includes(locale) ? locale : DEFAULT_LOCALE;
    }

    window.I18N = {
        currentLocale: DEFAULT_LOCALE,
        _messages: {},

        register(locale, messages) {
            const normalized = normalize_locale(locale);
            this._messages[normalized] = { ...(this._messages[normalized] || {}), ...(messages || {}) };
        },

        t(key, params) {
            const dict = this._messages[this.currentLocale] || {};
            const fallback = this._messages[DEFAULT_LOCALE] || {};
            let text = dict[key];
            if (text === undefined) text = fallback[key];
            if (text === undefined) return key;
            if (params && typeof text === "string") {
                Object.keys(params).forEach((name) => {
                    text = text.replace(new RegExp("\\{" + name + "\\}", "g"), String(params[name]));
                });
            }
            return text;
        },

        applyToDOM(root) {
            const scope = root || document;
            if (!scope || typeof scope.querySelectorAll !== "function") return;

            scope.querySelectorAll("[data-i18n]").forEach((el) => {
                const key = el.getAttribute("data-i18n");
                if (key) el.textContent = this.t(key);
            });

            scope.querySelectorAll("[data-i18n-html]").forEach((el) => {
                const key = el.getAttribute("data-i18n-html");
                if (key) el.innerHTML = this.t(key);
            });

            scope.querySelectorAll("[data-i18n-title]").forEach((el) => {
                const key = el.getAttribute("data-i18n-title");
                if (key) el.title = this.t(key);
            });

            scope.querySelectorAll("[data-i18n-aria-label]").forEach((el) => {
                const key = el.getAttribute("data-i18n-aria-label");
                if (key) el.setAttribute("aria-label", this.t(key));
            });

            scope.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
                const key = el.getAttribute("data-i18n-placeholder");
                if (key) el.placeholder = this.t(key);
            });

            scope.querySelectorAll("[data-i18n-alt]").forEach((el) => {
                const key = el.getAttribute("data-i18n-alt");
                if (key) el.alt = this.t(key);
            });
        },

        setLocale(locale) {
            const normalized = normalize_locale(locale);
            this.currentLocale = normalized;
            document.documentElement.lang = HTML_LANG_MAP[normalized] || HTML_LANG_MAP[DEFAULT_LOCALE];
            this.applyToDOM();
            return normalized;
        },

        getLocaleName(locale) {
            return LOCALE_NAMES[normalize_locale(locale)] || LOCALE_NAMES[DEFAULT_LOCALE];
        },

        getKeys(locale) {
            const normalized = normalize_locale(locale || this.currentLocale || DEFAULT_LOCALE);
            return Object.keys(this._messages[normalized] || {}).sort();
        },

        isOnlineFeatureAvailable() {
            return ONLINE_FEATURE_LOCALES.includes(this.currentLocale);
        }
    };
})();
