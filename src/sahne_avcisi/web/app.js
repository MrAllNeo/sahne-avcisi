const state = {
  file: null,
  encodedImage: null,
  category: "all",
  adultConfirmed: false,
};

const elements = {
  input: document.querySelector("#imageInput"),
  dropzone: document.querySelector("#dropzone"),
  dropTitle: document.querySelector("#dropTitle"),
  previewRow: document.querySelector("#previewRow"),
  preview: document.querySelector("#imagePreview"),
  fileName: document.querySelector("#fileName"),
  fileMeta: document.querySelector("#fileMeta"),
  remove: document.querySelector("#removeImage"),
  adultToggle: document.querySelector("#adultToggle"),
  ageDialog: document.querySelector("#ageDialog"),
  searchButton: document.querySelector("#searchButton"),
  resultsSection: document.querySelector("#resultsSection"),
  results: document.querySelector("#results"),
  resultTitle: document.querySelector("#resultTitle"),
  resultMeta: document.querySelector("#resultMeta"),
  systemState: document.querySelector("#systemState"),
};

document.querySelectorAll(".type-button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".type-button").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    state.category = button.dataset.category;
  });
});

elements.input.addEventListener("change", () => selectFile(elements.input.files[0]));

["dragenter", "dragover"].forEach((eventName) => {
  elements.dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.dropzone.classList.add("dragging");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  elements.dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.dropzone.classList.remove("dragging");
  });
});

elements.dropzone.addEventListener("drop", (event) => selectFile(event.dataTransfer.files[0]));
elements.remove.addEventListener("click", (event) => {
  event.preventDefault();
  clearFile();
});

elements.adultToggle.addEventListener("change", () => {
  if (elements.adultToggle.checked && !state.adultConfirmed) {
    elements.ageDialog.showModal();
  }
});

elements.ageDialog.addEventListener("close", () => {
  if (elements.ageDialog.returnValue === "confirm") {
    state.adultConfirmed = true;
    elements.adultToggle.checked = true;
    refreshStats(true);
  } else {
    elements.adultToggle.checked = false;
  }
});

elements.searchButton.addEventListener("click", searchScene);

async function selectFile(file) {
  if (!file) return;
  const allowed = ["image/jpeg", "image/png", "image/webp"];
  if (!allowed.includes(file.type)) {
    showInlineError("JPG, PNG veya WebP formatında bir görsel seç.");
    return;
  }
  if (file.size > 12 * 1024 * 1024) {
    showInlineError("Görsel 12 MB sınırını aşıyor.");
    return;
  }
  state.file = file;
  state.encodedImage = await readAsDataUrl(file);
  elements.preview.src = state.encodedImage;
  elements.fileName.textContent = file.name;
  elements.fileMeta.textContent = formatBytes(file.size);
  elements.dropzone.classList.add("hidden");
  elements.previewRow.classList.remove("hidden");
  elements.searchButton.disabled = false;
}

function clearFile() {
  state.file = null;
  state.encodedImage = null;
  elements.input.value = "";
  elements.preview.removeAttribute("src");
  elements.dropzone.classList.remove("hidden");
  elements.previewRow.classList.add("hidden");
  elements.searchButton.disabled = true;
}

async function searchScene() {
  if (!state.encodedImage) return;
  const originalText = elements.searchButton.querySelector("span").textContent;
  elements.searchButton.disabled = true;
  elements.searchButton.querySelector("span").textContent = "Kare eşleştiriliyor…";

  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_base64: state.encodedImage,
        allow_adult: elements.adultToggle.checked && state.adultConfirmed,
        category: state.category,
        limit: 8,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Arama tamamlanamadı.");
    renderResults(payload);
  } catch (error) {
    renderError(error.message);
  } finally {
    elements.searchButton.disabled = false;
    elements.searchButton.querySelector("span").textContent = originalText;
  }
}

function renderResults(payload) {
  elements.resultsSection.classList.remove("hidden");
  elements.resultMeta.textContent = `${payload.indexed_frames.toLocaleString("tr-TR")} kare içinde arandı`;
  elements.results.replaceChildren();

  if (!payload.results.length) {
    elements.resultTitle.textContent = "Henüz eşleşme yok";
    elements.results.innerHTML = `
      <div class="empty-result">
        İndeks boş veya bu kare mevcut kaynaklarda bulunamadı. Önce izinli videoları indeksleyiciyle ekleyebilirsin.
      </div>`;
  } else {
    elements.resultTitle.textContent = `${payload.results.length} olası eşleşme`;
    payload.results.forEach((item) => {
      const card = document.createElement("article");
      card.className = "result-card";
      const episode = item.episode ? ` · Bölüm ${escapeHtml(item.episode)}` : "";
      card.innerHTML = `
        <div class="result-score">%${Math.round(item.similarity)}</div>
        <div>
          <h3>${escapeHtml(item.title)}</h3>
          <p>${escapeHtml(item.source_name)}${episode} · ${escapeHtml(item.timestamp)}</p>
        </div>
        <a href="${escapeAttribute(item.source_url)}" target="_blank" rel="noreferrer">Kaynağı aç ↗</a>`;
      elements.results.append(card);
    });
  }
  elements.resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderError(message) {
  elements.resultsSection.classList.remove("hidden");
  elements.resultTitle.textContent = "Arama yapılamadı";
  elements.resultMeta.textContent = "";
  elements.results.innerHTML = `<div class="empty-result">${escapeHtml(message)}</div>`;
}

function showInlineError(message) {
  elements.dropTitle.textContent = message;
  window.setTimeout(() => {
    elements.dropTitle.textContent = "Ekran görüntüsünü seç veya buraya bırak";
  }, 3500);
}

async function refreshStats(includeAdult = false) {
  try {
    const [healthResponse, statsResponse, sourcesResponse] = await Promise.all([
      fetch("/api/health"),
      fetch("/api/stats"),
      fetch(`/api/sources?adult=${includeAdult}`),
    ]);
    if (!healthResponse.ok || !statsResponse.ok || !sourcesResponse.ok) throw new Error("API yanıt vermedi");
    const stats = await statsResponse.json();
    const sources = await sourcesResponse.json();
    document.querySelector("#sourceCount").textContent = sources.items.length.toLocaleString("tr-TR");
    document.querySelector("#activeCount").textContent = stats.active_sources.toLocaleString("tr-TR");
    document.querySelector("#mediaCount").textContent = stats.media.toLocaleString("tr-TR");
    document.querySelector("#frameCount").textContent = stats.frames.toLocaleString("tr-TR");
    renderSyncStats(stats.latest_sync);
    elements.systemState.textContent = "Arama motoru hazır";
    elements.systemState.parentElement.classList.add("ready");
  } catch {
    elements.systemState.textContent = "Motor çevrimdışı";
  }
}

function renderSyncStats(sync) {
  const container = document.querySelector(".catalog-status");
  container.classList.remove("synced", "failed");
  if (!sync) return;

  container.classList.add(sync.status === "completed" ? "synced" : "failed");
  document.querySelector("#lastSync").textContent = sync.completed_at
    ? `${formatDate(sync.completed_at)} · ${sync.status === "completed" ? "Başarılı" : "Hatalı"}`
    : "Çalışıyor";
  document.querySelector("#createdSourceCount").textContent = Number(sync.created_count).toLocaleString("tr-TR");
  document.querySelector("#updatedSourceCount").textContent = Number(sync.updated_count).toLocaleString("tr-TR");
  document.querySelector("#missingSourceCount").textContent = Number(sync.missing_count).toLocaleString("tr-TR");
}

function formatDate(sqliteDate) {
  const parsed = new Date(`${sqliteDate.replace(" ", "T")}Z`);
  if (Number.isNaN(parsed.getTime())) return sqliteDate;
  return new Intl.DateTimeFormat("tr-TR", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

function escapeAttribute(value) {
  const url = String(value ?? "");
  return /^https?:\/\//i.test(url) ? url.replaceAll('"', "&quot;") : "#";
}

refreshStats();
