// The "Browse available hacks" page: loads the weekly report's available.json and
// filters it in the browser. No dependencies; every value is written with textContent.
(function () {
  "use strict";

  var PAGE_SIZE = 200;
  var LANGUAGES = { en: "English", es: "Spanish" };

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      node.setAttribute(key, attrs[key]);
    });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function field(labelText, control) {
    var label = el("label", {}, labelText);
    label.appendChild(control);
    return label;
  }

  function option(value, text) {
    return el("option", { value: value }, text);
  }

  function init(root) {
    if (root.dataset.ready) return;
    root.dataset.ready = "1";

    var status = el("p", { class: "hb-status", role: "status" }, "Loading this week's report…");
    root.appendChild(status);

    fetch(root.dataset.source, { cache: "no-cache" })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (data) {
        build(root, status, data);
      })
      .catch(function () {
        status.textContent =
          "The report could not be loaded. It is published after the first weekly run; " +
          "the link below has the latest one.";
      });
  }

  function build(root, status, data) {
    var lists = { available: data.available || [], dropping: data.dropping || [] };
    var shown = PAGE_SIZE;

    var list = el("select", { "aria-label": "List" });
    list.appendChild(option("available", "Available (" + lists.available.length + ")"));
    list.appendChild(option("dropping", "Dropping soon (" + lists.dropping.length + ")"));

    var search = el("input", { type: "search", placeholder: "word or domain" });

    var tld = el("select", {});
    var language = el("select", {});
    language.appendChild(option("", "Any"));
    Object.keys(LANGUAGES).forEach(function (code) {
      language.appendChild(option(code, LANGUAGES[code]));
    });

    var maxLength = el("input", { type: "number", min: "3", max: "63", placeholder: "any" });

    var controls = el("div", { class: "hb-controls" });
    controls.appendChild(field("Show", list));
    controls.appendChild(field("Search", search));
    controls.appendChild(field("TLD", tld));
    controls.appendChild(field("Language", language));
    controls.appendChild(field("Max. letters", maxLength));

    var wrap = el("div", { class: "hb-table-wrap" });
    var table = el("table", {});
    var head = el("thead", {});
    var body = el("tbody", {});
    table.appendChild(head);
    table.appendChild(body);
    wrap.appendChild(table);

    var more = el("button", { class: "md-button hb-more", type: "button" }, "Show more");

    root.insertBefore(controls, status);
    root.appendChild(wrap);
    root.appendChild(more);

    function fillTlds() {
      var counts = {};
      lists[list.value].forEach(function (item) {
        counts[item.tld] = (counts[item.tld] || 0) + 1;
      });
      var previous = tld.value;
      tld.textContent = "";
      tld.appendChild(option("", "Any"));
      Object.keys(counts)
        .sort(function (a, b) {
          return counts[b] - counts[a] || a.localeCompare(b);
        })
        .forEach(function (suffix) {
          tld.appendChild(option(suffix, "." + suffix + " (" + counts[suffix] + ")"));
        });
      tld.value = counts[previous] ? previous : "";
    }

    function matches(item) {
      var text = search.value.trim().toLowerCase();
      if (text && item.word.indexOf(text) === -1 && item.domain.indexOf(text) === -1) return false;
      if (tld.value && item.tld !== tld.value) return false;
      if (language.value && (item.langs || []).indexOf(language.value) === -1) return false;
      var limit = parseInt(maxLength.value, 10);
      if (limit && item.word.length > limit) return false;
      return true;
    }

    function render() {
      var dropping = list.value === "dropping";
      var columns = dropping
        ? ["Domain", "Word", "Language", "Status", "Expires"]
        : ["Domain", "Word", "Language"];
      head.textContent = "";
      var row = el("tr", {});
      columns.forEach(function (name) {
        row.appendChild(el("th", { scope: "col" }, name));
      });
      head.appendChild(row);

      var found = lists[list.value].filter(matches);
      body.textContent = "";
      found.slice(0, shown).forEach(function (item) {
        var line = el("tr", {});
        line.appendChild(el("td", {}, item.domain));
        line.appendChild(el("td", {}, item.word));
        var names = (item.langs || []).map(function (code) {
          return LANGUAGES[code] || code;
        });
        line.appendChild(el("td", {}, names.join(", ") || "-"));
        if (dropping) {
          line.appendChild(el("td", {}, item.status || "-"));
          line.appendChild(el("td", {}, item.expires || "-"));
        }
        body.appendChild(line);
      });

      var visible = Math.min(shown, found.length);
      status.textContent =
        "Checked on " + data.date + ". " +
        (found.length
          ? "Showing " + visible + " of " + found.length + " " + (dropping ? "dropping" : "available") + " hacks."
          : "Nothing matches these filters.");
      wrap.hidden = found.length === 0;
      more.hidden = found.length <= shown;
    }

    function reset() {
      shown = PAGE_SIZE;
      render();
    }

    list.addEventListener("change", function () {
      fillTlds();
      reset();
    });
    [search, maxLength].forEach(function (control) {
      control.addEventListener("input", reset);
    });
    [tld, language].forEach(function (control) {
      control.addEventListener("change", reset);
    });
    more.addEventListener("click", function () {
      shown += PAGE_SIZE;
      render();
    });

    fillTlds();
    render();
  }

  function start() {
    var root = document.getElementById("hack-browser");
    if (root) init(root);
  }

  // Material for MkDocs swaps pages without reloading (navigation.instant).
  if (window.document$ && window.document$.subscribe) {
    window.document$.subscribe(start);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
