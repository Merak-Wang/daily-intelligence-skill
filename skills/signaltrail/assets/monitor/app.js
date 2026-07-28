"use strict";

const state = {
  snapshot: null,
  view: "stream",
  query: "",
  category: "",
  source: "",
  imageOnly: false,
};

const labels = {
  "information.international": "国际",
  "information.domestic": "国内",
  "information.military": "军事",
  "information.market": "市场",
  "technology.news": "技术",
  "technology.papers": "论文",
  "technology.open_source": "开源",
  new: "新出现",
  developing: "发展中",
  confirmed: "多源确认",
  updated: "有更新",
  success: "正常",
  partial: "部分可用",
  no_items: "无条目",
  failed: "失败",
  rate_limited: "限流",
  verification_required: "待验证",
  unsupported: "未接入",
};

const $ = (selector) => document.querySelector(selector);

function textElement(tag, value, className = "") {
  const element = document.createElement(tag);
  element.textContent = value ?? "";
  if (className) element.className = className;
  return element;
}

function formatTime(value) {
  if (!value) return "时间未知";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function categoryKey(item) {
  return `${item.module || "information"}.${item.category || "international"}`;
}

function sourceMap() {
  return new Map((state.snapshot?.sources || []).map((row) => [row.source_id, row]));
}

function setMetrics(snapshot) {
  const summary = snapshot.summary || {};
  $("#metric-sources").textContent = summary.source_count ?? 0;
  $("#metric-items").textContent = summary.item_count ?? 0;
  $("#metric-clusters").textContent = summary.cluster_count ?? 0;
  $("#metric-corroborated").textContent = summary.multi_source_clusters ?? 0;
  $("#metric-pending").textContent = summary.pending_source_count ?? 0;
  $("#generated-time").textContent = formatTime(snapshot.generated_at);
  $("#token-note").textContent = `模型 token：${snapshot.token_usage ?? 0}`;
}

function populateFilters(snapshot) {
  const categorySelect = $("#category-filter");
  const sourceSelect = $("#source-filter");
  while (categorySelect.options.length > 1) categorySelect.remove(1);
  while (sourceSelect.options.length > 1) sourceSelect.remove(1);
  const categories = [...new Set(snapshot.items.map(categoryKey))].sort();
  const sources = [...snapshot.sources].sort((a, b) =>
    String(a.source_name).localeCompare(String(b.source_name), "zh-CN")
  );
  categories.forEach((category) => {
    const option = document.createElement("option");
    option.value = category;
    option.textContent = labels[category] || category;
    categorySelect.append(option);
  });
  sources.forEach((source) => {
    const option = document.createElement("option");
    option.value = source.source_id;
    option.textContent = source.source_name;
    sourceSelect.append(option);
  });
  categorySelect.value = state.category;
  sourceSelect.value = state.source;
}

function matchesItem(item) {
  if (state.category && categoryKey(item) !== state.category) return false;
  if (state.source && item.source_id !== state.source) return false;
  if (state.imageOnly && !item.image_url) return false;
  if (!state.query) return true;
  const haystack = [
    item.title,
    item.description,
    item.source_name,
    item.original_provider,
  ].join(" ").toLocaleLowerCase();
  return haystack.includes(state.query);
}

function storyCard(item) {
  const article = document.createElement("article");
  article.className = "story-card";
  if (item.image_url) {
    const media = document.createElement("div");
    media.className = "story-media";
    const image = document.createElement("img");
    image.src = item.image_url;
    image.alt = "";
    image.loading = "lazy";
    image.referrerPolicy = "no-referrer";
    image.addEventListener("error", () => media.remove(), { once: true });
    media.append(image);
    article.append(media);
  }

  const body = document.createElement("div");
  body.className = "story-body";
  const meta = document.createElement("div");
  meta.className = "story-meta";
  meta.append(textElement("span", item.source_name, "source-name"));
  if (item.original_provider && item.original_provider !== item.source_name) {
    meta.append(textElement("span", `经 ${item.original_provider}`));
  }
  meta.append(textElement("span", labels[categoryKey(item)] || categoryKey(item), "tag"));
  body.append(meta);

  const title = document.createElement("h3");
  const link = document.createElement("a");
  link.href = item.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = item.title;
  title.append(link);
  body.append(title);
  if (item.description) body.append(textElement("p", item.description, "story-description"));

  const footer = document.createElement("div");
  footer.className = "story-footer";
  const hasPublication = Boolean(item.published_at);
  footer.append(textElement(
    "span",
    `${hasPublication ? "发布" : "采集"} ${formatTime(item.published_at || item.discovered_at)}`,
    hasPublication ? "" : "time-fallback"
  ));
  const method = item.metadata?.acquisition_method || "index";
  footer.append(textElement("span", method.toUpperCase()));
  body.append(footer);
  article.append(body);
  return article;
}

function renderStream() {
  const list = $("#stream-list");
  list.replaceChildren();
  const items = (state.snapshot?.items || []).filter(matchesItem);
  $("#stream-count").textContent = `显示 ${items.length} 条`;
  if (!items.length) {
    list.append(textElement("p", "当前筛选条件下没有新闻。", "empty-state"));
    return;
  }
  const fragment = document.createDocumentFragment();
  items.forEach((item) => fragment.append(storyCard(item)));
  list.append(fragment);
}

function clusterMatches(cluster, itemById) {
  const items = cluster.item_ids.map((id) => itemById.get(id)).filter(Boolean);
  return items.some(matchesItem);
}

function renderClusters() {
  const list = $("#cluster-list");
  list.replaceChildren();
  const itemById = new Map((state.snapshot?.items || []).map((item) => [item.item_id, item]));
  const sources = sourceMap();
  const clusters = (state.snapshot?.clusters || []).filter((cluster) =>
    clusterMatches(cluster, itemById)
  );
  if (!clusters.length) {
    list.append(textElement("p", "当前筛选条件下没有事件簇。", "empty-state"));
    return;
  }
  clusters.forEach((cluster) => {
    const card = document.createElement("article");
    card.className = `cluster-card ${cluster.phase || "new"}`;
    const main = document.createElement("div");
    const meta = document.createElement("div");
    meta.className = "cluster-meta";
    meta.append(textElement("span", labels[cluster.phase] || cluster.phase, "phase"));
    meta.append(textElement("span", `${cluster.source_count} 个来源`));
    meta.append(textElement("span", labels[`${cluster.module}.${cluster.category}`] || cluster.category));
    main.append(meta);
    main.append(textElement("h3", cluster.title));
    const sourceNames = cluster.source_ids
      .map((id) => sources.get(id)?.source_name || id)
      .join(" · ");
    main.append(textElement("p", sourceNames, "cluster-sources"));
    const side = document.createElement("div");
    side.className = "cluster-side";
    side.append(textElement("strong", String(cluster.importance), "cluster-score"));
    side.append(textElement("small", "确定性关注度 / 100"));
    side.append(textElement("small", `最新：${formatTime(cluster.published_at || cluster.last_seen_at)}`));
    card.append(main, side);
    list.append(card);
  });
}

function renderSources() {
  const tbody = $("#source-table");
  tbody.replaceChildren();
  const health = new Map((state.snapshot?.health || []).map((row) => [row.source_id, row]));
  [...(state.snapshot?.sources || [])]
    .sort((a, b) => {
      const statusDelta = String(a.status).localeCompare(String(b.status));
      return statusDelta || Number(a.tier || 3) - Number(b.tier || 3);
    })
    .forEach((source) => {
      const row = document.createElement("tr");
      const sourceCell = document.createElement("td");
      const link = document.createElement("a");
      link.href = source.source_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = source.source_name;
      sourceCell.append(link);
      const sourceHealth = health.get(source.source_id) || {};
      const statusCell = document.createElement("td");
      statusCell.append(textElement(
        "span",
        labels[source.status] || source.status,
        `status-pill status-${source.status}`
      ));
      [
        sourceCell,
        statusCell,
        textElement("td", `${Math.round((sourceHealth.success_rate || 0) * 100)}%`),
        textElement("td", String(sourceHealth.consecutive_failures || 0)),
        textElement("td", String(source.items_count || 0)),
        textElement("td", (source.methods || []).join(" + ") || "—"),
        textElement("td", formatTime(sourceHealth.last_success_at)),
      ].forEach((cell) => row.append(cell));
      tbody.append(row);
    });
}

function renderPending() {
  const list = $("#pending-list");
  list.replaceChildren();
  const pending = state.snapshot?.pending_verifications || [];
  if (!pending.length) {
    list.append(textElement("p", "当前没有需要人工处理的来源。", "empty-state"));
    return;
  }
  pending.forEach((item) => {
    const card = document.createElement("article");
    card.className = "pending-card";
    const content = document.createElement("div");
    content.append(textElement("h3", `${item.source_name} · ${labels[item.status] || item.status}`));
    content.append(textElement("p", item.error || "该来源需要在显式验证流程中处理。"));
    const link = document.createElement("a");
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "查看来源";
    card.append(content, link);
    list.append(card);
  });
}

function renderAll() {
  renderStream();
  renderClusters();
  renderSources();
  renderPending();
}

function switchView(view) {
  state.view = view;
  document.querySelectorAll(".tab").forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".view-panel").forEach((panel) => {
    const active = panel.id === `view-${view}`;
    panel.classList.toggle("active", active);
    panel.hidden = !active;
  });
  localStorage.setItem("daily-intel-monitor-view", view);
}

function bindControls() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  });
  $("#search").addEventListener("input", (event) => {
    state.query = event.target.value.trim().toLocaleLowerCase();
    renderStream();
    renderClusters();
  });
  $("#category-filter").addEventListener("change", (event) => {
    state.category = event.target.value;
    renderStream();
    renderClusters();
  });
  $("#source-filter").addEventListener("change", (event) => {
    state.source = event.target.value;
    renderStream();
    renderClusters();
  });
  $("#image-only").addEventListener("change", (event) => {
    state.imageOnly = event.target.checked;
    renderStream();
    renderClusters();
  });
}

async function loadSnapshot() {
  const status = $("#status-message");
  try {
    const response = await fetch("/api/snapshot", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || payload.error || "快照读取失败");
    state.snapshot = payload;
    setMetrics(payload);
    populateFilters(payload);
    renderAll();
    status.className = "status-message ready";
    status.textContent = "";
  } catch (error) {
    status.className = "status-message error";
    status.textContent = `无法读取监控快照：${error.message}`;
  }
}

bindControls();
switchView(localStorage.getItem("daily-intel-monitor-view") || "stream");
loadSnapshot();
setInterval(loadSnapshot, 60_000);
