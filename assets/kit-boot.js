// The last team's colours, applied before the page first draws.
//
// assets/kit.js paints a team's pages and keeps the result in this browser.
// Modules run after the first paint, so without this every one of that
// team's pages would open in the house navy and then change colour. A plain
// script in <head> runs before anything is drawn. It only ever sets the
// properties kit.js writes, and only to colours, whatever storage holds.
(function () {
    try {
        var tokens = JSON.parse(localStorage.getItem('piq-kit') || 'null');
        if (!tokens || typeof tokens !== 'object') return;
        var root = document.documentElement.style;
        for (var name in tokens) {
            var value = String(tokens[name]);
            if (/^--(kit|accent|on-accent)[a-z0-9-]*$/.test(name)
                && /^(#[0-9a-f]{6}|rgba\([0-9., ]+\))$/.test(value)) {
                root.setProperty(name, value);
            }
        }
    } catch (e) {
        // No storage, or something unreadable in it: the house kit stands.
    }
})();
