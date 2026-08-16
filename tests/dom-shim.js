// A DOM small enough to read, real enough to load a page.
//
// Two bugs in two days got past every suite in this repo: the live-tagging tool
// called a function a previous commit had deleted and was dead for eight days,
// and three section rails closed over the first subject opened and went on
// showing it. Both were found by opening the page in a browser, because
// `tests/video.test.js` cannot import a module that touches the DOM and the
// emulator suites drive Firestore without ever loading one.
//
// The roadmap said closing that gap needed a headless browser or a DOM shim and
// that "both are dependencies this repo does not have". Half of that was wrong.
// A shim is code, and the surface these pages actually use is small: 171
// `createElement`s, a hundred-odd `querySelector`s over class names, and
// `innerHTML` with a literal string in it. That is what is implemented here and
// nothing else.
//
// What it is NOT: a renderer. There is no layout, no cascade, no reflow, so
// nothing here can tell you a column has collapsed. Inline styles ARE written
// through to the attribute, so markup taken out of here and rendered against
// the real stylesheet keeps its bar widths — which is how a chart gets looked at.
// It answers "did this code run, and did it put the right things in the right
// places" — which is the question both of those bugs failed.

const VOID = new Set([
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
    'param', 'source', 'track', 'wbr',
]);

// Content is text, not markup, until the matching close tag.
const RAW_TEXT = new Set(['script', 'style']);

const ENTITIES = {
    amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: '\u00a0',
    middot: '\u00b7', times: '\u00d7', mdash: '\u2014', ndash: '\u2013',
    hellip: '\u2026', rsquo: '\u2019', lsquo: '\u2018', ldquo: '\u201c',
    rdquo: '\u201d', deg: '\u00b0', prime: '\u2032',
};

function decodeEntities(text) {
    if (!text.includes('&')) return text;
    return text.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (whole, body) => {
        if (body[0] === '#') {
            const code = body[1] === 'x' || body[1] === 'X'
                ? parseInt(body.slice(2), 16)
                : parseInt(body.slice(1), 10);
            return Number.isFinite(code) ? String.fromCodePoint(code) : whole;
        }
        return ENTITIES[body] ?? whole;
    });
}

const escapeText = (text) => String(text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// ------------------------------------------------------------------- nodes

class TextNode {
    constructor(text) {
        this.nodeType = 3;
        this.data = String(text);
        this.parentNode = null;
    }

    get textContent() { return this.data; }

    set textContent(value) { this.data = String(value); }

    get outerHTML() { return escapeText(this.data); }

    remove() { this.parentNode?.removeChild(this); }
}

class ClassList {
    constructor(el) { this.el = el; }

    get _set() {
        const raw = this.el.getAttribute('class') || '';
        return raw.split(/\s+/).filter(Boolean);
    }

    _write(list) {
        this.el.setAttribute('class', [...new Set(list)].join(' '));
    }

    add(...names) { this._write([...this._set, ...names.filter(Boolean)]); }

    remove(...names) {
        this._write(this._set.filter((n) => !names.includes(n)));
    }

    contains(name) { return this._set.includes(name); }

    /**
     * `toggle(name, force)` — and `force` is deliberately not coerced with a
     * bare `if`. Half this codebase calls it as `toggle('hidden', !thing)`
     * where `thing` can be undefined, and a shim that treated an explicit
     * `undefined` as "flip it" would disagree with the browser exactly when a
     * value went missing, which is the case worth testing.
     */
    toggle(name, force) {
        const on = force === undefined ? !this.contains(name) : !!force;
        if (on) this.add(name); else this.remove(name);
        return on;
    }

    get length() { return this._set.length; }

    item(i) { return this._set[i] ?? null; }

    toString() { return this._set.join(' '); }

    [Symbol.iterator]() { return this._set[Symbol.iterator](); }
}

// Attributes that are also live properties, because page code sets them both
// ways. Anything not here is reachable through get/setAttribute only.
const REFLECTED = [
    'href', 'src', 'type', 'title', 'alt', 'placeholder', 'name', 'target',
    'rel', 'role', 'colspan', 'rowspan', 'min', 'max', 'step',
];

class Element {
    constructor(tag, ownerDocument, namespace = null) {
        this.nodeType = 1;
        this.localName = String(tag).toLowerCase();
        this.tagName = namespace ? String(tag) : this.localName.toUpperCase();
        this.namespaceURI = namespace;
        this.ownerDocument = ownerDocument;
        this.attributes = new Map();
        this.childNodes = [];
        this.parentNode = null;
        this.classList = new ClassList(this);
        this.style = makeStyle(this);
        this.dataset = makeDataset(this);
        this._listeners = new Map();
        this._value = undefined;
        this.checked = false;
        this.disabled = false;
        this.selected = false;
        // Numbers, not reflected attributes: a pitch is drawn with
        // `canvas.width / 2` all over it, and a shim handing back '' would turn
        // every coordinate into NaN and draw a plausible-looking nothing.
        if (this.localName === 'canvas' || this.localName === 'img') {
            this.width = 300;
            this.height = 150;
        }
    }

    /**
     * A 2D context that draws nothing and remembers everything. There is no
     * raster here, so a test can ask whether the pitch was drawn and with what
     * — never what it looks like.
     */
    getContext(kind) {
        if (kind !== '2d') return null;
        if (!this._ctx) this._ctx = makeCanvasContext(this);
        return this._ctx;
    }

    // ---------------------------------------------------------- attributes

    setAttribute(name, value) {
        this.attributes.set(String(name).toLowerCase(), String(value));
    }

    getAttribute(name) {
        const key = String(name).toLowerCase();
        return this.attributes.has(key) ? this.attributes.get(key) : null;
    }

    hasAttribute(name) { return this.attributes.has(String(name).toLowerCase()); }

    removeAttribute(name) { this.attributes.delete(String(name).toLowerCase()); }

    toggleAttribute(name, force) {
        const on = force === undefined ? !this.hasAttribute(name) : !!force;
        if (on) this.setAttribute(name, ''); else this.removeAttribute(name);
        return on;
    }

    get id() { return this.getAttribute('id') || ''; }

    set id(value) { this.setAttribute('id', value); }

    get className() { return this.getAttribute('class') || ''; }

    set className(value) { this.setAttribute('class', value); }

    get value() {
        if (this._value !== undefined) return this._value;
        return this.getAttribute('value') ?? '';
    }

    set value(v) { this._value = v == null ? '' : String(v); }

    get hidden() { return this.hasAttribute('hidden'); }

    set hidden(on) { this.toggleAttribute('hidden', on); }

    // ---------------------------------------------------------------- tree

    get children() { return this.childNodes.filter((n) => n.nodeType === 1); }

    get childElementCount() { return this.children.length; }

    get firstChild() { return this.childNodes[0] ?? null; }

    get lastChild() { return this.childNodes[this.childNodes.length - 1] ?? null; }

    get firstElementChild() { return this.children[0] ?? null; }

    get lastElementChild() {
        const kids = this.children;
        return kids[kids.length - 1] ?? null;
    }

    get parentElement() {
        return this.parentNode?.nodeType === 1 ? this.parentNode : null;
    }

    _siblings() { return this.parentNode ? this.parentNode.childNodes : []; }

    get nextSibling() {
        const kids = this._siblings();
        return kids[kids.indexOf(this) + 1] ?? null;
    }

    get previousSibling() {
        const kids = this._siblings();
        const at = kids.indexOf(this);
        return at > 0 ? kids[at - 1] : null;
    }

    get nextElementSibling() {
        const kids = this.parentElement?.children || [];
        return kids[kids.indexOf(this) + 1] ?? null;
    }

    _adopt(node) {
        const child = typeof node === 'string' || typeof node === 'number'
            ? new TextNode(node)
            : node;
        child.parentNode?.removeChild(child);
        child.parentNode = this;
        return child;
    }

    appendChild(node) {
        if (node instanceof Fragment) {
            for (const child of [...node.childNodes]) this.appendChild(child);
            return node;
        }
        const child = this._adopt(node);
        this.childNodes.push(child);
        return child;
    }

    append(...nodes) { for (const n of nodes) this.appendChild(n); }

    prepend(...nodes) {
        for (const [i, n] of nodes.entries()) {
            this.childNodes.splice(i, 0, this._adopt(n));
        }
    }

    insertBefore(node, ref) {
        if (!ref) return this.appendChild(node);
        const at = this.childNodes.indexOf(ref);
        const child = this._adopt(node);
        this.childNodes.splice(at < 0 ? this.childNodes.length : at, 0, child);
        return child;
    }

    removeChild(node) {
        const at = this.childNodes.indexOf(node);
        if (at >= 0) this.childNodes.splice(at, 1);
        node.parentNode = null;
        return node;
    }

    remove() { this.parentNode?.removeChild(this); }

    replaceWith(...nodes) {
        const parent = this.parentNode;
        if (!parent) return;
        const at = parent.childNodes.indexOf(this);
        parent.childNodes.splice(at, 1, ...nodes.map((n) => parent._adopt(n)));
        this.parentNode = null;
    }

    before(...nodes) {
        for (const n of nodes) this.parentNode?.insertBefore(n, this);
    }

    after(...nodes) {
        const next = this.nextSibling;
        for (const n of nodes) this.parentNode?.insertBefore(n, next);
    }

    // ------------------------------------------------------------- content

    get textContent() {
        return this.childNodes.map((n) => n.textContent).join('');
    }

    set textContent(value) {
        for (const child of this.childNodes) child.parentNode = null;
        this.childNodes = [];
        if (value !== '' && value != null) this.appendChild(new TextNode(value));
    }

    get innerText() { return this.textContent; }

    set innerText(value) { this.textContent = value; }

    get innerHTML() {
        return this.childNodes.map((n) => n.outerHTML).join('');
    }

    set innerHTML(html) {
        for (const child of this.childNodes) child.parentNode = null;
        this.childNodes = [];
        for (const node of parseHTML(String(html), this.ownerDocument)) {
            this.appendChild(node);
        }
    }

    get outerHTML() {
        const attrs = [...this.attributes]
            .map(([k, v]) => ` ${k}="${String(v).replace(/"/g, '&quot;')}"`)
            .join('');
        if (VOID.has(this.localName)) return `<${this.localName}${attrs}>`;
        return `<${this.localName}${attrs}>${this.innerHTML}</${this.localName}>`;
    }

    insertAdjacentHTML(where, html) {
        const nodes = parseHTML(String(html), this.ownerDocument);
        if (where === 'beforeend') this.append(...nodes);
        else if (where === 'afterbegin') this.prepend(...nodes);
        else if (where === 'beforebegin') this.before(...nodes);
        else this.after(...nodes);
    }

    // ------------------------------------------------------------ matching

    matches(selector) { return matchesSelector(this, selector); }

    closest(selector) {
        let node = this;
        while (node && node.nodeType === 1) {
            if (node.matches(selector)) return node;
            node = node.parentNode;
        }
        return null;
    }

    querySelector(selector) { return querySelectorAll(this, selector)[0] ?? null; }

    querySelectorAll(selector) { return querySelectorAll(this, selector); }

    // -------------------------------------------------------------- events

    addEventListener(type, fn) {
        if (!this._listeners.has(type)) this._listeners.set(type, new Set());
        this._listeners.get(type).add(fn);
    }

    removeEventListener(type, fn) { this._listeners.get(type)?.delete(fn); }

    dispatchEvent(event) {
        // Bubbles, because delegated handlers on a list container are how most
        // of the rows on these pages are wired, and a shim that did not bubble
        // would report every one of them as dead.
        event.target = event.target || this;
        let node = this;
        while (node) {
            event.currentTarget = node;
            for (const fn of [...(node._listeners?.get(event.type) || [])]) {
                fn.call(node, event);
            }
            if (event.cancelBubble || event.bubbles === false) break;
            node = node.parentNode;
        }
        return !event.defaultPrevented;
    }

    click() {
        return this.dispatchEvent(new FakeEvent('click', { bubbles: true }));
    }

    focus() { this.ownerDocument.activeElement = this; }

    blur() {
        if (this.ownerDocument.activeElement === this) {
            this.ownerDocument.activeElement = this.ownerDocument.body;
        }
    }

    scrollIntoView() {}

    // Zero, and deliberately so: there is no layout here, and a made-up
    // rectangle would let a test claim something about a size nothing measured.
    getBoundingClientRect() {
        return { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 };
    }
}

for (const name of REFLECTED) {
    Object.defineProperty(Element.prototype, name, {
        get() { return this.getAttribute(name) ?? ''; },
        set(v) { this.setAttribute(name, v); },
        configurable: true,
    });
}

class Fragment extends Element {
    constructor(ownerDocument) {
        super('#fragment', ownerDocument);
        this.nodeType = 11;
    }
}

class FakeEvent {
    constructor(type, init = {}) {
        this.type = type;
        this.bubbles = init.bubbles ?? false;
        this.detail = init.detail;
        this.key = init.key;
        this.target = null;
        this.currentTarget = null;
        this.defaultPrevented = false;
        this.cancelBubble = false;
        Object.assign(this, init);
    }

    preventDefault() { this.defaultPrevented = true; }

    stopPropagation() { this.cancelBubble = true; }
}

const CANVAS_OPS = [
    'save', 'restore', 'beginPath', 'closePath', 'moveTo', 'lineTo', 'arc',
    'rect', 'fill', 'stroke', 'fillRect', 'strokeRect', 'clearRect', 'drawImage',
    'setLineDash', 'translate', 'rotate', 'scale', 'fillText', 'strokeText',
    'quadraticCurveTo', 'bezierCurveTo', 'ellipse', 'clip', 'setTransform',
];

function makeCanvasContext(canvas) {
    const calls = [];
    const ctx = {
        canvas,
        calls,
        fillStyle: '#000', strokeStyle: '#000', lineWidth: 1, lineCap: 'butt',
        lineJoin: 'miter', globalAlpha: 1, font: '10px sans-serif',
        textAlign: 'start', textBaseline: 'alphabetic', globalCompositeOperation: 'source-over',
        measureText: (text) => ({ width: String(text).length * 6 }),
        createLinearGradient: () => ({ addColorStop() {} }),
        createRadialGradient: () => ({ addColorStop() {} }),
        getImageData: (_x, _y, w, h) => ({
            width: w, height: h, data: new Uint8ClampedArray(w * h * 4),
        }),
        putImageData() {},
        createImageData: (w, h) => ({
            width: w, height: h, data: new Uint8ClampedArray(w * h * 4),
        }),
    };
    for (const op of CANVAS_OPS) {
        ctx[op] = (...args) => { calls.push([op, ...args]); };
    }
    return ctx;
}

const cssName = (key) => String(key).replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);

/**
 * `el.style`, written through to the `style` attribute.
 *
 * Not a detail: half the charts in this codebase are a `<i>` whose width is set
 * in JavaScript, and a style object that lived only in memory made `outerHTML`
 * hand back every bar collapsed to nothing. That is invisible while a test only
 * reads numbers and actively misleading the moment the markup is taken out and
 * rendered against the real stylesheet — which is the one way a bar chart can
 * be checked from here at all.
 */
function makeStyle(el = null) {
    const store = {};
    const flush = () => {
        if (!el) return;
        const css = Object.entries(store)
            .filter(([, value]) => value !== '')
            .map(([key, value]) => `${cssName(key)}: ${value}`)
            .join('; ');
        if (css) el.setAttribute('style', css);
        else el.removeAttribute('style');
    };

    return new Proxy(store, {
        get(target, key) {
            if (key === 'setProperty') {
                return (k, v) => {
                    target[String(k)] = v == null ? '' : String(v);
                    flush();
                };
            }
            if (key === 'removeProperty') {
                return (k) => { delete target[String(k)]; flush(); };
            }
            if (key === 'getPropertyValue') return (k) => target[String(k)] ?? '';
            if (key === 'cssText') return el?.getAttribute('style') ?? '';
            return target[key] ?? '';
        },
        set(target, key, value) {
            target[key] = value == null ? '' : String(value);
            flush();
            return true;
        },
    });
}

const dashed = (key) => `data-${key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`;

function makeDataset(el) {
    return new Proxy({}, {
        get: (_t, key) => (typeof key === 'string'
            ? (el.getAttribute(dashed(key)) ?? undefined) : undefined),
        set: (_t, key, value) => { el.setAttribute(dashed(key), value); return true; },
        has: (_t, key) => el.hasAttribute(dashed(key)),
        deleteProperty: (_t, key) => { el.removeAttribute(dashed(key)); return true; },
        ownKeys: () => [...el.attributes.keys()]
            .filter((k) => k.startsWith('data-'))
            .map((k) => k.slice(5).replace(/-([a-z])/g, (_m, c) => c.toUpperCase())),
        getOwnPropertyDescriptor: () => ({ enumerable: true, configurable: true }),
    });
}

// ------------------------------------------------------------------ parser

const TAG_RE = new RegExp([
    '<!--[\\s\\S]*?-->',
    '<!DOCTYPE[^>]*>',
    '<\\/([a-zA-Z][\\w:.-]*)\\s*>',
    '<([a-zA-Z][\\w:.-]*)((?:\\s+[^\\s/>"\'=]+(?:\\s*=\\s*(?:"[^"]*"|\'[^\']*\'|[^\\s>]*))?)*)\\s*(\\/?)>',
].join('|'), 'gi');

const ATTR_RE = /([^\s/>"'=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]*)))?/g;

/** Markup in, a flat list of top-level nodes out. Tolerant, like a browser. */
export function parseHTML(html, ownerDocument) {
    const root = new Fragment(ownerDocument);
    const stack = [root];
    const top = () => stack[stack.length - 1];
    let at = 0;
    let match;

    TAG_RE.lastIndex = 0;
    while ((match = TAG_RE.exec(html)) !== null) {
        if (match.index > at) {
            const text = html.slice(at, match.index);
            if (text.trim() || text.includes('\u00a0')) {
                top().appendChild(new TextNode(decodeEntities(text)));
            }
        }
        at = TAG_RE.lastIndex;

        const [whole, closeName, openName, attrText, selfClose] = match;
        if (whole.startsWith('<!')) continue;

        if (closeName) {
            const want = closeName.toLowerCase();
            // Unwind to the matching open tag, which is what makes a stray
            // </div> in a template string harmless rather than fatal.
            const depth = stack.findLastIndex((el) => el.localName === want);
            if (depth > 0) stack.length = depth;
            continue;
        }

        const el = ownerDocument.createElement(openName);
        if (attrText) {
            ATTR_RE.lastIndex = 0;
            let attr;
            while ((attr = ATTR_RE.exec(attrText)) !== null) {
                const value = attr[2] ?? attr[3] ?? attr[4] ?? '';
                el.setAttribute(attr[1], decodeEntities(value));
            }
        }
        top().appendChild(el);

        if (selfClose || VOID.has(el.localName)) continue;

        if (RAW_TEXT.has(el.localName)) {
            const end = html.toLowerCase().indexOf(`</${el.localName}`, at);
            const body = html.slice(at, end < 0 ? html.length : end);
            if (body.trim()) el.appendChild(new TextNode(body));
            at = end < 0 ? html.length : html.indexOf('>', end) + 1;
            TAG_RE.lastIndex = at;
            continue;
        }
        stack.push(el);
    }

    const tail = html.slice(at);
    if (tail.trim()) root.appendChild(new TextNode(decodeEntities(tail)));
    return [...root.childNodes].map((n) => { n.parentNode = null; return n; });
}

// ---------------------------------------------------------------- selectors
//
// Descendant combinator only, over tag / #id / .class / [attr] / [attr="v"] /
// :last-child. That is every selector this codebase uses; anything richer would
// be a parser nobody in the repo needs.

function parseSimple(part) {
    const test = { tag: null, id: null, classes: [], attrs: [], lastChild: false };
    const re = /([.#]?[\w-]+)|(\[[^\]]*\])|(:[\w-]+)/g;
    let m;
    while ((m = re.exec(part)) !== null) {
        const token = m[0];
        if (token.startsWith('#')) test.id = token.slice(1);
        else if (token.startsWith('.')) test.classes.push(token.slice(1));
        else if (token.startsWith('[')) {
            const inner = token.slice(1, -1);
            const eq = inner.indexOf('=');
            if (eq < 0) test.attrs.push([inner.trim(), null]);
            else {
                test.attrs.push([
                    inner.slice(0, eq).trim(),
                    inner.slice(eq + 1).trim().replace(/^["']|["']$/g, ''),
                ]);
            }
        } else if (token.startsWith(':')) {
            if (token === ':last-child') test.lastChild = true;
            // Any other pseudo-class matches nothing rather than everything: a
            // shim that silently ignored `:hover` would report a selector as
            // matching rows it could never reach in a browser.
            else test.impossible = true;
        } else test.tag = token.toLowerCase();
    }
    return test;
}

function matchesSimple(el, test) {
    if (test.impossible) return false;
    if (test.tag && test.tag !== '*' && el.localName !== test.tag) return false;
    if (test.id && el.id !== test.id) return false;
    for (const cls of test.classes) if (!el.classList.contains(cls)) return false;
    for (const [name, value] of test.attrs) {
        if (!el.hasAttribute(name)) return false;
        if (value !== null && el.getAttribute(name) !== value) return false;
    }
    if (test.lastChild && el.parentElement?.lastElementChild !== el) return false;
    return true;
}

const compiled = new Map();

function compile(selector) {
    if (!compiled.has(selector)) {
        compiled.set(selector, String(selector).split(',').map((branch) =>
            branch.trim().split(/\s+/).filter(Boolean).map(parseSimple)));
    }
    return compiled.get(selector);
}

export function matchesSelector(el, selector) {
    return compile(selector).some((chain) => {
        if (!matchesSimple(el, chain[chain.length - 1])) return false;
        let node = el.parentElement;
        let want = chain.length - 2;
        while (want >= 0 && node) {
            if (matchesSimple(node, chain[want])) want -= 1;
            node = node.parentElement;
        }
        return want < 0;
    });
}

function querySelectorAll(root, selector) {
    const found = [];
    const walk = (node) => {
        for (const child of node.children) {
            if (matchesSelector(child, selector)) found.push(child);
            walk(child);
        }
    };
    walk(root);
    return found;
}

// ----------------------------------------------------------------- document

class FakeDocument extends Element {
    constructor() {
        super('#document', null);
        this.nodeType = 9;
        this.ownerDocument = this;
        this.documentElement = this.createElement('html');
        this.head = this.createElement('head');
        this.body = this.createElement('body');
        this.documentElement.append(this.head, this.body);
        this.appendChild(this.documentElement);
        this.activeElement = this.body;
        this.title = '';
    }

    createElement(tag) { return new Element(tag, this); }

    createElementNS(ns, tag) { return new Element(tag, this, ns); }

    createTextNode(text) { return new TextNode(text); }

    createDocumentFragment() { return new Fragment(this); }

    /**
     * Depth-first, first match wins — and null for an id that is not in the
     * page, which is the whole reason the real HTML is parsed rather than
     * conjuring an element for every id asked for. `byId('typo')` has to come
     * back empty here exactly as it does in a browser.
     */
    getElementById(id) {
        return this.querySelector(`#${CSS.escape(String(id))}`);
    }

    getElementsByClassName(name) { return this.querySelectorAll(`.${name}`); }

    getElementsByTagName(name) { return this.querySelectorAll(name); }
}

const CSS = { escape: (value) => String(value).replace(/([^\w-])/g, '\\$1') };

// ------------------------------------------------------------------ install

/**
 * Install a document parsed from `html`, plus the handful of browser globals
 * these pages read. Returns the pieces a test needs to drive the page.
 */
export function installDom(html, { url = 'http://localhost:5000/' } = {}) {
    const document = new FakeDocument();
    for (const node of parseHTML(html, document)) {
        if (node.nodeType !== 1) continue;
        if (node.localName === 'html') {
            for (const child of [...node.childNodes]) {
                if (child.localName === 'head') document.head.append(...child.childNodes);
                else if (child.localName === 'body') document.body.append(...child.childNodes);
                else document.body.appendChild(child);
            }
        } else if (node.localName === 'head') document.head.append(...node.childNodes);
        else if (node.localName === 'body') document.body.append(...node.childNodes);
        else document.body.appendChild(node);
    }

    const parsed = new URL(url);
    const location = {
        href: url,
        origin: parsed.origin,
        protocol: parsed.protocol,
        hostname: parsed.hostname,
        host: parsed.host,
        port: parsed.port,
        pathname: parsed.pathname,
        search: parsed.search,
        hash: parsed.hash,
        assign(next) { this.href = String(next); },
        replace(next) { this.href = String(next); },
        reload() {},
        toString() { return this.href; },
    };

    const storage = () => {
        const map = new Map();
        return {
            getItem: (k) => (map.has(String(k)) ? map.get(String(k)) : null),
            setItem: (k, v) => map.set(String(k), String(v)),
            removeItem: (k) => map.delete(String(k)),
            clear: () => map.clear(),
            key: (i) => [...map.keys()][i] ?? null,
            get length() { return map.size; },
        };
    };

    const listeners = new Map();
    const frames = [];
    const win = {
        document,
        location,
        innerWidth: 1280,
        innerHeight: 800,
        scrollX: 0,
        scrollY: 0,
        devicePixelRatio: 1,
        addEventListener(type, fn) {
            if (!listeners.has(type)) listeners.set(type, new Set());
            listeners.get(type).add(fn);
        },
        removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
        dispatchEvent(event) {
            for (const fn of [...(listeners.get(event.type) || [])]) fn(event);
            return true;
        },
        scrollTo() {},
        scroll() {},
        print() { win._printed = (win._printed || 0) + 1; },
        matchMedia: (query) => ({
            matches: false, media: query,
            addEventListener() {}, removeEventListener() {},
            addListener() {}, removeListener() {},
        }),
        getComputedStyle: () => makeStyle(),
        // Registered and never fired. There is no compositor here, so the next
        // frame genuinely never arrives — and a shim that fired it off a timer
        // instead turns a page's render loop into an infinite loop inside the
        // test process, which then throws against whatever document is
        // installed by the time it comes round again. Drive one frame by hand
        // with `_frames.next()`; the xG sandbox exposes `_sandbox.frame()` for
        // exactly this reason.
        requestAnimationFrame(fn) { frames.push(fn); return frames.length; },
        cancelAnimationFrame(id) { frames[id - 1] = null; },
        _frames: {
            get pending() { return frames.filter(Boolean).length; },
            next(now = 16) {
                const due = frames.filter(Boolean);
                frames.length = 0;
                for (const fn of due) fn(now);
            },
        },
        alert() {},
        // `confirm` answering yes would run destructive paths unasked; these
        // pages guard "remove this player" with it.
        confirm: () => false,
        localStorage: storage(),
        sessionStorage: storage(),
        history: { pushState() {}, replaceState() {}, back() {}, go() {} },
        _printed: 0,
    };
    win.window = win;
    win.self = win;
    win.top = win;

    const navigator = { onLine: true, userAgent: 'PitchIQ test shim', language: 'en' };

    // Timers belong to the page, and are cancelled when it closes. Two reasons,
    // and the second is the one that matters: a `node --test` process will not
    // exit while the live-tagging tool's match clock is still ticking, and a
    // timer that outlives its document fires against whatever page is loaded
    // next — which is a failure in the wrong test about the wrong page.
    const timers = new Set();
    const track = (real, cancel) => (...args) => {
        const id = real(...args);
        timers.add([id, cancel]);
        return id;
    };
    const clocks = {
        setTimeout: track(setTimeout, clearTimeout),
        setInterval: track(setInterval, clearInterval),
        clearTimeout: (id) => clearTimeout(id),
        clearInterval: (id) => clearInterval(id),
    };
    Object.assign(win, clocks);

    const globals = {
        ...clocks,
        window: win, document, location, navigator, CSS,
        localStorage: win.localStorage,
        sessionStorage: win.sessionStorage,
        history: win.history,
        HTMLElement: Element, Element, Node: Element, Text: TextNode,
        Event: FakeEvent, CustomEvent: FakeEvent, KeyboardEvent: FakeEvent,
        MouseEvent: FakeEvent, DocumentFragment: Fragment,
        requestAnimationFrame: win.requestAnimationFrame,
        cancelAnimationFrame: win.cancelAnimationFrame,
        getComputedStyle: win.getComputedStyle,
        matchMedia: win.matchMedia,
        alert: win.alert, confirm: win.confirm,
        Image: class { constructor() { this.src = ''; this.onload = null; } },
        IntersectionObserver: class {
            observe() {} unobserve() {} disconnect() {}
        },
        ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
        MutationObserver: class { observe() {} disconnect() {} takeRecords() { return []; } },
    };

    const saved = new Map();
    for (const [name, value] of Object.entries(globals)) {
        saved.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
        Object.defineProperty(globalThis, name, {
            value, writable: true, configurable: true,
        });
    }

    const restore = () => {
        for (const [id, cancel] of timers) cancel(id);
        timers.clear();
        frames.length = 0;
        for (const [name, descriptor] of saved) {
            if (descriptor) Object.defineProperty(globalThis, name, descriptor);
            else delete globalThis[name];
        }
    };

    return { window: win, document, location, navigator, restore, FakeEvent };
}

export { Element, TextNode, FakeEvent, FakeDocument };
