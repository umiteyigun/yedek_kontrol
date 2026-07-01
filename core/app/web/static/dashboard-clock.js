(function () {
  "use strict";

  const boot = document.getElementById("server-clock-boot");
  const clockEl = document.getElementById("server-live-clock");
  if (!clockEl || !boot) return;

  const dateEl = document.getElementById("server-live-date");
  const weekdayEl = document.getElementById("server-live-weekday");
  const tzLabelEl = document.getElementById("server-timezone-label");
  const tzIanaEl = document.getElementById("server-timezone-iana");
  const tzOffsetEl = document.getElementById("server-timezone-offset");
  const ntpEl = document.getElementById("server-ntp-status");
  const editModal = document.getElementById("clock-edit-modal");
  const editToggle = document.getElementById("clock-edit-toggle");
  const editCancel = document.getElementById("clock-edit-cancel");
  const editCancel2 = document.getElementById("clock-edit-cancel-2");
  const dateInput = document.getElementById("clock-input-date");
  const timeInput = document.getElementById("clock-input-time");

  let epoch = Number(boot.dataset.epoch || 0);
  let tz = boot.dataset.tz || "UTC";
  const assetBase = window.yedekAssetBase ? window.yedekAssetBase() : "";

  function applyClockMeta(data) {
    if (data.timezone) {
      tz = data.timezone;
      if (tzIanaEl) tzIanaEl.textContent = data.timezone;
      const tzSelect = document.getElementById("clock-input-tz");
      if (tzSelect) tzSelect.value = data.timezone;
    }
    if (tzLabelEl) {
      tzLabelEl.textContent = data.timezone_label || data.timezone || "—";
      if (data.timezone) tzLabelEl.title = data.timezone;
    }
    if (tzOffsetEl && data.utc_offset) {
      tzOffsetEl.textContent = "UTC" + data.utc_offset;
      tzOffsetEl.hidden = false;
    }
    if (ntpEl && data.ntp_synchronized !== undefined && data.ntp_synchronized !== "") {
      const on = ["yes", "1", "true"].includes(String(data.ntp_synchronized).toLowerCase());
      ntpEl.textContent = "NTP: " + (on ? "aktif" : "kapali");
      ntpEl.hidden = false;
    }
  }

  applyClockMeta({
    timezone: tz,
    timezone_label: boot.dataset.tzLabel || "",
    utc_offset: boot.dataset.utcOffset || "",
    ntp_synchronized: boot.dataset.ntp || "",
  });

  function formatParts(unixSec) {
    const parts = new Intl.DateTimeFormat("tr-TR", {
      timeZone: tz,
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).formatToParts(new Date(unixSec * 1000));
    const get = (type) => (parts.find((p) => p.type === type) || {}).value || "";
    return {
      isoDate: `${get("year")}-${get("month")}-${get("day")}`,
      date: `${get("day")}.${get("month")}.${get("year")}`,
      time: `${get("hour")}:${get("minute")}:${get("second")}`,
      weekday: new Intl.DateTimeFormat("tr-TR", { timeZone: tz, weekday: "long" }).format(
        new Date(unixSec * 1000)
      ),
    };
  }

  function render() {
    if (!epoch) return;
    const p = formatParts(epoch);
    clockEl.textContent = p.time;
    if (dateEl) dateEl.textContent = p.date;
    if (weekdayEl) weekdayEl.textContent = p.weekday;
    if (dateInput && (!editModal || !editModal.open)) {
      dateInput.value = p.isoDate;
      if (timeInput) timeInput.value = p.time;
    }
  }

  function openClockModal() {
    if (!editModal) return;
    render();
    if (typeof editModal.showModal === "function") editModal.showModal();
  }

  function closeClockModal() {
    if (editModal && typeof editModal.close === "function") editModal.close();
  }

  if (editToggle) editToggle.addEventListener("click", openClockModal);
  if (editCancel) editCancel.addEventListener("click", closeClockModal);
  if (editCancel2) editCancel2.addEventListener("click", closeClockModal);

  setInterval(function () {
    if (epoch && (!editModal || !editModal.open)) {
      epoch += 1;
      render();
    }
  }, 1000);

  setInterval(async function () {
    try {
      const res = await fetch(assetBase + "/api/server/clock");
      const data = await res.json();
      if (data.ok && data.clock_epoch) {
        epoch = data.clock_epoch;
        applyClockMeta(data);
        render();
      }
    } catch (err) {
      /* sessiz */
    }
  }, 60000);

  render();
})();
