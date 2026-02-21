function toggleTheme() {
    const html = document.documentElement;
    const themeMeta = document.getElementById('theme-color');
    const lightSwitch = document.getElementById('lightSwitch');
    
    requestAnimationFrame(() => {
        if (lightSwitch.checked) {
            html.classList.add('dark');
            localStorage.setItem('theme', 'dark');
            if (themeMeta) {
                themeMeta.setAttribute('content', '#111827'); // dark:bg-gray-900
            }
        } else {
            html.classList.remove('dark');
            localStorage.setItem('theme', 'light');
            if (themeMeta) {
                themeMeta.setAttribute('content', '#f3f4f6'); // bg-gray-100
            }
        }
    });
}

window.addEventListener('load', () => {
    const savedTheme = localStorage.getItem('theme');
    const lightSwitch = document.getElementById('lightSwitch');
    const themeMeta = document.getElementById('theme-color');
    
    requestAnimationFrame(() => {
        if (savedTheme === 'dark' || (!savedTheme && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
            lightSwitch.checked = true;
            document.documentElement.classList.add('dark');
            if (themeMeta) {
                themeMeta.setAttribute('content', '#111827'); // dark:bg-gray-900
            }
        } else {
            lightSwitch.checked = false;
            document.documentElement.classList.remove('dark');
            if (themeMeta) {
                themeMeta.setAttribute('content', '#f3f4f6'); // bg-gray-100
            }
        }
    });
});