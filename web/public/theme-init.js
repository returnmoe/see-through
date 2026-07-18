(function initializeTheme() {
  var theme = 'dark';

  try {
    var storedTheme = window.localStorage.getItem('see-through-theme');
    if (storedTheme === 'dark' || storedTheme === 'light') {
      theme = storedTheme;
    } else if (window.matchMedia('(prefers-color-scheme: light)').matches) {
      theme = 'light';
    }
  } catch (error) {
    if (window.matchMedia('(prefers-color-scheme: light)').matches) {
      theme = 'light';
    }
  }

  document.documentElement.dataset.theme = theme;
})();
