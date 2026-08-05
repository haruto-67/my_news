function qs(name) {
  return new URLSearchParams(location.search).get(name);
}

async function fetchJson(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`failed to fetch ${path}: ${res.status}`);
  return res.json();
}

function findArticle(digest, id) {
  for (const category of digest.categories) {
    for (const article of category.articles) {
      if (article.id === id) return { article, categoryName: category.name };
    }
  }
  return null;
}

function renderArticle(article, categoryName) {
  const root = document.getElementById("article");
  root.innerHTML = "";

  const categoryTag = document.createElement("p");
  categoryTag.className = "article-detail-category";
  categoryTag.textContent = categoryName;
  root.appendChild(categoryTag);

  const title = document.createElement("h1");
  title.className = "article-detail-title";
  title.textContent = article.title;
  root.appendChild(title);

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
  if (meta.childNodes.length > 0) root.appendChild(meta);

  const body = document.createElement("div");
  body.className = "article-detail-body";
  const paragraphs = (article.body || article.summary || "").split(/\n{2,}/);
  for (const para of paragraphs) {
    if (!para.trim()) continue;
    const p = document.createElement("p");
    p.textContent = para.trim();
    body.appendChild(p);
  }
  root.appendChild(body);

  const sourceLink = document.createElement("a");
  sourceLink.className = "article-source-link";
  sourceLink.href = article.url;
  sourceLink.target = "_blank";
  sourceLink.rel = "noopener noreferrer";
  sourceLink.textContent = "元記事を読む →";
  root.appendChild(sourceLink);

  root.hidden = false;
}

async function init() {
  const status = document.getElementById("status");
  const date = qs("date");
  const id = qs("id");

  if (!date || !id) {
    status.textContent = "記事が指定されていません。";
    return;
  }

  let digest;
  try {
    digest = await fetchJson(`data/${date}.json`);
  } catch (e) {
    status.textContent = "記事の読み込みに失敗しました。";
    console.error(e);
    return;
  }

  const found = findArticle(digest, id);
  if (!found) {
    status.textContent = "指定された記事が見つかりませんでした。";
    return;
  }

  status.style.display = "none";
  renderArticle(found.article, found.categoryName);
}

init();
