// A native source trigger, with its expandable detail outside the button.
(function () {
  'use strict';
  window.slmSourceRow = function (id, label) {
    var row = document.createElement('div');
    row.className = 'fact-result-item'; row.dataset.factId = id;
    var button = document.createElement('button');
    button.type = 'button'; button.className = 'btn sm ghost'; button.textContent = label;
    button.addEventListener('click', function (e) {
      // Open the real memory in the Memories tab when that pane is loaded;
      // otherwise the fact popup (fact-detail delegates on the row).
      if (typeof window.odOpenMemory === 'function') {
        e.stopPropagation();
        window.odOpenMemory(id);
        return;
      }
      row.click();
    });
    row.appendChild(button);
    return row;
  };
}());
