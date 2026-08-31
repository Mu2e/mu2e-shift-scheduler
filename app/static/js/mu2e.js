/*
 * Shared client-side helpers for the Mu2e Shift Scheduler.
 * Vanilla JS + Bootstrap 5 (loaded before this file). Every feature here is a
 * progressive enhancement: pages must remain usable with JS disabled.
 */
(function () {
  "use strict";

  /* ---------------------------------------------------------------------
   * Server-file picker: pairs a local <input type="file"> with a
   * "Browse server" button that lists container-storage files in a shared
   * modal. Selecting a server file fills the hidden <name>_server input and
   * disables the local input (mutually exclusive sources).
   * ------------------------------------------------------------------- */
  var activePicker = null;

  function pickerModal() {
    return document.getElementById("filePickerModal");
  }

  function clearServerChoice(picker) {
    var hidden = picker.querySelector("input[type=hidden]");
    var localInput = picker.querySelector("input[type=file]");
    var chosen = picker.querySelector("[data-fp-chosen]");
    hidden.value = "";
    if (localInput) localInput.disabled = false;
    if (chosen) chosen.classList.add("d-none");
  }

  function selectServerFile(picker, name) {
    var hidden = picker.querySelector("input[type=hidden]");
    var localInput = picker.querySelector("input[type=file]");
    var chosen = picker.querySelector("[data-fp-chosen]");
    hidden.value = name;
    if (localInput) {
      localInput.value = "";
      localInput.disabled = true;
    }
    if (chosen) {
      chosen.querySelector("[data-fp-chosen-name]").textContent = name;
      chosen.classList.remove("d-none");
    }
    localInput && localInput.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function renderFileList(listEl, dir, files) {
    listEl.textContent = "";
    if (!files.length) {
      var empty = document.createElement("div");
      empty.className = "list-group-item text-muted";
      empty.textContent = "No files in server storage.";
      listEl.appendChild(empty);
      return;
    }
    files.forEach(function (file) {
      var item = document.createElement("button");
      item.type = "button";
      item.className =
        "list-group-item list-group-item-action d-flex justify-content-between align-items-center";
      var label = document.createElement("span");
      label.textContent = file.name;
      var meta = document.createElement("small");
      meta.className = "text-muted";
      meta.textContent = (file.size || 0) + " bytes · " + (file.modified || "");
      item.appendChild(label);
      item.appendChild(meta);
      item.addEventListener("click", function () {
        if (activePicker) selectServerFile(activePicker, file.name);
        bootstrap.Modal.getInstance(pickerModal()).hide();
      });
      listEl.appendChild(item);
    });
  }

  function openPickerModal(picker) {
    var modalEl = pickerModal();
    if (!modalEl) return;
    activePicker = picker;
    var listEl = modalEl.querySelector("[data-fp-list]");
    listEl.textContent = "";
    var loading = document.createElement("div");
    loading.className = "list-group-item text-muted";
    loading.textContent = "Loading…";
    listEl.appendChild(loading);
    new bootstrap.Modal(modalEl).show();

    var dir = picker.dataset.dir || "csv";
    fetch("/api/files?dir=" + encodeURIComponent(dir))
      .then(function (resp) { return resp.json(); })
      .then(function (data) { renderFileList(listEl, dir, data.files || []); })
      .catch(function () {
        listEl.textContent = "";
        var err = document.createElement("div");
        err.className = "list-group-item text-danger";
        err.textContent = "Could not load the server file list.";
        listEl.appendChild(err);
      });
  }

  function initFilePickers() {
    document.querySelectorAll(".file-picker").forEach(function (picker) {
      var browse = picker.querySelector("[data-fp-browse]");
      if (browse) {
        browse.addEventListener("click", function () { openPickerModal(picker); });
      }
      var clear = picker.querySelector("[data-fp-clear]");
      if (clear) {
        clear.addEventListener("click", function (e) {
          e.preventDefault();
          clearServerChoice(picker);
        });
      }
      var localInput = picker.querySelector("input[type=file]");
      if (localInput) {
        localInput.addEventListener("change", function () {
          if (localInput.files && localInput.files.length) clearServerChoice(picker);
        });
      }
    });
  }

  /* ---------------------------------------------------------------------
   * Contact popovers: person names carry data-contact attributes; clicking
   * shows institution / mailto / tel. Without JS the link is a plain mailto.
   * ------------------------------------------------------------------- */
  function contactContent(el) {
    var wrap = document.createElement("div");
    var institution = el.dataset.institution;
    var email = el.dataset.email;
    var phone = el.dataset.phone;
    if (institution) {
      var inst = document.createElement("div");
      inst.textContent = institution;
      wrap.appendChild(inst);
    }
    if (email) {
      var mail = document.createElement("div");
      var mailLink = document.createElement("a");
      mailLink.href = "mailto:" + email;
      mailLink.textContent = email;
      mail.appendChild(mailLink);
      wrap.appendChild(mail);
    }
    if (phone) {
      var tel = document.createElement("div");
      var telLink = document.createElement("a");
      telLink.href = "tel:" + phone.replace(/[^+0-9]/g, "");
      telLink.textContent = phone;
      tel.appendChild(telLink);
      wrap.appendChild(tel);
    }
    if (!wrap.childNodes.length) {
      var none = document.createElement("div");
      none.className = "text-muted";
      none.textContent = "No contact information on file.";
      wrap.appendChild(none);
    }
    return wrap;
  }

  function initContactPopovers() {
    document.querySelectorAll("[data-contact]").forEach(function (el) {
      var popover = new bootstrap.Popover(el, {
        trigger: "click",
        html: true,
        container: "body",
        title: el.dataset.name || el.textContent,
        content: function () { return contactContent(el); },
      });
      el.addEventListener("click", function (e) { e.preventDefault(); });
    });
    document.addEventListener("click", function (e) {
      document.querySelectorAll("[data-contact]").forEach(function (el) {
        if (el === e.target || el.contains(e.target)) return;
        var instance = bootstrap.Popover.getInstance(el);
        if (instance) {
          var tip = document.querySelector(".popover.show");
          if (!tip || !tip.contains(e.target)) instance.hide();
        }
      });
    });
  }

  /* ---------------------------------------------------------------------
   * Dynamic table rows (Shift Setup page): clone a <template> row on Add,
   * remove on demand but never below one row.
   * ------------------------------------------------------------------- */
  function initDynamicRows() {
    document.querySelectorAll("[data-dynamic-rows]").forEach(function (container) {
      var templateEl = document.getElementById(container.dataset.template);
      var body = container.querySelector("tbody");
      var addButton = document.querySelector(
        "[data-add-row='" + container.dataset.dynamicRows + "']"
      );
      var counter = document.querySelector(
        "[data-row-count='" + container.dataset.dynamicRows + "']"
      );

      function updateCount() {
        if (counter) counter.textContent = body.querySelectorAll("tr").length;
      }

      function wireRemove(row) {
        var remove = row.querySelector("[data-remove-row]");
        if (remove) {
          remove.addEventListener("click", function () {
            if (body.querySelectorAll("tr").length > 1) {
              row.remove();
              updateCount();
            }
          });
        }
      }

      body.querySelectorAll("tr").forEach(wireRemove);
      if (addButton && templateEl) {
        addButton.addEventListener("click", function () {
          var fragment = templateEl.content.cloneNode(true);
          body.appendChild(fragment);
          wireRemove(body.lastElementChild);
          updateCount();
        });
      }
      updateCount();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initFilePickers();
    initContactPopovers();
    initDynamicRows();
  });
})();
