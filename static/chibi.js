// ============================================================
// NHÂN VẬT CHIBI (Đợt 5) — mỗi tài khoản 1 nhân vật, tự phối:
// kiểu tóc + màu tóc + màu áo + pháp bảo. Vẽ bằng SVG theo lớp
// ngay trên trình duyệt: nhẹ, sắc nét, không cần file ảnh.
// Cấu hình nhân vật: {toc: 0-5, mau_toc: 0-5, ao: 0-7, phap_bao: key}
// ============================================================
(function () {
    const HAIR_STYLES = ["Búi Củ Tỏi", "Tóc Dài", "Mái Ngố", "Hai Búi", "Búi Trâm", "Tóc Xù"];
    const HAIR_COLORS = [
        { name: "Đen", hex: "#2f2a33" }, { name: "Nâu", hex: "#7a4a21" },
        { name: "Bạc", hex: "#c9d2dc" }, { name: "Đỏ", hex: "#b03a2e" },
        { name: "Lam", hex: "#2e5fa3" }, { name: "Tím", hex: "#6c3483" }
    ];
    const ROBE_COLORS = [
        { name: "Áo Lam", hex: "#2563eb" }, { name: "Áo Đỏ", hex: "#dc2626" },
        { name: "Áo Lục", hex: "#16a34a" }, { name: "Áo Tím", hex: "#7c3aed" },
        { name: "Áo Vàng", hex: "#d97706" }, { name: "Áo Hồng", hex: "#db2777" },
        { name: "Áo Trắng", hex: "#e8ecf3" }, { name: "Áo Đen", hex: "#23272f" }
    ];
    const ITEMS = [
        { key: "kiem", name: "Kiếm" }, { key: "quat", name: "Quạt" },
        { key: "ho_lo", name: "Hồ Lô" }, { key: "sach", name: "Sách" },
        { key: "truong", name: "Trượng" }, { key: "khong", name: "Không cầm" }
    ];
    const SKIN = "#ffd9b3";

    function chibiDefault() { return { toc: 0, mau_toc: 0, ao: 0, phap_bao: "sach" }; }

    function shade(hex, f) {
        const n = parseInt(hex.slice(1), 16);
        let r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
        r = Math.max(0, Math.min(255, Math.round(r * f)));
        g = Math.max(0, Math.min(255, Math.round(g * f)));
        b = Math.max(0, Math.min(255, Math.round(b * f)));
        return `rgb(${r},${g},${b})`;
    }

    function hairBack(c, hair) {
        if (c.toc === 1) { // Tóc Dài: hai dải tóc sau vai
            return `<path d="M34 34 Q27 88 33 110 L47 110 Q40 70 43 42 Z" fill="${hair}"/>
                    <path d="M86 34 Q93 88 87 110 L73 110 Q80 70 77 42 Z" fill="${hair}"/>`;
        }
        if (c.toc === 5) { // Tóc Xù: bông hai bên
            return `<circle cx="33" cy="52" r="10" fill="${hair}"/><circle cx="87" cy="52" r="10" fill="${hair}"/>`;
        }
        return "";
    }

    function hairFront(c, hair) {
        const fringe = `<path d="M37 47 Q36 21 60 20 Q84 21 83 47 Q72 33 60 33 Q48 33 37 47 Z" fill="${hair}"/>`;
        switch (c.toc) {
            case 0: return `<circle cx="60" cy="15" r="8.5" fill="${hair}"/>` + fringe;
            case 1: return fringe;
            case 2: return `<path d="M36 49 Q33 19 60 19 Q87 19 84 49 L84 41 Q60 32 36 41 Z" fill="${hair}"/>`;
            case 3: return `<circle cx="37" cy="23" r="8.5" fill="${hair}"/><circle cx="83" cy="23" r="8.5" fill="${hair}"/>` + fringe;
            case 4: return `<circle cx="60" cy="12" r="9" fill="${hair}"/>
                            <line x1="49" y1="9" x2="75" y2="15" stroke="#f59e0b" stroke-width="2.5" stroke-linecap="round"/>
                            <circle cx="76" cy="15" r="3.2" fill="#dc2626"/>` + fringe;
            case 5: return `<circle cx="40" cy="29" r="9" fill="${hair}"/><circle cx="52" cy="21" r="10" fill="${hair}"/>
                            <circle cx="67" cy="20" r="10" fill="${hair}"/><circle cx="79" cy="28" r="9" fill="${hair}"/>
                            <circle cx="60" cy="31" r="12" fill="${hair}"/>`;
        }
        return fringe;
    }

    function itemSVG(c) {
        switch (c.phap_bao) {
            case "kiem": return `<g transform="rotate(16 88 92)">
                    <polygon points="84.5,56 90.5,56 89,95 86,95" fill="#d7dde6" stroke="#9aa5b1" stroke-width="0.8"/>
                    <rect x="81" y="95" width="13" height="3.6" rx="1.6" fill="#f59e0b"/>
                    <rect x="85.6" y="98.6" width="3.4" height="11" rx="1.6" fill="#7c4a21"/></g>`;
            case "quat": return `<path d="M87 114 L71 85 A21 21 0 0 1 103 85 Z" fill="#fbbf24" stroke="#b45309" stroke-width="1.2"/>
                    <line x1="87" y1="114" x2="76" y2="88" stroke="#b45309" stroke-width="1"/>
                    <line x1="87" y1="114" x2="87" y2="84" stroke="#b45309" stroke-width="1"/>
                    <line x1="87" y1="114" x2="98" y2="88" stroke="#b45309" stroke-width="1"/>
                    <line x1="87" y1="114" x2="87" y2="120" stroke="#7c4a21" stroke-width="2.4" stroke-linecap="round"/>`;
            case "ho_lo": return `<circle cx="89" cy="107" r="8.5" fill="#d97706"/>
                    <circle cx="89" cy="94" r="5.8" fill="#f59e0b"/>
                    <rect x="86.8" y="85.5" width="4.4" height="4.5" rx="1" fill="#7c4a21"/>
                    <ellipse cx="86" cy="104" rx="2.4" ry="3.4" fill="#fde68a" opacity="0.7"/>`;
            case "sach": return `<g transform="rotate(-8 28 104)">
                    <rect x="17" y="91" width="21" height="27" rx="2" fill="#2563eb"/>
                    <rect x="20" y="94" width="15" height="21" rx="1" fill="#fef9ec"/>
                    <line x1="23" y1="99" x2="32" y2="99" stroke="#94a3b8" stroke-width="1.4"/>
                    <line x1="23" y1="104" x2="32" y2="104" stroke="#94a3b8" stroke-width="1.4"/>
                    <line x1="23" y1="109" x2="30" y2="109" stroke="#94a3b8" stroke-width="1.4"/></g>`;
            case "truong": return `<line x1="93" y1="46" x2="93" y2="126" stroke="#7c4a21" stroke-width="3.4" stroke-linecap="round"/>
                    <circle cx="93" cy="38" r="7.5" fill="#a78bfa" stroke="#7c3aed" stroke-width="1.6"/>
                    <circle cx="90.5" cy="35.5" r="2.2" fill="#ede9fe"/>`;
            default: return "";
        }
    }

    function chibiSVG(cfg, size) {
        const c = Object.assign(chibiDefault(), cfg || {});
        const hair = (HAIR_COLORS[c.mau_toc] || HAIR_COLORS[0]).hex;
        const robe = (ROBE_COLORS[c.ao] || ROBE_COLORS[0]).hex;
        const robeDark = shade(robe === "#e8ecf3" ? "#c8cfdb" : robe, 0.72);
        const s = size || 80;
        return `<svg width="${s}" height="${Math.round(s * 140 / 120)}" viewBox="0 0 120 140" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Nhân vật chibi">
            ${hairBack(c, hair)}
            <ellipse cx="47" cy="137" rx="9" ry="3.4" fill="#374151"/>
            <ellipse cx="73" cy="137" rx="9" ry="3.4" fill="#374151"/>
            <path d="M44 84 Q33 100 37 117 L46 115 Q43 100 48 88 Z" fill="${robeDark}"/>
            <path d="M76 84 Q87 99 85 114 L76 113 Q74 99 72 88 Z" fill="${robeDark}"/>
            <path d="M38 138 L44 82 Q60 73 76 82 L82 138 Q60 145 38 138 Z" fill="${robe}" stroke="rgba(0,0,0,0.18)" stroke-width="1"/>
            <path d="M51 75 L60 87 L69 75 L69 81 L60 93 L51 81 Z" fill="#fef3c7"/>
            <rect x="42" y="107" width="36" height="7" rx="3" fill="${robeDark}"/>
            <rect x="56" y="107" width="8" height="7" fill="#f59e0b"/>
            <circle cx="37" cy="48" r="5" fill="${SKIN}"/>
            <circle cx="83" cy="48" r="5" fill="${SKIN}"/>
            <circle cx="60" cy="46" r="23" fill="${SKIN}"/>
            <ellipse cx="44" cy="55" rx="4" ry="2.2" fill="#ff9d9d" opacity="0.55"/>
            <ellipse cx="76" cy="55" rx="4" ry="2.2" fill="#ff9d9d" opacity="0.55"/>
            <ellipse cx="51" cy="48" rx="2.6" ry="3.6" fill="#26221f"/>
            <ellipse cx="69" cy="48" rx="2.6" ry="3.6" fill="#26221f"/>
            <circle cx="52" cy="46.4" r="1" fill="#ffffff"/>
            <circle cx="70" cy="46.4" r="1" fill="#ffffff"/>
            <path d="M55 56.5 Q60 61 65 56.5" stroke="#b3543f" stroke-width="2" fill="none" stroke-linecap="round"/>
            ${hairFront(c, hair)}
            ${itemSVG(c)}
        </svg>`;
    }

    // Bộ chọn nhân vật: preview lớn + 4 nhóm lựa chọn. onChange(state) gọi mỗi lần đổi.
    function buildChibiPicker(el, initial, onChange) {
        if (!el) return;
        const state = Object.assign(chibiDefault(), initial || {});
        function optBtn(group, val, inner, title) {
            const on = state[group] === val;
            return `<button type="button" data-group="${group}" data-val="${val}" title="${title || ''}"
                class="flex flex-col items-center gap-0.5 p-1.5 rounded-xl border ${on ? 'border-amber-400 bg-amber-900/70' : 'border-amber-700/40 bg-black/50 hover:border-amber-500/70'} cursor-pointer transition">
                ${inner}<span class="text-[9px] font-bold ${on ? 'text-amber-200' : 'text-amber-300/70'}">${title || ''}</span></button>`;
        }
        function render() {
            const mini = (patch) => chibiSVG(Object.assign({}, state, patch), 40);
            el.innerHTML = `
            <div class="flex flex-col sm:flex-row gap-3">
                <div class="shrink-0 mx-auto text-center">
                    <div class="bg-gradient-to-b from-sky-950/80 to-black/60 border border-amber-500/50 rounded-2xl px-4 pt-2 pb-1 inline-block">${chibiSVG(state, 104)}</div>
                    <p class="text-[10px] text-amber-300/80 mt-1">Xem trước nhân vật</p>
                </div>
                <div class="flex-1 space-y-2.5 text-left">
                    <div><p class="text-[11px] font-extrabold text-amber-300 mb-1">💇 Kiểu tóc</p>
                        <div class="flex flex-wrap gap-1.5">${HAIR_STYLES.map((n, i) => optBtn('toc', i, mini({ toc: i }), n)).join('')}</div></div>
                    <div><p class="text-[11px] font-extrabold text-amber-300 mb-1">🎨 Màu tóc</p>
                        <div class="flex flex-wrap gap-1.5">${HAIR_COLORS.map((col, i) => optBtn('mau_toc', i, `<span class="w-8 h-8 rounded-full border border-white/30" style="background:${col.hex}"></span>`, col.name)).join('')}</div></div>
                    <div><p class="text-[11px] font-extrabold text-amber-300 mb-1">👘 Áo</p>
                        <div class="flex flex-wrap gap-1.5">${ROBE_COLORS.map((col, i) => optBtn('ao', i, mini({ ao: i }), col.name)).join('')}</div></div>
                    <div><p class="text-[11px] font-extrabold text-amber-300 mb-1">✨ Pháp bảo cầm tay</p>
                        <div class="flex flex-wrap gap-1.5">${ITEMS.map(it => optBtn('phap_bao', it.key, mini({ phap_bao: it.key }), it.name)).join('')}</div></div>
                </div>
            </div>`;
            el.querySelectorAll('button[data-group]').forEach(b => {
                b.addEventListener('click', () => {
                    const g = b.dataset.group;
                    state[g] = (g === 'phap_bao') ? b.dataset.val : parseInt(b.dataset.val, 10);
                    render();
                    if (onChange) onChange(Object.assign({}, state));
                });
            });
        }
        render();
        return { get: () => Object.assign({}, state) };
    }

    window.CHIBI = { HAIR_STYLES, HAIR_COLORS, ROBE_COLORS, ITEMS, chibiDefault, chibiSVG, buildChibiPicker };
    window.chibiSVG = chibiSVG;
    window.chibiDefault = chibiDefault;
    window.buildChibiPicker = buildChibiPicker;
})();
