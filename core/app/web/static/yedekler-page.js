(function () {
  "use strict";

  var selectAll = document.getElementById("backup-select-all");
  var boxes = document.querySelectorAll(".backup-select");
  var countEl = document.getElementById("backup-selected-count");

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

  window.confirmFtpResend = function () {
    var n = document.querySelectorAll(".backup-select:checked").length;
    if (!n) {
      alert("FTP icin en az bir yedek secin.");
      return false;
    }
    return confirm(n + " yedek FTP sunucusuna tekrar gonderilsin mi?");
  };

  updateCount();
})();
