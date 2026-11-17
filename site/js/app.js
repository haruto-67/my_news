const SAVED_KEY = "myness.saved";
const LEGACY_FAVORITES_KEY = "myNewsFavorites";
const WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];
const CAT_COLORS = 6;

const state = {
  dates: [],
  digests: new Map(),
  view: { mode: "day", date: null },
  filter: "all",
  readerPushed: false,
};

const $ = (id) => document.getElementById(id);
const el = {
  editionDate: $("edition-date"),
  editionDay: $("edition-day"),
  dateStrip: $("date-strip"),
  filters: $("filters"),
  feed: $("feed"),
  reader: $("reader"),
  readerScroll: $("reader-scroll"),
  readerArticle: $("reader-article"),
  readerClose: $("reader-close"),
  readerSave: $("reader-save"),
  readerSource: $("reader-source"),
};

/* ---------- helpers ---------- */

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function svgStar(filled) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "20");
  svg.setAttribute("height", "20");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", "M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z");
  path.setAttribute("fill", filled ? "currentColor" : "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.8");
  path.setAttribute("stroke-linejoin", "round");
  svg.append(path);
  return svg;
}

function parseDate(str) {
  const [y, m, d] = str.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function domainOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function catColor(index) {
  return `var(--cat-${index % CAT_COLORS})`;
}

async function fetchJson(path) {
  const res = await fetch(path, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

async function loadDigest(date) {
  if (!state.digests.has(date)) {
    const digest = await fetchJson(`data/${date}.json`);
    state.digests.set(date, digest);
    migrateLegacyFavorites(digest);
  }
  return state.digests.get(date);
}

/* ---------- saved ---------- */

function loadSaved() {
  try {
    return JSON.parse(localStorage.getItem(SAVED_KEY) || "[]");
  } catch {
    return [];
  }
}

function writeSaved(list) {
  try {
    localStorage.setItem(SAVED_KEY, JSON.stringify(list));
  } catch {
    /* storage unavailable */
  }
}

let saved = loadSaved();

function isSaved(url) {
  return saved.some((s) => s.article.url === url);
}

function toggleSaved(entry) {
  if (isSaved(entry.article.url)) {
    saved = saved.filter((s) => s.article.url !== entry.article.url);
  } else {
    saved.unshift({ ...entry, savedAt: Date.now() });
  }
  writeSaved(saved);
}

// 旧版の「お気に入り」(URLのみ保存)を、その記事を含む日を開いた時に引き継ぐ
function migrateLegacyFavorites(digest) {
  let legacy;
  try {
    legacy = new Set(JSON.parse(localStorage.getItem(LEGACY_FAVORITES_KEY) || "[]"));
  } catch {
    return;
  }
  if (!legacy.size) return;
  let changed = false;
  digest.categories.forEach((cat, catIndex) => {
    for (const article of cat.articles) {
      if (legacy.has(article.url)) {
        if (!isSaved(article.url)) {
          saved.push({ date: digest.date, catIndex, catName: cat.name, article, savedAt: 0 });
        }
        legacy.delete(article.url);
        changed = true;
      }
    }
  });
  if (changed) {
    writeSaved(saved);
    try {
      localStorage.setItem(LEGACY_FAVORITES_KEY, JSON.stringify([...legacy]));
    } catch {
      /* ignore */
    }
  }
}

/* ---------- routing ---------- */

function parseHash() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "saved") return { mode: "saved" };
  if (/^\d{4}-\d{2}-\d{2}$/.test(parts[0] || "")) return { mode: "day", date: parts[0], id: parts[1] || null };
  return { mode: "day", date: null, id: null };
}

function go(hash, { replace = false } = {}) {
  if (replace) history.replaceState(null, "", hash);
  else history.pushState(null, "", hash);
  route();
}

async function route() {
  const r = parseHash();

  if (r.mode === "saved") {
    closeReader();
    state.view = { mode: "saved", date: state.view.date || state.dates[0] };
    renderAll();
    return;
  }

  const date = r.date && state.dates.includes(r.date) ? r.date : state.dates[0];

  if (r.id) {
    // 記事を開いた時は、下の一覧は直前の表示のまま残す（初回表示の時だけその日を出す）
    if (!state.view.date) state.view = { mode: "day", date };
    const digest = await loadDigest(r.date || date);
    if (!el.feed.childElementCount) await renderAll();
    const found = findArticle(digest, r.id);
    if (found) openReader({ date: digest.date, ...found });
    return;
  }

  closeReader();
  const changedDate = state.view.mode !== "day" || state.view.date !== date;
  state.view = { mode: "day", date };
  await renderAll();
  if (changedDate) window.scrollTo({ top: 0 });
}

function findArticle(digest, id) {
  for (const [catIndex, cat] of digest.categories.entries()) {
    const article = cat.articles.find((a) => a.id === id);
    if (article) return { article, catIndex, catName: cat.name };
  }
  return null;
}

/* ---------- render: masthead ---------- */

function renderMasthead() {
  const date = state.view.date;
  if (!date) return;
  const d = parseDate(date);
  el.editionDate.textContent = `${d.getMonth() + 1}.${String(d.getDate()).padStart(2, "0")}`;
  el.editionDay.textContent = `${WEEKDAYS[d.getDay()]}曜日`;

  el.dateStrip.replaceChildren();
  let prevMonth = null;
  for (const ds of state.dates) {
    const dd = parseDate(ds);
    const month = dd.getMonth() + 1;
    if (prevMonth !== null && month !== prevMonth) {
      el.dateStrip.append(h("li", { class: "month", "aria-hidden": "true" }, `${month}月`));
    }
    prevMonth = month;
    const current = state.view.mode === "day" && ds === date;
    el.dateStrip.append(
      h(
        "li",
        {},
        h(
          "button",
          {
            "aria-current": current ? "date" : null,
            "aria-label": `${month}月${dd.getDate()}日`,
            onclick: () => go(`#/${ds}`, { replace: true }),
          },
          h("span", { class: "d" }, dd.getDate()),
          h("span", { class: "w" + (dd.getDay() === 0 ? " is-sun" : "") }, WEEKDAYS[dd.getDay()])
        )
      )
    );
  }
  const currentBtn = el.dateStrip.querySelector('[aria-current="date"]');
  if (currentBtn) {
    const strip = el.dateStrip;
    const left = currentBtn.offsetLeft - strip.offsetLeft;
    if (left < strip.scrollLeft || left + currentBtn.offsetWidth > strip.scrollLeft + strip.clientWidth) {
      strip.scrollLeft = left - strip.clientWidth / 2 + currentBtn.offsetWidth / 2;
    }
  }
}

/* ---------- render: filters ---------- */

function renderFilters(digest) {
  const inSaved = state.view.mode === "saved";
  const chips = [
    h(
      "button",
      {
        class: "chip",
        "aria-pressed": String(!inSaved && state.filter === "all"),
        onclick: () => setFilter("all"),
      },
      "すべて"
    ),
  ];
  digest.categories.forEach((cat, i) => {
    chips.push(
      h(
        "button",
        {
          class: "chip",
          "aria-pressed": String(!inSaved && state.filter === i),
          onclick: () => setFilter(i),
        },
        h("span", { class: "block", style: `--c:${catColor(i)}` }),
        cat.name,
        h("span", { class: "count" }, cat.articles.length)
      )
    );
  });
  chips.push(
    h(
      "button",
      {
        class: "chip chip-saved",
        "aria-pressed": String(inSaved),
        "aria-label": "保存した記事",
        onclick: () => go("#/saved", { replace: true }),
      },
      svgStar(inSaved),
      saved.length ? h("span", { class: "count" }, saved.length) : null
    )
  );
  el.filters.replaceChildren(...chips);
}

function setFilter(f) {
  state.filter = f;
  if (state.view.mode === "saved") {
    go(`#/${state.view.date}`, { replace: true });
  } else {
    renderAll();
  }
  window.scrollTo({ top: Math.min(window.scrollY, el.filters.offsetTop) });
}

/* ---------- render: feed ---------- */

function thumb(article, cls) {
  if (!article.image) return null;
  const wrap = h("span", { class: `thumb ${cls || ""}` });
  const img = h("img", {
    src: article.image,
    alt: "",
    loading: "lazy",
    decoding: "async",
    referrerpolicy: "no-referrer",
  });
  img.addEventListener("error", () => {
    const row = wrap.closest(".row");
    wrap.remove();
    if (row) row.style.gridTemplateColumns = "1fr";
  });
  wrap.append(img);
  return wrap;
}

const ACTION_CLASS = { 要対応: "act act-must", 確認推奨: "act act-check", 影響なし: "act act-none" };

function actionChip(article) {
  return article.my_action ? h("span", { class: ACTION_CLASS[article.my_action] || "act" }, article.my_action) : null;
}

function metaLine(article) {
  const bits = [];
  const act = actionChip(article);
  if (act) bits.push(act);
  if (article.severity) bits.push(h("span", { class: "sev" }, article.severity));
  if (isSaved(article.url)) {
    const star = svgStar(true);
    star.setAttribute("width", "14");
    star.setAttribute("height", "14");
    bits.push(h("span", { class: "saved-mark", "aria-label": "保存済み" }, star));
  }
  return bits.length ? h("div", { class: "meta" }, bits) : null;
}

function storyHref(date, article) {
  return `#/${date}/${encodeURIComponent(article.id)}`;
}

function leadStory(date, article) {
  return h(
    "a",
    { class: "story lead", href: storyHref(date, article), onclick: onStoryClick },
    thumb(article),
    h("h3", { class: "title" }, article.title),
    h("p", { class: "summary" }, article.summary),
    metaLine(article)
  );
}

function rowStory(date, article) {
  const t = thumb(article);
  return h(
    "a",
    {
      class: "story row",
      href: storyHref(date, article),
      onclick: onStoryClick,
      style: t ? null : "grid-template-columns:1fr",
    },
    h("div", {}, h("h3", { class: "title" }, article.title), h("p", { class: "summary" }, article.summary), metaLine(article)),
    t
  );
}

function onStoryClick(e) {
  if (e.metaKey || e.ctrlKey || e.shiftKey) return;
  e.preventDefault();
  state.readerPushed = true;
  go(e.currentTarget.getAttribute("href"));
}

function storyList(date, articles) {
  if (!articles.length) return [];
  // 写真のある記事を先頭(大きく)にする
  const leadIndex = Math.max(0, articles.findIndex((a) => a.image));
  const lead = articles[leadIndex];
  const rest = articles.filter((_, i) => i !== leadIndex);
  return [leadStory(date, lead), ...rest.map((a) => rowStory(date, a))];
}

function renderDayFeed(digest) {
  const sections = [];
  digest.categories.forEach((cat, i) => {
    if (state.filter !== "all" && state.filter !== i) return;
    if (!cat.articles.length) return;
    const head =
      state.filter === "all"
        ? h("h2", { class: "section-head" }, h("span", { class: "block", style: `--c:${catColor(i)}` }), cat.name)
        : null;
    sections.push(h("section", { class: "section" }, head, storyList(digest.date, cat.articles)));
  });
  if (!sections.length) sections.push(h("p", { class: "empty" }, "この日の記事はありません"));
  el.feed.replaceChildren(...sections);
}

function renderSavedFeed() {
  if (!saved.length) {
    el.feed.replaceChildren(h("p", { class: "empty" }, "記事を開いて ☆ を押すとここに残ります"));
    return;
  }
  const groups = new Map();
  for (const s of saved) {
    if (!groups.has(s.date)) groups.set(s.date, []);
    groups.get(s.date).push(s);
  }
  const nodes = [];
  for (const date of [...groups.keys()].sort().reverse()) {
    const d = parseDate(date);
    nodes.push(h("h2", { class: "saved-date" }, `${d.getMonth() + 1}.${String(d.getDate()).padStart(2, "0")} ${WEEKDAYS[d.getDay()]}`));
    for (const s of groups.get(date)) nodes.push(rowStory(date, s.article));
  }
  el.feed.replaceChildren(h("section", { class: "section" }, nodes));
}

async function renderAll() {
  const date = state.view.date;
  const digest = await loadDigest(date);
  if (state.filter !== "all" && !digest.categories[state.filter]) state.filter = "all";
  renderMasthead();
  renderFilters(digest);
  if (state.view.mode === "saved") renderSavedFeed();
  else renderDayFeed(digest);
}

/* ---------- reader ---------- */

let readerEntry = null;

function openReader(entry) {
  readerEntry = entry;
  const { article, catIndex, catName } = entry;

  const hero = article.image
    ? h(
        "div",
        { class: "reader-hero" },
        h("img", { src: article.image, alt: "", referrerpolicy: "no-referrer", onerror: (e) => e.target.parentElement.remove() })
      )
    : null;

  const metaBits = [];
  const act = actionChip(article);
  if (act) metaBits.push(act);
  if (article.severity) metaBits.push(h("span", { class: "sev" }, article.severity));
  if (article.affected_versions) metaBits.push(h("span", {}, article.affected_versions));

  const paragraphs = (article.body || article.summary || "")
    .split(/\n+/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => h("p", {}, p));

  el.readerArticle.className = "reader-article" + (hero ? "" : " no-hero");
  el.readerArticle.replaceChildren(
    hero || "",
    h(
      "header",
      { class: "reader-head" },
      h("div", { class: "reader-cat" }, h("span", { class: "block", style: `--c:${catColor(catIndex)}` }), catName, h("span", {}, domainOf(article.url))),
      h("h1", { class: "reader-title" }, article.title),
      metaBits.length ? h("div", { class: "meta" }, metaBits) : null
    ),
    h("div", { class: "reader-body" }, paragraphs)
  );

  el.readerSource.href = article.url;
  updateSaveButton();

  const wasHidden = el.reader.hidden;
  el.reader.hidden = false;
  document.body.classList.add("reading");
  el.readerScroll.scrollTop = 0;
  if (wasHidden) {
    el.reader.classList.remove("is-entering");
    void el.reader.offsetWidth;
    el.reader.classList.add("is-entering");
    el.readerClose.focus({ preventScroll: true });
  }
  document.title = `${article.title} — Myness`;
}

function updateSaveButton() {
  const on = readerEntry && isSaved(readerEntry.article.url);
  el.readerSave.setAttribute("aria-pressed", String(!!on));
  el.readerSave.replaceChildren(svgStar(on));
}

function closeReader() {
  if (el.reader.hidden) return;
  el.reader.hidden = true;
  document.body.classList.remove("reading");
  document.title = "Myness";
  readerEntry = null;
}

el.readerClose.addEventListener("click", () => {
  if (state.readerPushed) {
    state.readerPushed = false;
    history.back();
  } else {
    go(state.view.mode === "saved" ? "#/saved" : `#/${state.view.date}`, { replace: true });
  }
});

el.readerSave.addEventListener("click", () => {
  if (!readerEntry) return;
  toggleSaved({
    date: readerEntry.date,
    catIndex: readerEntry.catIndex,
    catName: readerEntry.catName,
    article: readerEntry.article,
  });
  updateSaveButton();
  renderAll();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !el.reader.hidden) el.readerClose.click();
});

window.addEventListener("popstate", () => {
  state.readerPushed = false;
  route();
});

/* ---------- boot ---------- */

async function boot() {
  try {
    const index = await fetchJson("data/index.json");
    state.dates = index.dates.map((d) => d.date).sort().reverse();
    if (!state.dates.length) throw new Error("no dates");
    await route();
  } catch (err) {
    console.error(err);
    el.feed.replaceChildren(h("p", { class: "empty" }, "ニュースを読み込めませんでした。時間をおいて開き直してください"));
  }
}

boot();
