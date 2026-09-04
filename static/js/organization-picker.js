(function () {
  function enhance(select) {
    if (select.dataset.organizationPickerReady === "true") return;
    select.dataset.organizationPickerReady = "true";

    var options = Array.from(select.options).filter(function (option) { return option.value; });
    if (options.length < 20) return;

    var controls = document.createElement("div");
    controls.className = "organization-picker-controls";
    var search = document.createElement("input");
    search.type = "search";
    search.className = "organization-picker-search";
    search.placeholder = "Search organization or FDID";
    search.setAttribute("aria-label", "Search organizations");
    var county = document.createElement("select");
    county.className = "organization-picker-county";
    county.setAttribute("aria-label", "Filter organizations by county");
    county.innerHTML = '<option value="">All counties</option>';
    Array.from(new Set(options.map(function (option) { return option.dataset.county || ""; }).filter(Boolean)))
      .sort(function (a, b) { return a.localeCompare(b); })
      .forEach(function (name) {
        var choice = document.createElement("option");
        choice.value = name;
        choice.textContent = name + " County";
        county.appendChild(choice);
      });
    var count = document.createElement("small");
    count.className = "organization-picker-count";

    controls.appendChild(search);
    controls.appendChild(county);
    select.parentNode.insertBefore(controls, select);
    select.parentNode.insertBefore(count, select.nextSibling);

    function filter() {
      var query = search.value.trim().toLowerCase();
      var selectedCounty = county.value;
      var visible = 0;
      options.forEach(function (option) {
        var searchText = (option.textContent + " " + (option.dataset.search || "")).toLowerCase();
        var matchesText = !query || searchText.includes(query);
        var matchesCounty = !selectedCounty || option.dataset.county === selectedCounty;
        var keepSelected = option.selected;
        option.hidden = !(matchesText && matchesCounty) && !keepSelected;
        if (!option.hidden) visible += 1;
      });
      count.textContent = visible + " organization" + (visible === 1 ? "" : "s") + " shown";
    }

    search.addEventListener("input", filter);
    county.addEventListener("change", filter);
    filter();
  }

  function initialize() {
    document.querySelectorAll("select[data-organization-picker]").forEach(enhance);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
  else initialize();
})();
