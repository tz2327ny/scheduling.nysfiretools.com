(() => {
  const calendar = document.querySelector("[data-availability-calendar]");
  if (!calendar) return;

  const dayButtons = [...calendar.querySelectorAll("[data-calendar-date]:not([disabled])")];
  const cells = [...calendar.querySelectorAll("[data-calendar-cell]")];
  const selection = calendar.querySelector("[data-availability-selection]");
  const selectionLabel = calendar.querySelector("[data-selection-label]");
  const addLink = calendar.querySelector("[data-add-link]");
  const clearButton = calendar.querySelector("[data-clear-selection]");
  if (!selection || !selectionLabel || !addLink || !clearButton) return;

  let startDate = null;
  let endDate = null;

  const readableDate = (isoDate) => new Date(`${isoDate}T12:00:00`).toLocaleDateString(
    undefined,
    { month: "short", day: "numeric", year: "numeric" },
  );

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
      selection.hidden = true;
      return;
    }
    selection.hidden = false;
    selectionLabel.textContent = startDate === endDate
      ? readableDate(startDate)
      : `${readableDate(startDate)} – ${readableDate(endDate)}`;
    const baseUrl = addLink.dataset.baseUrl;
    addLink.href = `${baseUrl}?start=${encodeURIComponent(startDate)}&end=${encodeURIComponent(endDate)}`;
  };

  dayButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const selectedDate = button.dataset.calendarDate;
      if (!startDate || (startDate !== endDate)) {
        startDate = selectedDate;
        endDate = selectedDate;
      } else {
        startDate = startDate < selectedDate ? startDate : selectedDate;
        endDate = endDate > selectedDate ? endDate : selectedDate;
      }
      paintSelection();
    });
  });

  clearButton.addEventListener("click", () => {
    startDate = null;
    endDate = null;
    paintSelection();
  });
})();
