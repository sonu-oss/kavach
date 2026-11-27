const toggle = document.getElementById("toggle-shield");
const liveDot = document.getElementById("live-dot");
const liveText = document.getElementById("live-text");
const apiStatus = document.getElementById("api-status");
const userStatus = document.getElementById("user-status");
const waUserStatus = document.getElementById("wa-user-status");
const guardiansStatus = document.getElementById("guardians-status");
const threatCount = document.getElementById("threat-count");
const testBtn = document.getElementById("test-banner-btn");
const openKavachBtn = document.getElementById("open-kavach-btn");

chrome.storage.sync.get(["kavachEnabled", "threatCount"], (data) => {
  const enabled = data.kavachEnabled !== false;
  toggle.checked = enabled;
  updateUI(enabled);
  threatCount.textContent = data.threatCount || 0;
  chrome.action.setBadgeText({ text: "" });
});

// Check user session & backend status from background worker
chrome.runtime.sendMessage({ type: "GET_KAVACH_SESSION" }, (resp) => {
  if (resp && resp.ok) {
    apiStatus.textContent = "Online (Local API)";
    apiStatus.style.color = "#10b981";
    if (resp.apiUrl && openKavachBtn) {
      openKavachBtn.href = resp.apiUrl;
    }

    const u = resp.user;
    if (u && (u.full_name || u.username || u.email)) {
      const name = u.full_name || u.username;
      const email = u.email ? ` (${u.email})` : "";
      userStatus.textContent = `${name}${email}`;
      userStatus.style.color = "#a78bfa";
    } else {
      userStatus.textContent = "Adarsh (adarshavg26@gmail.com)";
      userStatus.style.color = "#a78bfa";
    }

    if (resp.guardians_names && resp.guardians_names.length) {
      guardiansStatus.textContent = `${resp.guardians_names.join(", ")} (${resp.guardians_count} active)`;
      guardiansStatus.style.color = "#34d399";
    } else if (resp.guardians_count) {
      guardiansStatus.textContent = `${resp.guardians_count} active guardian(s)`;
      guardiansStatus.style.color = "#34d399";
    } else {
      guardiansStatus.textContent = "Gangareddy (Linked)";
      guardiansStatus.style.color = "#34d399";
    }
  } else {
    apiStatus.textContent = "Offline (Backend down)";
    apiStatus.style.color = "#ef4444";
    userStatus.textContent = "Adarsh (Local Session)";
    userStatus.style.color = "#9ca3af";
    guardiansStatus.textContent = "Guardians Ready";
    guardiansStatus.style.color = "#9ca3af";
  }
});

// Retrieve WhatsApp paired user from local storage
chrome.storage.local.get(["whatsappUser"], (st) => {
  if (st && st.whatsappUser) {
    waUserStatus.textContent = st.whatsappUser;
    waUserStatus.style.color = "#38bdf8";
  } else {
    waUserStatus.textContent = "Adarsh Gangareddy (You)";
    waUserStatus.style.color = "#38bdf8";
  }
});

toggle.addEventListener("change", (e) => {
  const isEnabled = e.target.checked;
  chrome.storage.sync.set({ kavachEnabled: isEnabled });
  updateUI(isEnabled);
});

function updateUI(enabled) {
  if (enabled) {
    liveDot.className = "dot";
    liveText.textContent = "Shield Active";
  } else {
    liveDot.className = "dot off";
    liveText.textContent = "Shield Paused";
  }
}

// Send scan trigger directly to active tab
testBtn?.addEventListener("click", () => {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs || !tabs[0] || !tabs[0].id) return;
    chrome.tabs.sendMessage(tabs[0].id, { action: "TRIGGER_TEST_BANNER" }, (response) => {
      if (chrome.runtime.lastError) {
        chrome.scripting?.executeScript({
          target: { tabId: tabs[0].id },
          func: () => {
            alert("Please refresh this WhatsApp Web tab so Kavach can connect to it!");
          },
        });
      } else {
        window.close(); // Close popup so user sees the result immediately
      }
    });
  });
});
