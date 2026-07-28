const FAVORITES_KEY = "myNewsFavorites";

const state = {
  index: null,
  digest: null,
  activeCategory: "すべて",
  favoritesOnly: false,
};

const el = {
  dateSelect: document.getElementById("date-select"),
  favoritesOnly: document.getElementById("favorites-only"),
  categoryTabs: document.getElementById("category-tabs"),
  categories: document.getElementById("categories"),
  status: document.getElementById("status"),
  generatedAt: document.getElementById("generated-at"),
};

function loadFavorites() {
  try {
    return new Set(JSON.parse(localStorage.getItem(FAVORITES_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function saveFavorites(set) {
  localStorage.setItem(FAVORITES_KEY, JSON.stringify([...set]));
}

let favorites = loadFavorites();

function toggleFavorite(url) {
  if (favorites.has(url)) {
    favorites.delete(url);
  } else {
    favorites.add(url);
  }
  saveFavorites(favorites);
  render();
}

async function fetchJson(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`failed to fetch ${path}: ${res.status}`);
  return res.json();
}

function renderCategoryTabs() {
  const names = ["すべて", ...state.digest.categories.map((c) => c.name)];
  el.categoryTabs.innerHTML = "";
  for (const name of names) {
    const btn = document.createElement("button");
    btn.className = "category-tab" + (name === state.activeCategory ? " active" : "");
    btn.textContent = name;
    btn.addEventListener("click", () => {
      state.activeCategory = name;
      render();
    });
    el.categoryTabs.appendChild(btn);
  }
}

function articleCard(article) {
  const card = document.createElement("div");
  card.className = "article-card";

  const body = document.createElement("div");
  body.className = "article-body";

  const title = document.createElement("p");
  title.className = "article-title";
  const link = document.createElement("a");
  link.href = article.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = article.title;
  title.appendChild(link);

  const summary = document.createElement("p");
  summary.className = "article-summary";
  summary.textContent = article.summary;

  const meta = document.createElement("div");
  meta.className = "article-meta";
  if (article.severity) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = article.severity;
    meta.appendChild(badge);
  }
  if (article.affected_versions) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = article.affected_versions;
    meta.appendChild(badge);
  }

  body.append(title, summary, meta);

  const favBtn = document.createElement("button");
  favBtn.className = "favorite-btn" + (favorites.has(article.url) ? " active" : "");
  favBtn.setAttribute("aria-label", "お気に入り");
  favBtn.textContent = favorites.has(article.url) ? "★" : "☆";
  favBtn.addEventListener("click", () => toggleFavorite(article.url));

  card.append(body, favBtn);
  return card;
}

function render() {
  renderCategoryTabs();
  el.categories.innerHTML = "";

  const categories = state.digest.categories.filter(
    (c) => state.activeCategory === "すべて" || c.name === state.activeCategory
  );

  let totalShown = 0;
  for (const category of categories) {
    let articles = category.articles;
    if (state.favoritesOnly) {
      articles = articles.filter((a) => favorites.has(a.url));
    }
    if (articles.length === 0 && (state.favoritesOnly || state.activeCategory !== "すべて")) {
      if (state.favoritesOnly) continue;
    }

    const block = document.createElement("section");
    block.className = "category-block";
    const heading = document.createElement("h2");
    heading.textContent = category.name;
    block.appendChild(heading);

    if (articles.length === 0) {
      const note = document.createElement("p");
      note.className = "empty-note";
      note.textContent = "該当記事なし";
      block.appendChild(note);
    } else {
      for (const article of articles) {
        block.appendChild(articleCard(article));
        totalShown += 1;
      }
    }
    el.categories.appendChild(block);
  }

  el.status.style.display = totalShown === 0 && state.favoritesOnly ? "block" : "none";
  el.status.textContent = "お気に入り記事はまだありません。";
  el.generatedAt.textContent = state.digest.generated_at
    ? `生成日時: ${new Date(state.digest.generated_at).toLocaleString("ja-JP")}`
    : "";
}

async function loadDigest(date) {
  const entry = state.index.dates.find((d) => d.date === date);
  if (!entry) return;
  state.digest = await fetchJson(`data/${entry.file}`);
  state.activeCategory = "すべて";
  render();
}

async function init() {
  try {
    state.index = await fetchJson("data/index.json");
  } catch (e) {
    el.status.textContent = "データの読み込みに失敗しました。";
    console.error(e);
    return;
  }

  if (!state.index.dates || state.index.dates.length === 0) {
    el.status.textContent = "まだダイジェストがありません。初回配信をお待ちください。";
    return;
  }

  el.status.style.display = "none";

  for (const entry of state.index.dates) {
    const opt = document.createElement("option");
    opt.value = entry.date;
    opt.textContent = entry.date;
    el.dateSelect.appendChild(opt);
  }

  el.dateSelect.addEventListener("change", () => loadDigest(el.dateSelect.value));
  el.favoritesOnly.addEventListener("change", () => {
    state.favoritesOnly = el.favoritesOnly.checked;
    render();
  });

  await loadDigest(state.index.dates[0].date);
}

init();
