(function () {
  "use strict";

  function confirmRmanStart(form) {
    const tip = form.dataset.tip || "";
    const cold = form.dataset.cold === "1";
    if (tip === "RMAN_INCR" && cold) {
      alert("Gunluk Fark yedegi ARCHIVELOG modu gerektirir. Once veritabaninda ArchiveLog acin.");
      return false;
    }
    if (cold && (tip === "RMAN_FULL_MANUAL" || tip === "RMAN_FULL")) {
      return confirm(
        "DIKKAT — NOARCHIVELOG full yedek\n\n" +
          "• Veritabani otomatik SHUTDOWN + MOUNT yapilacak\n" +
          "• HBYS bu surede kullanilamaz\n" +
          "• Yedek bitince DB otomatik OPEN edilir\n" +
          "• Hata olursa sistem yine de DB acmaya calisir\n\n" +
          "Devam edilsin mi?"
      );
    }
    return true;
  }

  document.querySelectorAll(".rman-start-form").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!confirmRmanStart(form)) {
        event.preventDefault();
      }
    });
  });

  function toggleRmanWeeklyDay() {
    const select = document.getElementById("rman-schedule-type");
    const wrap = document.getElementById("rman-day-wrap");
    const daySelect = document.getElementById("rman-day-select");
    const hint = document.getElementById("rman-schedule-hint");
    if (!select || !wrap) return;
    const isIncr = select.value === "RMAN_INCR";
    wrap.classList.toggle("rman-day-hidden", isIncr);
    wrap.hidden = isIncr;
    if (daySelect) {
      daySelect.disabled = isIncr;
      daySelect.required = !isIncr;
    }
    if (hint) {
      hint.innerHTML = isIncr
        ? "<strong>Gunluk Fark:</strong> her gun belirtilen saatte calisir — haftanin gunu secilmez."
        : "<strong>Haftalik Full:</strong> yalnizca secilen gun + saatte level-0 yedek alinir.";
    }
  }

  document.addEventListener("DOMContentLoaded", toggleRmanWeeklyDay);
  toggleRmanWeeklyDay();
})();
