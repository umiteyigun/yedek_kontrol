(function () {
  "use strict";

  var selectAll = document.getElementById("backup-select-all");
  var boxes = document.querySelectorAll(".backup-select");
  var countEl = document.getElementById("backup-selected-count");
  var liveBanner = document.getElementById("backup-live-banner");
  var pollTimer = null;

  function updateCount() {
    if (!countEl) return;
    var n = document.querySelectorAll(".backup-select:checked").length;
    countEl.textContent = n + " secili";
    if (selectAll) {
      selectAll.indeterminate = n > 0 && n < boxes.length;
      selectAll.checked = boxes.length > 0 && n === boxes.length;
    }
  }

  if (selectAll) {
    selectAll.addEventListener("change", function () {
      boxes.forEach(function (box) {
        box.checked = selectAll.checked;
      });
      updateCount();
    });
  }

  boxes.forEach(function (box) {
    box.addEventListener("change", updateCount);
  });

  var ftpBtn = document.getElementById("ftp-resend-btn");
  if (ftpBtn) {
    ftpBtn.addEventListener("click", function (event) {
      var n = document.querySelectorAll(".backup-select:checked").length;
      if (!n) {
        event.preventDefault();
        alert("FTP icin en az bir yedek secin.");
        return;
      }
      if (!confirm(n + " yedek FTP sunucusuna tekrar gonderilsin mi?")) {
        event.preventDefault();
      }
    });
  }

  document.querySelectorAll(".backup-delete-btn").forEach(function (btn) {
    btn.addEventListener("click", function (event) {
      if (!confirm("Bu yedegi silmek istediginize emin misiniz?")) {
        event.preventDefault();
      }
    });
  });

  window.confirmFtpResend = function () {
    var n = document.querySelectorAll(".backup-select:checked").length;
    if (!n) {
      alert("FTP icin en az bir yedek secin.");
      return false;
    }
    return confirm(n + " yedek FTP sunucusuna tekrar gonderilsin mi?");
  };

  function renderStages(status) {
    var track = document.getElementById("backup-stage-track");
    if (!track || !status.stages_list) return;

    status.stages_list.forEach(function (st) {
      var item = track.querySelector('[data-stage="' + st.key + '"]');
      if (!item) return;
      item.classList.remove("active", "done", "pending");
      if (st.active) item.classList.add("active");
      else if (st.done) item.classList.add("done");
      else item.classList.add("pending");
      var dur = item.querySelector("[data-stage-duration]");
      if (dur && st.duration_label) dur.textContent = st.duration_label;
    });

    var stageEl = document.getElementById("backup-live-stage");
    if (stageEl && status.stage_label) stageEl.textContent = status.stage_label;

    var timerEl = document.getElementById("backup-live-timer");
    if (timerEl) {
      if (status.stage_live_duration_label) {
        timerEl.textContent = status.stage_live_duration_label;
      } else if (status.total_duration_label) {
        timerEl.textContent = "Toplam " + status.total_duration_label;
      } else {
        timerEl.textContent = "";
      }
    }
  }

  function pollBackupStatus() {
    var prefix = liveBanner.getAttribute("data-api-prefix") || "";
    fetch(prefix + "/api/v1/backup/status", { credentials: "same-origin" })
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (!data.ok || !data.status) return;
        var status = data.status;
        renderStages(status);
        if (status.state !== "running") {
          if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
          }
          window.location.reload();
        }
      })
      .catch(function () {});
  }

  if (liveBanner && liveBanner.getAttribute("data-poll") === "1") {
    pollTimer = setInterval(pollBackupStatus, 5000);
    pollBackupStatus();
  }

  updateCount();
})();
