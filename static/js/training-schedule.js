(() => {
  const parseLocalDateTime = (value) => {
    const match = value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
    if (!match) return null;

    const [, year, month, day, hour, minute] = match.map(Number);
    const date = new Date(year, month - 1, day, hour, minute);
    return Number.isNaN(date.getTime()) ? null : date;
  };

  const formatLocalDateTime = (date) => {
    const pad = (value) => String(value).padStart(2, "0");
    return [
      date.getFullYear(),
      "-",
      pad(date.getMonth() + 1),
      "-",
      pad(date.getDate()),
      "T",
      pad(date.getHours()),
      ":",
      pad(date.getMinutes()),
    ].join("");
  };

  const suggestedEndFor = (startValue) => {
    const start = parseLocalDateTime(startValue);
    if (!start) return "";
    start.setHours(start.getHours() + 3);
    return formatLocalDateTime(start);
  };

  document.querySelectorAll(".unit-session-form").forEach((session) => {
    const startInput = session.querySelector("[data-unit-start]");
    const endInput = session.querySelector("[data-unit-end]");
    if (!startInput || !endInput) return;

    let suggestedEnd = suggestedEndFor(startInput.value);
    let endIsAutomatic = !endInput.value || endInput.value === suggestedEnd;

    const updateSuggestedEnd = () => {
      const nextSuggestion = suggestedEndFor(startInput.value);
      if (endIsAutomatic || !endInput.value) {
        endInput.value = nextSuggestion;
        endIsAutomatic = true;
      }
      suggestedEnd = nextSuggestion;
    };

    endInput.addEventListener("input", () => {
      endIsAutomatic = endInput.value === suggestedEnd;
    });
    startInput.addEventListener("input", updateSuggestedEnd);
    startInput.addEventListener("change", updateSuggestedEnd);
  });
})();
