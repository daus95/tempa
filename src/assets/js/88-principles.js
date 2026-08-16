// ---------------------------------------------------------------------------
// Architecture Principles
// ---------------------------------------------------------------------------
async function renderPrinciples() {
  principlesSaveStatus.textContent = "";
  principlesSaveStatus.classList.remove("err");
  try {
    const res = await fetch("/api/principles");
    const data = await res.json();
    if (!data.ok) { toast(data.error || "Could not load the principles.", true); return; }
    principlesEditor.value = data.content;
    state.principlesSet = !!data.content;
  } catch (e) {
    toast("Network error loading the principles.", true);
  }
}

principlesSaveBtn.addEventListener("click", async () => {
  principlesSaveBtn.disabled = true;
  principlesSaveStatus.textContent = "";
  principlesSaveStatus.classList.remove("err");
  try {
    const res = await fetch("/api/principles/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: principlesEditor.value }),
    });
    const data = await res.json();
    if (!data.ok) {
      principlesSaveStatus.textContent = data.error || "Could not save the principles.";
      principlesSaveStatus.classList.add("err");
      return;
    }
    principlesEditor.value = data.content;
    state.principlesSet = !!data.content;
    toast(data.content ? "Architecture principles saved." : "Architecture principles cleared.");
    selectTop("home");
  } catch (e) {
    principlesSaveStatus.textContent = "Network error while saving.";
    principlesSaveStatus.classList.add("err");
  } finally {
    principlesSaveBtn.disabled = false;
  }
});

