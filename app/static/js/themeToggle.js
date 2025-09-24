function toggleTheme() {
    const html = document.documentElement;
    if (html.classList.contains('dark')) {
        html.classList.remove('dark');
        localStorage.setItem('theme', 'light');
    } else {
        html.classList.add('dark');
        localStorage.setItem('theme', 'dark');
    }
}

window.addEventListener('load', () => {
    const savedTheme = localStorage.getItem('theme');
    const lightSwitch = document.getElementById('lightSwitch');
    if (savedTheme === 'dark' || (!savedTheme && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
        lightSwitch.checked = true;
        document.documentElement.classList.add('dark');
    } else {
        lightSwitch.checked = false;
        document.documentElement.classList.remove('dark');
    }
});