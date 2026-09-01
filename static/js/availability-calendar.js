(() => {
  const calendar = document.querySelector("[data-availability-calendar]");
  if (!calendar) return;

  const selectableCells = [...calendar.querySelectorAll("[data-selectable-date]")];
  const cells = [...calendar.querySelectorAll("[data-calendar-cell]")];
  const quickForm = calendar.querySelector("[data-availability-selection]");
  const selectionLabel = calendar.querySelector("[data-selection-label]");
  const clearButton = calendar.querySelector("[data-clear-selection]");
  const startsInput = calendar.querySelector("[data-starts-at]");
  const endsInput = calendar.querySelector("[data-ends-at]");
  const allDayInput = quickForm?.querySelector('input[name="all_day"]');
  const startTimeInput = calendar.querySelector("[data-start-time]");
  const endTimeInput = calendar.querySelector("[data-end-time]");
  const saveButton = calendar.querySelector("[data-save-availability]");
  const timeFields = [...calendar.querySelectorAll("[data-time-field]")];

  if (!quickForm || !selectionLabel || !clearButton || !startsInput || !endsInput) return;

  let startDate = startsInput.value ? startsInput.value.slice(0, 10) : null;
  let endDate = endsInput.value ? endsInput.value.slice(0, 10) : startDate;

  const parseLocalDate = (isoDate) => new Date(`${isoDate}T12:00:00`);
  const toIsoDate = (date) => {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  };
  const shiftDate = (isoDate, amount) => {
    const date = parseLocalDate(isoDate);
    date.setDate(date.getDate() + amount);
    return toIsoDate(date);
  };
  const readableDate = (isoDate) => parseLocalDate(isoDate).toLocaleDateString(
    undefined,
    { month: "short", day: "numeric", year: "numeric" },
  );

  if (startDate && endDate && allDayInput?.checked) endDate = shiftDate(endDate, -1);
  if (startTimeInput && startsInput.value.includes("T")) startTimeInput.value = startsInput.value.slice(11, 16);
  if (endTimeInput && endsInput.value.includes("T")) endTimeInput.value = endsInput.value.slice(11, 16);

  const updateDateTimeValues = () => {
    if (!startDate || !endDate) {
      startsInput.value = "";
      endsInput.value = "";
      return;
    }
    const startsValue = allDayInput?.checked
      ? `${startDate}T00:00`
      : `${startDate}T${startTimeInput?.value || "08:00"}`;
    const endsValue = allDayInput?.checked
      ? `${shiftDate(endDate, 1)}T00:00`
      : `${endDate}T${endTimeInput?.value || "17:00"}`;
    startsInput.value = startsValue;
    endsInput.value = endsValue;
    startsInput.setAttribute("value", startsValue);
    endsInput.setAttribute("value", endsValue);
  };

  const paintSelection = () => {
    cells.forEach((cell) => {
      const date = cell.dataset.calendarCell;
      cell.classList.toggle(
        "selected-range",
        Boolean(startDate && endDate && date >= startDate && date <= endDate),
      );
      cell.classList.toggle("range-start", date === startDate);
      cell.classList.toggle("range-end", date === endDate);
    });
    if (!startDate || !endDate) {
      quickForm.classList.add("empty-selection");
      selectionLabel.textContent = "Select one or more dates on the calendar below";
      clearButton.hidden = true;
      if (saveButton) saveButton.disabled = true;
      updateDateTimeValues();
      return;
    }
    quickForm.classList.remove("empty-selection");
    clearButton.hidden = false;
    if (saveButton) saveButton.disabled = false;
    selectionLabel.textContent = startDate === endDate
      ? readableDate(startDate)
      : `${readableDate(startDate)} – ${readableDate(endDate)}`;
    updateDateTimeValues();
  };

  const updateTimeVisibility = () => {
    timeFields.forEach((field) => { field.hidden = Boolean(allDayInput?.checked); });
    updateDateTimeValues();
  };

  selectableCells.forEach((cell) => {
    cell.addEventListener("click", (event) => {
      if (event.target.closest(".calendar-entry")) return;
      const selectedDate = cell.dataset.selectableDate;
      if (!startDate || startDate !== endDate) {
        startDate = selectedDate;
        endDate = selectedDate;
      } else if (selectedDate < startDate) {
        endDate = startDate;
        startDate = selectedDate;
      } else {
        endDate = selectedDate;
      }
      paintSelection();
    });
  });

  clearButton.addEventListener("click", () => {
    startDate = null;
    endDate = null;
    paintSelection();
  });
  allDayInput?.addEventListener("change", updateTimeVisibility);
  startTimeInput?.addEventListener("change", updateDateTimeValues);
  endTimeInput?.addEventListener("change", updateDateTimeValues);
  quickForm.addEventListener("submit", updateDateTimeValues);

  updateTimeVisibility();
  paintSelection();
})();
