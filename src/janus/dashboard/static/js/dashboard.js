(function() {
    var root = document.documentElement;
    var body = document.body;
    var sidebar = document.getElementById('sidebar');
    var sidebarOverlay = document.getElementById('sidebar-overlay');
    var mobileOpenButton = document.querySelector('[data-sidebar-open]');
    var collapseButton = document.querySelector('[data-sidebar-collapse]');
    var palette = document.getElementById('command-palette');
    var paletteSearch = document.getElementById('command-search');
    var paletteEmpty = document.getElementById('command-empty');
    var themeMedia = window.matchMedia('(prefers-color-scheme: light)');

    function storedValue(key, fallback) {
        try {
            return localStorage.getItem(key) || fallback;
        } catch (error) {
            return fallback;
        }
    }

    function saveValue(key, value) {
        try {
            localStorage.setItem(key, value);
        } catch (error) {}
    }

    function resolveTheme(preference) {
        if (preference === 'system') return themeMedia.matches ? 'light' : 'dark';
        return preference;
    }

    function updateThemeControls() {
        var preference = root.dataset.themePreference || 'dark';
        document.querySelectorAll('[data-theme-toggle]').forEach(function(button) {
            var labels = {
                dark: 'Dark theme. Click for light theme',
                light: 'Light theme. Click for system theme',
                system: 'System theme. Click for dark theme'
            };
            button.setAttribute('aria-label', labels[preference]);
            button.setAttribute('title', labels[preference]);
        });
    }

    function setTheme(preference) {
        root.dataset.themePreference = preference;
        root.dataset.theme = resolveTheme(preference);
        saveValue('janus-theme', preference);
        updateThemeControls();
        document.dispatchEvent(new CustomEvent('janus:theme', {
            detail: { preference: preference, theme: root.dataset.theme }
        }));
    }

    document.querySelectorAll('[data-theme-toggle]').forEach(function(button) {
        button.addEventListener('click', function() {
            var current = root.dataset.themePreference || 'dark';
            var next = current === 'dark' ? 'light' : current === 'light' ? 'system' : 'dark';
            setTheme(next);
        });
    });

    themeMedia.addEventListener('change', function() {
        if (root.dataset.themePreference === 'system') setTheme('system');
    });

    function setMobileSidebar(open) {
        if (!sidebar || !sidebarOverlay) return;
        sidebar.classList.toggle('open', open);
        sidebarOverlay.classList.toggle('open', open);
        body.classList.toggle('sidebar-open', open);
        if (mobileOpenButton) mobileOpenButton.setAttribute('aria-expanded', String(open));
    }

    if (mobileOpenButton) {
        mobileOpenButton.addEventListener('click', function() {
            setMobileSidebar(true);
        });
    }

    document.querySelectorAll('[data-sidebar-close]').forEach(function(button) {
        button.addEventListener('click', function() {
            setMobileSidebar(false);
        });
    });

    if (sidebar) {
        sidebar.querySelectorAll('a').forEach(function(link) {
            link.addEventListener('click', function() {
                setMobileSidebar(false);
            });
        });
    }

    function setSidebarCollapsed(collapsed) {
        if (!sidebar || !collapseButton) return;
        sidebar.classList.toggle('is-collapsed', collapsed);
        collapseButton.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
        collapseButton.setAttribute('title', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
        saveValue('janus-sidebar', collapsed ? 'collapsed' : 'expanded');
    }

    if (collapseButton) {
        setSidebarCollapsed(storedValue('janus-sidebar', 'expanded') === 'collapsed');
        collapseButton.addEventListener('click', function() {
            setSidebarCollapsed(!sidebar.classList.contains('is-collapsed'));
        });
    }

    function visibleCommandItems() {
        if (!palette) return [];
        return Array.prototype.slice.call(palette.querySelectorAll('.command-item:not([hidden])'));
    }

    function selectCommandItem(index) {
        var items = visibleCommandItems();
        items.forEach(function(item) {
            item.classList.remove('is-selected');
        });
        if (!items.length) return;
        var safeIndex = (index + items.length) % items.length;
        items[safeIndex].classList.add('is-selected');
        items[safeIndex].scrollIntoView({ block: 'nearest' });
    }

    function filterCommands() {
        if (!paletteSearch || !palette) return;
        var query = paletteSearch.value.trim().toLowerCase();
        var visible = 0;
        palette.querySelectorAll('.command-item').forEach(function(item) {
            var matches = item.textContent.toLowerCase().indexOf(query) !== -1;
            item.hidden = !matches;
            item.classList.remove('is-selected');
            if (matches) visible += 1;
        });
        if (paletteEmpty) paletteEmpty.classList.toggle('hidden', visible !== 0);
        selectCommandItem(0);
    }

    function openPalette() {
        if (!palette || !paletteSearch) return;
        palette.classList.remove('hidden');
        body.classList.add('command-open');
        paletteSearch.value = '';
        filterCommands();
        window.requestAnimationFrame(function() {
            paletteSearch.focus();
        });
    }

    function closePalette() {
        if (!palette) return;
        palette.classList.add('hidden');
        body.classList.remove('command-open');
    }

    document.querySelectorAll('[data-command-open]').forEach(function(button) {
        button.addEventListener('click', openPalette);
    });

    document.querySelectorAll('[data-command-close]').forEach(function(button) {
        button.addEventListener('click', closePalette);
    });

    if (paletteSearch) {
        paletteSearch.addEventListener('input', filterCommands);
        paletteSearch.addEventListener('keydown', function(event) {
            var items = visibleCommandItems();
            var selected = items.findIndex(function(item) {
                return item.classList.contains('is-selected');
            });
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                selectCommandItem(selected + 1);
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                selectCommandItem(selected - 1);
            } else if (event.key === 'Enter' && items.length) {
                event.preventDefault();
                var target = items[selected < 0 ? 0 : selected];
                window.location.assign(target.href);
            }
        });
    }

    document.addEventListener('keydown', function(event) {
        var modifier = event.metaKey || event.ctrlKey;
        if (modifier && event.key.toLowerCase() === 'k') {
            event.preventDefault();
            palette && !palette.classList.contains('hidden') ? closePalette() : openPalette();
        } else if (event.key === 'Escape') {
            closePalette();
            setMobileSidebar(false);
        }
    });

    document.body.addEventListener('htmx:beforeRequest', function() {
        body.classList.add('is-requesting');
    });

    document.body.addEventListener('htmx:afterRequest', function() {
        body.classList.remove('is-requesting');
    });

    document.body.addEventListener('htmx:sendError', function() {
        body.classList.remove('is-requesting');
    });

    updateThemeControls();
})();
