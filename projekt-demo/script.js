// Save checkbox state to localStorage
const checkboxes = document.querySelectorAll('.check-input');

// Load saved state
checkboxes.forEach((checkbox, index) => {
  const saved = localStorage.getItem(`projekt-demo-step-${index}`);
  if (saved === 'true') {
    checkbox.checked = true;
  }
});

// Save on change
checkboxes.forEach((checkbox, index) => {
  checkbox.addEventListener('change', () => {
    localStorage.setItem(`projekt-demo-step-${index}`, checkbox.checked);
  });
});
