function filterShows() {
    const input = document.getElementById('searchInput').value.toLowerCase().trim();
    document.querySelectorAll('.show-item').forEach(item => {
        const name = item.dataset.showName || '';
        item.style.display = name.includes(input) ? '' : 'none';
    });
}
