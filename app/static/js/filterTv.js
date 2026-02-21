function toggleShow(headerEl) {
    const seasons = headerEl.nextElementSibling;
    const chevron = headerEl.querySelector('i');
    seasons.classList.toggle('hidden');
    chevron.classList.toggle('fa-chevron-right');
    chevron.classList.toggle('fa-chevron-down');
}

function toggleSeason(headerEl) {
    const episodes = headerEl.nextElementSibling;
    const chevron = headerEl.querySelector('i');
    episodes.classList.toggle('hidden');
    chevron.classList.toggle('fa-chevron-right');
    chevron.classList.toggle('fa-chevron-down');
}

function filterShows() {
    const input = document.getElementById('searchInput').value.toLowerCase().trim();
    const showItems = document.querySelectorAll('.show-item');

    if (!input) {
        // No search: collapse everything back to default
        showItems.forEach(show => {
            show.style.display = '';
            const seasons = show.querySelector('.seasons');
            seasons.classList.add('hidden');
            seasons.querySelectorAll('.episodes').forEach(ep => ep.classList.add('hidden'));
            show.querySelectorAll('i').forEach(i => {
                i.classList.remove('fa-chevron-down');
                i.classList.add('fa-chevron-right');
            });
            show.querySelectorAll('.episode-item').forEach(ep => ep.style.display = '');
            show.querySelectorAll('.season-item').forEach(s => s.style.display = '');
        });
        return;
    }

    showItems.forEach(show => {
        let showHasMatch = false;
        const seasonItems = show.querySelectorAll('.season-item');

        seasonItems.forEach(season => {
            let seasonHasMatch = false;
            const episodeItems = season.querySelectorAll('.episode-item');

            episodeItems.forEach(ep => {
                const name = ep.dataset.episodeName || '';
                const matches = name.includes(input);
                ep.style.display = matches ? '' : 'none';
                if (matches) {
                    seasonHasMatch = true;
                    showHasMatch = true;
                }
            });

            if (seasonHasMatch) {
                season.style.display = '';
                const epList = season.querySelector('.episodes');
                epList.classList.remove('hidden');
                const chevron = season.querySelector('.season-header i');
                chevron.classList.remove('fa-chevron-right');
                chevron.classList.add('fa-chevron-down');
            } else {
                season.style.display = 'none';
            }
        });

        if (showHasMatch) {
            show.style.display = '';
            const seasonList = show.querySelector('.seasons');
            seasonList.classList.remove('hidden');
            const chevron = show.querySelector('.show-header i');
            chevron.classList.remove('fa-chevron-right');
            chevron.classList.add('fa-chevron-down');
        } else {
            show.style.display = 'none';
        }
    });
}
