const ADDON_DETAILS = document.getElementById("addonDetails");
const ADDON_LIST = document.getElementById("addonList");
const STATUS_CONTAINER = document.getElementById("statusContainer");

function announceStatus(text) {
  // Consecutive status updates with the same content won't be read.
  // Append a non-breaking space to force announcements to be treated as different, without altering their visible/spoken content.
  if (STATUS_CONTAINER.textContent == text) {
    text = text + "\x00A0";
  }
  STATUS_CONTAINER.textContent = text;
}

ADDON_DETAILS.addEventListener("turbo:frame-load", e => {
  announceStatus("Add-on details loaded");
});

ADDON_LIST.addEventListener("turbo:frame-load", e => {
  announceStatus("Results loaded");
});
